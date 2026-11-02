"""Decision interpreter: change-set schema, quote verification, live LLM call.

`interpret()` generates the change set with Muse Spark and quote-verifies
every item against the guide text; paraphrases are dropped automatically.
Key-free paths (stubs + verification) keep working without MODEL_API_KEY.
"""

import json
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from chainreaction.graph.repo import package_dir, relative_posix
from chainreaction.llm import chat, extract_json

CHANGE_SET_JSON_SCHEMA = {
    "name": "change_set",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string"},
            "source_url": {"type": "string"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": ["removed", "renamed", "changed",
                                     "deprecated", "missing_param",
                                     "missing_key", "rejected_value"],
                        },
                        "quote": {"type": "string"},
                        "replacement": {"type": ["string", "null"]},
                        "detail": {
                            "type": ["object", "null"],
                            "properties": {
                                "expected": {"type": ["string", "null"]},
                                "actual": {"type": ["string", "null"]},
                                "note": {"type": ["string", "null"]},
                            },
                            "required": ["expected", "actual", "note"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["symbol", "kind", "quote", "replacement",
                                 "detail"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["decision", "source_url", "items"],
        "additionalProperties": False,
    },
}

INTERPRET_SYSTEM_PROMPT = """\
You are the ChainReaction decision interpreter. Given a dependency decision
(e.g. "upgrade numpy to 2.0"), find the OFFICIAL migration guide or changelog
for that decision using web search, then emit the change set as JSON.

Rules:
- Every item needs a `quote`: an EXACT substring of the fetched guide text
  proving the symbol changed. Quotes are string-matched; paraphrases are
  dropped automatically, so copy precisely.
- `symbol` is the fully qualified name (e.g. "numpy.sctypes").
- Kinds (classify carefully — only `removed` and `renamed` count as
  breakage): `removed` for members that no longer exist; `renamed` for
  members that moved to a new name (old uses break; `replacement` is
  the new name); `deprecated` for members that still work but warn
  (warnings, not breakage); `changed` for behavior changes in working
  members (flags, not breakage).
  `missing_param` covers a config item the decision needs but our code never
  supplies (function parameter, option, env var). `missing_key` covers a
  missing table/mapping entry (pricing table, registry). `rejected_value`
  covers our own schema/validator rejecting the decision's value (Literal,
  enum, regex). Record the machine-checkable gap in `detail.expected` vs
  `detail.actual` (e.g. expected `"meta" in Provider`, actual
  `Literal["anthropic","openai","ollama"]`).
- Prefer the vendor's own docs (numpy.org, changelog, migration guide) over
  third-party writeups. Record the guide URL in `source_url`.
- When in doubt, include the item with its quote; the verifier will confirm.
- Output JSON only, matching the change_set schema.
"""

VENDOR_CHANGE_SET_JSON_SCHEMA = {
    "name": "vendor_change_set",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string"},
            "source_url": {"type": "string"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": ["removed", "renamed", "changed",
                                     "deprecated", "missing_param",
                                     "missing_key", "rejected_value"],
                        },
                        "quote": {"type": "string"},
                        "replacement": {"type": ["string", "null"]},
                        "code_quote": {"type": "string"},
                        "code_file": {"type": "string"},
                        "code_line": {"type": "integer"},
                        "detail": {
                            "type": ["object", "null"],
                            "properties": {
                                "expected": {"type": ["string", "null"]},
                                "actual": {"type": ["string", "null"]},
                                "note": {"type": ["string", "null"]},
                            },
                            "required": ["expected", "actual", "note"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["symbol", "kind", "quote", "replacement",
                                 "code_quote", "code_file", "code_line",
                                 "detail"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["decision", "source_url", "items"],
        "additionalProperties": False,
    },
}

VENDOR_SYSTEM_PROMPT = """\
You are the ChainReaction vendor-decision interpreter. Given a vendor
switch (e.g. "switch to Meta Model API"), the vendor guide text, and
the repo's own source, emit the change set as JSON.

Rules:
- Config breaks live in OUR code, not the vendor docs: a provider name
  our schema rejects, a base_url our client never passes, a pricing
  entry our table lacks. Read the source to find them.
- Every config item (`missing_param`, `missing_key`, `rejected_value`)
  needs a `code_quote`: an EXACT span from the repo source proving the
  gap, plus its `code_file` (repo-relative path as shown) and
  `code_line` (the span's starting line number). Code quotes are
  string-matched against the file; paraphrases are dropped, so copy
  precisely without the line-number prefixes.
- `symbol` must name OUR definition (e.g.
  "sponsorfit.llm.MeteredLLMClient._call_openai"), never an SDK class
  or env var — out-of-package config symbols are dropped.
- `quote` is an EXACT substring of the guide text when the guide
  states the need (base_url, model name); otherwise "" — the code
  quote is the grounding for config items.
- Other kinds (`removed`, `renamed`, `changed`, `deprecated`) work as
  usual: `quote` must string-match the guide; leave code fields ""/0.
- Record the machine-checkable gap in `detail.expected` vs
  `detail.actual` (e.g. expected `"meta" in Provider`, actual
  `Literal["anthropic","openai","ollama"]`).
- Output JSON only, matching the vendor_change_set schema.
"""


@dataclass
class BreakDetail:
    expected: str | None  # what the decision needs
    actual: str | None  # what the code has
    note: str | None = None


@dataclass
class ChangeItem:
    symbol: str
    # removed|changed|deprecated (dependency API) or
    # missing_param|missing_key|rejected_value (our code vs the decision)
    kind: str
    quote: str
    replacement: str | None = None
    detail: BreakDetail | None = None
    verified: bool = False
    # Grounding: "guide" (quote must string-match the guide), "code"
    # (code_quote must string-match the repo file at code_file:line),
    # or "probe" (a break the verifier discovered, e.g. a KeyError —
    # quoted by no document).
    source: str = "guide"
    # Code grounding for vendor decisions (source == "code"): exact
    # span from the repo plus its repo-relative file:line.
    code_quote: str = ""
    code_file: str = ""
    code_line: int = 0


@dataclass
class ChangeSet:
    decision: str
    source_url: str
    items: list[ChangeItem] = field(default_factory=list)


def verify_quotes(
    items: list[ChangeItem], source_text: str
) -> tuple[list[ChangeItem], list[ChangeItem]]:
    """Keep grounded items; return (verified, dropped).

    Guide items survive only when the quote string-matches. Probe items
    carry no quote (no document states them) and survive as verified —
    their grounding is the probe run, labeled at render time. Code
    items are never verified here (they need their repo files);
    verify_code_quotes owns them and they drop when they reach this
    function by mistake.
    """
    verified, dropped = [], []
    for item in items:
        if item.source == "probe":
            item.verified = True
        elif item.source == "code":
            item.verified = False
        else:
            item.verified = bool(item.quote) and item.quote in source_text
        (verified if item.verified else dropped).append(item)
    return verified, dropped


# Config/schema kinds name OUR definitions (closed world, resolved to
# graph nodes); every other kind names an external symbol to match uses.
CONFIG_KINDS = ("missing_param", "missing_key", "rejected_value")

# Kinds that seed breakage: uses of removed/renamed members fail.
# `deprecated` members still work (warnings) and `changed` members only
# behave differently (flags); neither counts as breakage.
SEED_KINDS = ("removed", "renamed")


def change_symbols(changeset: ChangeSet, verified_only: bool = True) -> set[str]:
    """Breakage seeds: removed/renamed symbols (verified ones by default)."""
    return {
        i.symbol
        for i in changeset.items
        if (not verified_only or i.verified) and i.kind in SEED_KINDS
    }


def deprecated_symbols(changeset: ChangeSet,
                       verified_only: bool = True) -> set[str]:
    """Deprecated symbols: still work, reported as warnings."""
    return {
        i.symbol
        for i in changeset.items
        if (not verified_only or i.verified) and i.kind == "deprecated"
    }


def changed_symbols(changeset: ChangeSet,
                    verified_only: bool = True) -> set[str]:
    """Behavior-change flags: working members, excluded from scores."""
    return {
        i.symbol
        for i in changeset.items
        if (not verified_only or i.verified) and i.kind == "changed"
    }


def config_items(changeset: ChangeSet, verified_only: bool = True
                 ) -> list[ChangeItem]:
    return [
        i for i in changeset.items
        if (not verified_only or i.verified) and i.kind in CONFIG_KINDS
    ]


def drop_out_of_package_items(changeset: ChangeSet, package: str
                              ) -> tuple[ChangeSet, list[str]]:
    """Drop config items naming symbols outside our package.

    Model-boundary sanitizer: config kinds resolve to graph nodes
    (closed world), so a config symbol outside the package — an env
    var, an SDK class, a model ID — can never resolve and would fail
    propagation. Returns (kept ChangeSet, sorted dropped symbols).
    Every other kind names external symbols and is untouched.
    """
    kept, dropped = [], []
    for item in changeset.items:
        if (item.kind in CONFIG_KINDS and item.symbol != package
                and not item.symbol.startswith(package + ".")):
            dropped.append(item.symbol)
        else:
            kept.append(item)
    return (ChangeSet(changeset.decision, changeset.source_url, kept),
            sorted(set(dropped)))


def collect_package_sources(target: str | Path, package: str,
                            max_chars: int = 400_000
                            ) -> tuple[str, dict[str, str]]:
    """Bundle the package's .py sources for the vendor interpreter.

    Returns (prompt_text, files) where prompt_text lists every file as
    "=== FILE: <target-relative path> ===" plus line-numbered content,
    and files maps that path to the raw text for quote verification.
    SponsorFit is ~1,900 lines and fits easily; larger repos truncate
    with a marker (the model still grounds what it sees).
    """
    root = Path(target)
    pkgdir = package_dir(root, package)
    files: dict[str, str] = {}
    if pkgdir.is_dir():
        for path in sorted(pkgdir.rglob("*.py")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            files[relative_posix(path, root)] = text
    chunks = [f"Repository source for package `{package}` "
              f"(target {target}). Each file starts with "
              f'"=== FILE: <path> ===" plus line-numbered content. '
              f"Quote code spans EXACTLY as they appear, without the "
              f"line-number prefixes.\n"]
    total = 0
    for rel in sorted(files):
        numbered = "\n".join(
            f"{i}: {line}"
            for i, line in enumerate(files[rel].splitlines(), 1))
        block = f"\n=== FILE: {rel} ===\n{numbered}\n"
        if total + len(block) > max_chars:
            chunks.append("\n[TRUNCATED: remaining files omitted]\n")
            break
        chunks.append(block)
        total += len(block)
    return "".join(chunks), files


def verify_code_quotes(items: list[ChangeItem],
                       files: dict[str, str]
                       ) -> tuple[list[ChangeItem], list[ChangeItem]]:
    """Keep code-grounded items; return (verified, dropped).

    A code item survives only when its file exists, its quote is an
    exact substring of that file, its line is valid, and that line
    points into the quote (the file's stripped line appears in the
    quote or vice versa). Same fail-closed contract as guide quotes:
    paraphrases and drifting lines drop automatically.
    """
    norm_files = {path.replace("\\", "/"): text
                  for path, text in files.items()}
    verified, dropped = [], []
    for item in items:
        if item.source != "code":
            item.verified = False
            dropped.append(item)
            continue
        rel = (item.code_file or "").replace("\\", "/")
        text = norm_files.get(rel)
        if text is None:
            # Accept package-relative spellings ("sponsorfit/llm.py")
            # against target-relative keys ("src/sponsorfit/llm.py").
            hits = [t for p, t in norm_files.items()
                    if p.endswith("/" + rel.lstrip("/"))]
            text = hits[0] if len(hits) == 1 else None
        quote = (item.code_quote or "").strip()
        ok = bool(text is not None and quote and quote in text)
        if ok:
            lines = text.splitlines()
            if not (1 <= item.code_line <= len(lines)):
                ok = False
            else:
                file_line = lines[item.code_line - 1].strip()
                quote_lines = [ln.strip() for ln in quote.splitlines()
                               if ln.strip()]
                ok = bool(file_line) and (
                    file_line in quote
                    or quote in file_line
                    or any(ql and (ql in file_line or file_line in ql)
                           for ql in quote_lines))
        item.verified = ok
        (verified if ok else dropped).append(item)
    return verified, dropped


def verify_all(items: list, guide_text: str, target=None, package: str = ""
               ) -> tuple[list, list]:
    """Verify each item against its own grounding, order preserved.

    Guide items string-match the guide text, code items string-match
    their repo files under target/package (missing target or package
    drops every code item), probe items pass on the probe run. Returns
    (verified, dropped) with verified in the input order.
    """
    guide_items = [i for i in items
                   if getattr(i, "source", "guide") != "code"]
    code_items = [i for i in items
                  if getattr(i, "source", "guide") == "code"]
    verified_guide, _ = verify_quotes(guide_items, guide_text)
    if code_items and target is not None and package:
        _, files = collect_package_sources(target, package)
        verified_code, _ = verify_code_quotes(code_items, files)
    else:
        verified_code = []
        for item in code_items:
            item.verified = False
    ok = {id(i) for i in verified_guide + verified_code}
    return ([i for i in items if id(i) in ok],
            [i for i in items if id(i) not in ok])


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self.skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.chunks.append(data)


def html_to_text(page: str) -> str:
    """Strip tags, unescape entities, collapse whitespace to one line.

    Quotes match against this cleaned text — both the recorded stubs and
    (later) the LLM's exact-substring quotes.
    """
    parser = _TextExtractor()
    parser.feed(page)
    return " ".join(" ".join(parser.chunks).split())


def fetch_page(url: str, timeout: int = 60) -> tuple[int | None, bytes]:
    """Fetch a URL; returns (HTTP status, raw body). Raises on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "ChainReaction/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def decode_page(raw: bytes) -> str:
    """Decode fetched bytes (utf-8, latin-1 fallback)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def page_to_text(raw: bytes) -> str:
    """Decode a fetched page to clean quotable text."""
    return html_to_text(decode_page(raw))


class FetchedText(str):
    """Fetched guide text plus its structured table rows.

    A plain str for every consumer that only needs the text; the run
    store reads .removal_table for deterministic completeness.
    """

    def __new__(cls, text: str, removal_table=None):
        obj = super().__new__(cls, text)
        obj.removal_table = removal_table or {"removed": [],
                                              "deprecated": []}
        return obj


_REMOVED_TABLE_MARKERS = ("members that have been removed:",
                          "replacements for the removed members:")
_DEPRECATED_TABLE_MARKER = "presents deprecated members"

_TABLE_RE = re.compile(r"<table.*?</table>", re.S)
_ROW_RE = re.compile(r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
                     re.S)
_NP_REF_RE = re.compile(r"np\.([A-Za-z_]\w*(?:\.\w+)*)")


def _clean_cell(raw: str) -> str:
    # Same algorithm as the quotable text: chunk-joining inserts spaces
    # where inline tags abut punctuation ("add_docstring ."), so quotes
    # built this way are exact substrings by construction.
    return html_to_text(raw)


def _table_symbol(name: str, method_table: bool) -> str:
    name = re.sub(r"^(np|numpy)\.", "", name.strip())
    if not name:
        return ""
    if method_table and "." not in name:
        return f"numpy.ndarray.{name}"
    return f"numpy.{name}"


def extract_guide_tables(page: str) -> dict:
    """Deterministic rows from the guide's structured member tables.

    Returns {"removed": [...], "deprecated": [...]} with
    {symbol, quote, replacement} rows. Tables are recognized by the
    sentence introducing them (numpy 2.0 migration guide shape: a
    main-namespace removal table, a deprecated table, and an
    ndarray-methods removal table); pages without them yield empty
    lists and the model works prose-only.
    """
    removed, deprecated = [], []
    tables = list(_TABLE_RE.finditer(page))
    for index, match in enumerate(tables):
        prev_end = tables[index - 1].end() if index else 0
        # Markers may wrap across source lines; match collapsed text.
        context = " ".join(page[prev_end:match.start()][-800:].split())
        heads = list(re.finditer(r"<h[1-4][^>]*>(.*?)</h[1-4]>",
                                 page[:match.start()], re.S))
        heading = _clean_cell(heads[-1].group(1)).lower() if heads else ""
        if _DEPRECATED_TABLE_MARKER in context:
            dest, method_table = deprecated, False
        elif any(m in context for m in _REMOVED_TABLE_MARKERS):
            dest, method_table = removed, "ndarray" in heading
        else:
            continue
        for row in _ROW_RE.finditer(match.group(0)):
            name = _clean_cell(row.group(1))
            guidance = _clean_cell(row.group(2))
            if not name:
                continue
            symbol = _table_symbol(name, method_table)
            if not symbol:
                continue
            ref = _NP_REF_RE.search(guidance)
            replacement = (f"numpy.{ref.group(1).rstrip('.')}"
                           if ref else None)
            dest.append({"symbol": symbol,
                         "quote": f"{name} {guidance}".strip(),
                         "replacement": replacement})
    return {"removed": removed, "deprecated": deprecated}


def fetch_text(url: str, timeout: int = 60) -> str:
    """Fetch a migration guide / changelog as clean text (raises on failure)."""
    _, raw = fetch_page(url, timeout)
    return page_to_text(raw)


def to_json(changeset: ChangeSet) -> str:
    return json.dumps(asdict(changeset), indent=2)


def interpret(decision: str, guide_text: str, source_url: str = "",
              chat_fn=None, removal_table=None) -> ChangeSet:
    """Generate the change set with Muse Spark; verify quotes vs the guide.

    Items whose quotes don't string-match are dropped. Raises
    RuntimeError when MODEL_API_KEY is missing. chat_fn defaults to
    llm.chat; the run store injects ctx.llm so calls are recorded.
    removal_table (from extract_guide_tables) lists structured rows for
    the model to confirm; None keeps the prose-only prompt.
    """
    chat_fn = chat_fn or chat
    table_section = ""
    if removal_table:
        rows = ([(r["symbol"], r.get("quote", ""), "removed")
                 for r in removal_table.get("removed", [])]
                + [(r["symbol"], r.get("quote", ""), "deprecated")
                   for r in removal_table.get("deprecated", [])])
        if rows:
            listed = "\n".join(f"- {symbol} ({kind}): {quote[:160]}"
                               for symbol, quote, kind in rows)
            table_section = (
                "\n\nThe guide's structured tables list these members "
                "(confirm each with a quote from the text above; add "
                "anything else you find in the prose):\n\n"
                f"{listed}\n")
    user = (
        f"Decision: {decision}\n\n"
        "Migration guide / changelog text (the ONLY quotable source — "
        "every quote must be an EXACT substring of this text):\n\n"
        f"{guide_text}\n"
        f"{table_section}\n"
        "Emit the change set as a single JSON object matching this schema:\n"
        f"{json.dumps(CHANGE_SET_JSON_SCHEMA['schema'])}"
    )
    data = extract_json(chat_fn(
        INTERPRET_SYSTEM_PROMPT, user, max_tokens=12000,
        response_format={"type": "json_schema",
                         "json_schema": CHANGE_SET_JSON_SCHEMA}))
    items = []
    for raw in data.get("items", []):
        if not raw.get("symbol") or not raw.get("quote") or not raw.get("kind"):
            continue
        detail = raw.get("detail")
        items.append(ChangeItem(
            symbol=raw["symbol"],
            kind=raw["kind"],
            quote=raw["quote"],
            replacement=raw.get("replacement"),
            detail=(BreakDetail(detail.get("expected"), detail.get("actual"),
                                detail.get("note"))
                    if isinstance(detail, dict) else None),
        ))
    verified, _ = verify_quotes(items, guide_text)
    return ChangeSet(decision, source_url, verified)


def interpret_with_table(decision: str, guide_text: str, source_url: str = "",
                         removal_table=None, chat_fn=None):
    """Blind model pass merged with guided pass + deterministic table rows.

    With a structured table the model runs twice: once blind (prose
    only — the honest baseline) and once with the table rows to
    confirm. Returns (ChangeSet, stats) with blind_hits / blind_recall
    (blind coverage of the removal table), recall (guided coverage,
    None without a table), missed (table symbols both passes skipped,
    added deterministically), table_size, and quote_misses
    (deterministic quotes absent from the cleaned text — an extraction
    drift alarm). The guided pass wins kind conflicts on overlap;
    missed rows are added as verified items with exact row quotes.
    Without a table the single call is blind by construction.
    """
    table = removal_table or {"removed": [], "deprecated": []}
    rows = ([dict(r, kind="removed") for r in table.get("removed", [])]
            + [dict(r, kind="deprecated")
               for r in table.get("deprecated", [])])
    removed = {r["symbol"] for r in table.get("removed", [])}
    if rows:
        blind_items = interpret(decision, guide_text, source_url,
                                chat_fn, None).items
        guided_items = interpret(decision, guide_text, source_url,
                                 chat_fn, removal_table).items
    else:
        blind_items = guided_items = interpret(
            decision, guide_text, source_url, chat_fn, None).items
    guided_symbols = {i.symbol for i in guided_items}
    blind_symbols = {i.symbol for i in blind_items}
    model_items = guided_items + [i for i in blind_items
                                  if i.symbol not in guided_symbols]
    model_symbols = {i.symbol for i in model_items}
    added, quote_misses = [], []
    for row in rows:
        if row["symbol"] in model_symbols:
            continue
        if row["quote"] not in guide_text:
            quote_misses.append(row["symbol"])
        added.append(ChangeItem(
            symbol=row["symbol"], kind=row["kind"], quote=row["quote"],
            replacement=row.get("replacement"), verified=True))
    recall = ((len(guided_symbols & removed) / len(removed))
              if removed else None)
    blind_hits = len(blind_symbols & removed)
    stats = {"recall": recall,
             "missed": sorted(removed - model_symbols),
             "table_size": len(removed),
             "quote_misses": quote_misses,
             "blind_hits": blind_hits,
             "blind_recall": ((blind_hits / len(removed))
                              if removed else None)}
    return ChangeSet(decision, source_url, model_items + added), stats


def interpret_vendor(decision: str, guide_text: str, target: str | Path,
                     package: str, source_url: str = "",
                     chat_fn=None) -> ChangeSet:
    """Vendor change set from guide + repo source; verify both groundings.

    Config items (missing_param/missing_key/rejected_value) must quote
    the repo source (code_quote/file/line, string-matched); every other
    kind must quote the guide. Items failing their grounding drop.
    Raises RuntimeError when MODEL_API_KEY is missing. chat_fn
    defaults to llm.chat; the run store injects ctx.llm.
    """
    chat_fn = chat_fn or chat
    repo_text, files = collect_package_sources(target, package)
    user = (
        f"Decision: {decision}\n\n"
        "Vendor guide text (quotable for `quote` when it states the "
        "need; config grounding comes from the source below):\n\n"
        f"{guide_text}\n\n"
        f"{repo_text}\n"
        "Emit the change set as a single JSON object matching this schema:\n"
        f"{json.dumps(VENDOR_CHANGE_SET_JSON_SCHEMA['schema'])}"
    )
    data = extract_json(chat_fn(
        VENDOR_SYSTEM_PROMPT, user, max_tokens=12000,
        response_format={"type": "json_schema",
                         "json_schema": VENDOR_CHANGE_SET_JSON_SCHEMA}))
    guide_items, code_items = [], []
    for raw in data.get("items", []):
        if not raw.get("symbol") or not raw.get("kind"):
            continue
        detail = raw.get("detail")
        item = ChangeItem(
            symbol=raw["symbol"],
            kind=raw["kind"],
            quote=raw.get("quote") or "",
            replacement=raw.get("replacement"),
            detail=(BreakDetail(detail.get("expected"), detail.get("actual"),
                                detail.get("note"))
                    if isinstance(detail, dict) else None),
            source=("code" if raw["kind"] in CONFIG_KINDS else "guide"),
            code_quote=raw.get("code_quote") or "",
            code_file=raw.get("code_file") or "",
            code_line=raw.get("code_line") or 0,
        )
        (code_items if item.source == "code" else guide_items).append(item)
    verified_guide, _ = verify_quotes(guide_items, guide_text)
    verified_code, _ = verify_code_quotes(code_items, files)
    return ChangeSet(decision, source_url, verified_guide + verified_code)
