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
from urllib.parse import quote as url_quote

from chainreaction.graph.repo import package_dir, relative_posix
from chainreaction.llm import chat, extract_json

BREAK_KINDS = ["removed", "renamed", "signature_changed", "changed",
               "deprecated", "missing_param", "missing_key",
               "rejected_value"]

_SIGNATURE_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "removed_kwargs": {"type": "array", "items": {"type": "string"}},
        "renamed_kwargs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"old": {"type": "string"},
                               "new": {"type": "string"}},
                "required": ["old", "new"],
                "additionalProperties": False,
            },
        },
        "min_positionals": {"type": ["integer", "null"]},
    },
    "required": ["removed_kwargs", "renamed_kwargs", "min_positionals"],
    "additionalProperties": False,
}

_SUPPORTING_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "quote": {"type": "string"},
        "source_url": {"type": "string"},
        "introduced_in": {"type": ["string", "null"]},
    },
    "required": ["quote", "source_url", "introduced_in"],
    "additionalProperties": False,
}

_DETAIL_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "expected": {"type": ["string", "null"]},
        "actual": {"type": ["string", "null"]},
        "note": {"type": ["string", "null"]},
    },
    "required": ["expected", "actual", "note"],
    "additionalProperties": False,
}

_ITEM_REQUIRED = ["symbol", "kind", "quote", "replacement", "detail",
                  "source_url", "introduced_in", "signature",
                  "supporting"]


def _item_properties():
    return {
        "symbol": {"type": "string"},
        "kind": {"type": "string", "enum": BREAK_KINDS},
        "quote": {"type": "string"},
        "replacement": {"type": ["string", "null"]},
        "detail": _DETAIL_SCHEMA,
        "source_url": {"type": "string"},
        "introduced_in": {"type": ["string", "null"]},
        "signature": _SIGNATURE_SCHEMA,
        "supporting": _SUPPORTING_SCHEMA,
    }


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
                    "properties": _item_properties(),
                    "required": _ITEM_REQUIRED,
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
(e.g. "upgrade jinja2 to 3.1") and the official release notes for every
release in the upgrade span, emit the change set as JSON.

Rules:
- Every item needs a `quote`: an EXACT substring of ONE release's notes
  proving the symbol changed. Quotes are string-matched; paraphrases are
  dropped automatically, so copy precisely.
- `symbol` is the fully qualified name (e.g. "jinja2.pass_context"). A
  whole module can be the symbol when the notes deprecate or remove it
  as a unit (e.g. "werkzeug.urls"); name exclusions exactly as written
  ("except X and Y") inside the quote so they can be honored.
- `source_url` is the release-notes URL the quote comes from;
  `introduced_in` is that release's version. Both are checked against
  the quote's true location.
- Kinds (classify carefully — only `removed`, `renamed`, and
  `signature_changed` count as breakage): `removed` for members that
  no longer exist; `renamed` for members that moved to a new name (old
  uses break; `replacement` is the new name); `signature_changed` for
  calls that break (removed or renamed keyword arguments, changed
  positional order — record them in `signature`); `deprecated` for
  members that still work but warn (warnings, not breakage); `changed`
  for behavior changes in working members (flags, not breakage).
  `missing_param` covers a config item the decision needs but our code never
  supplies (function parameter, option, env var). `missing_key` covers a
  missing table/mapping entry (pricing table, registry). `rejected_value`
  covers our own schema/validator rejecting the decision's value (Literal,
  enum, regex). Record the machine-checkable gap in `detail.expected` vs
  `detail.actual` (e.g. expected `"meta" in Provider`, actual
  `Literal["anthropic","openai","ollama"]`).
- Indirect removals: when one release deprecates a member and a later
  release removes deprecated code, emit ONE `removed` item with the
  deprecation as `quote` and the removal note as `supporting` (its own
  exact quote + source_url + introduced_in).
- When in doubt, include the item with its quote; the verifier will confirm.
- Output JSON only, matching the change_set schema.
"""

_VENDOR_ITEM_PROPERTIES = dict(
    _item_properties(),
    code_quote={"type": "string"},
    code_file={"type": "string"},
    code_line={"type": "integer"},
)

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
                    "properties": _VENDOR_ITEM_PROPERTIES,
                    "required": _ITEM_REQUIRED + ["code_quote", "code_file",
                                                  "code_line"],
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
class SignatureChange:
    removed_kwargs: list[str] = field(default_factory=list)
    renamed_kwargs: dict[str, str] = field(default_factory=dict)
    min_positionals: int | None = None  # positional-order break boundary


@dataclass
class SupportingEvidence:
    quote: str = ""
    source_url: str = ""
    quote_offset: int = -1
    introduced_in: str | None = None
    verified: bool = False
    verified_mode: str = ""  # exact|normalized ("" when unverified)


@dataclass
class DroppedItem:
    symbol: str
    kind: str = ""
    # quote_not_found|out_of_package|duplicate|unparseable
    reason: str = ""
    source_url: str = ""


@dataclass
class ChangeItem:
    symbol: str
    # removed|renamed|signature_changed|changed|deprecated (dependency
    # API) or missing_param|missing_key|rejected_value (our code vs
    # the decision)
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
    # M3 per-item grounding (multi-source sets): which release-notes
    # URL the quote comes from, its character offset there, and the
    # release that introduced the change.
    source_url: str = ""
    quote_offset: int = -1
    introduced_in: str | None = None
    # high: deterministic table/list rows and verified renames/
    # removals. medium: other verified model items. low: pattern rows
    # and behavior changes.
    confidence: str = "medium"
    # model|table|list|pattern
    extraction: str = "model"
    # exact|normalized ("" when unverified)
    verified_mode: str = ""
    signature: SignatureChange | None = None
    supporting: SupportingEvidence | None = None


@dataclass
class ChangeSet:
    decision: str
    source_url: str
    items: list[ChangeItem] = field(default_factory=list)
    dropped: list[DroppedItem] = field(default_factory=list)


# Whitespace collapses; quotes and dashes unify. Nothing else —
# normalization must never rescue a paraphrase, only punctuation.
_VERIFY_TRANSLATE = str.maketrans({
    "’": "'", "‘": "'", "´": "'", "`": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-",
})


def normalize_for_verify(text: str) -> str:
    """Match-tolerant form: collapsed whitespace, unified quotes/dashes."""
    return " ".join((text or "").translate(_VERIFY_TRANSLATE).split())


def locate_quote(quote: str, source_text: str,
                 normalized: bool = True) -> tuple[int, str]:
    """(offset, mode) of the quote in the text; (-1, "") when absent.

    Exact substring first; then the normalization pass (§5) when
    enabled. A normalized offset addresses the normalized text.
    """
    if quote and quote in source_text:
        return source_text.index(quote), "exact"
    if normalized and quote:
        norm_text = normalize_for_verify(source_text)
        norm_quote = normalize_for_verify(quote)
        if norm_quote and norm_quote in norm_text:
            return norm_text.index(norm_quote), "normalized"
    return -1, ""


def verify_quotes(
    items: list[ChangeItem], source_text: str, source_url: str = "",
    normalized: bool = True,
) -> tuple[list[ChangeItem], list[ChangeItem]]:
    """Keep grounded items; return (verified, dropped).

    Guide items survive only when the quote string-matches (exact, or
    the §5 normalization pass); survivors record quote_offset,
    verified_mode, and source_url. Probe items carry no quote (no
    document states them) and survive as verified — their grounding
    is the probe run, labeled at render time. Code items are never
    verified here (they need their repo files); verify_code_quotes
    owns them and they drop when they reach this function by mistake.
    Legacy flows pass normalized=False to keep exact-only semantics.
    """
    verified, dropped = [], []
    for item in items:
        if item.source == "probe":
            item.verified = True
        elif item.source == "code":
            item.verified = False
        else:
            offset, mode = locate_quote(item.quote, source_text,
                                        normalized)
            item.verified = offset >= 0
            item.quote_offset = offset
            item.verified_mode = mode
            if source_url and not item.source_url:
                item.source_url = source_url
        (verified if item.verified else dropped).append(item)
    return verified, dropped


# Config/schema kinds name OUR definitions (closed world, resolved to
# graph nodes); every other kind names an external symbol to match uses.
CONFIG_KINDS = ("missing_param", "missing_key", "rejected_value")

# Kinds that seed breakage. `deprecated` members still work (warnings)
# and `changed` members only behave differently (flags); neither
# counts as breakage.
SEED_KINDS = ("removed", "renamed", "signature_changed")
# Unconditional symbol seeds. signature_changed seeds conditionally
# (call sites passing an affected argument) via signature_seeds, so it
# stays out of the plain symbol set.
PLAIN_SEED_KINDS = ("removed", "renamed")


def change_symbols(changeset: ChangeSet, verified_only: bool = True) -> set[str]:
    """Breakage seeds: removed/renamed symbols (verified ones by default).

    signature_changed items seed conditionally (see signature_seeds)
    and are excluded: a bare use without the affected argument does
    not break.
    """
    return {
        i.symbol
        for i in changeset.items
        if (not verified_only or i.verified) and i.kind in PLAIN_SEED_KINDS
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


def split_out_of_package(changeset: ChangeSet, package: str
                           ) -> tuple[ChangeSet, list[DroppedItem]]:
    """Drop config items naming symbols outside our package.

    Model-boundary sanitizer: config kinds resolve to graph nodes
    (closed world), so a config symbol outside the package — an env
    var, an SDK class, a model ID — can never resolve and would fail
    propagation. Returns (kept ChangeSet, DroppedItems). Every other
    kind names external symbols and is untouched.
    """
    kept, dropped = [], []
    for item in changeset.items:
        if (item.kind in CONFIG_KINDS and item.symbol != package
                and not item.symbol.startswith(package + ".")):
            dropped.append(DroppedItem(item.symbol, item.kind,
                                       "out_of_package",
                                       item.source_url))
        else:
            kept.append(item)
    kept_set = ChangeSet(changeset.decision, changeset.source_url, kept,
                         list(changeset.dropped))
    return kept_set, dropped


def drop_out_of_package_items(changeset: ChangeSet, package: str
                              ) -> tuple[ChangeSet, list[str]]:
    """Legacy wrapper: (kept ChangeSet, sorted dropped symbols)."""
    kept, dropped = split_out_of_package(changeset, package)
    return kept, sorted({d.symbol for d in dropped})


def seed_exclusions(changeset: ChangeSet,
                    verified_only: bool = True) -> dict[str, set[str]]:
    """Member names a module seed must NOT match ("X except A and B").

    Parses each seeding item's quote for except-clauses and returns
    {symbol: {member, ...}}. A seed with exclusions matches uses under
    the module other than the named members (which survive).
    """
    excluded: dict[str, set[str]] = {}
    for item in changeset.items:
        if ((verified_only and not item.verified)
                or item.kind not in SEED_KINDS or not item.quote):
            continue
        names = _except_members(item.quote)
        if names:
            excluded.setdefault(item.symbol, set()).update(names)
    return excluded


def _except_members(quote: str) -> set[str]:
    """Code literals inside except-clauses ("except `a` and `b`")."""
    names = set()
    for match in _EXCEPT_RE.finditer(quote):
        end = re.search(r"[.;]", quote[match.end():])
        stop = match.end() + end.start() if end else len(quote)
        clause = quote[match.start():stop]
        for lit in _LITERAL_RE.finditer(clause):
            for symbol in split_symbols(lit.group(1)):
                # Members are leaves ("uri_to_iri", not "pkg.uri_to_iri").
                names.add(symbol.split(".")[-1])
    return names


def signature_seeds(changeset: ChangeSet,
                    verified_only: bool = True) -> dict[str, dict]:
    """signature_changed seeds: symbol -> {kwargs, min_positionals}."""
    seeds: dict[str, dict] = {}
    for item in changeset.items:
        if ((verified_only and not item.verified)
                or item.kind != "signature_changed" or not item.signature):
            continue
        sig = item.signature
        kwargs = set(sig.removed_kwargs) | set(sig.renamed_kwargs)
        if not kwargs and sig.min_positionals is None:
            continue
        seeds[item.symbol] = {"kwargs": kwargs,
                              "min_positionals": sig.min_positionals}
    return seeds


def item_confidence(extraction: str, kind: str, verified: bool) -> str:
    """high/medium/low per §4 (§3's pattern pass stays low)."""
    if not verified:
        return "low"
    if extraction in ("table", "list"):
        return "high"
    if extraction == "pattern":
        return "low"
    if kind in ("removed", "renamed"):
        return "high"
    if kind == "changed":
        return "low"
    return "medium"


def item_link(item: ChangeItem) -> str:
    """Source URL with a text-fragment link at the quote start (§5).

    Code-grounded items (§8) link to their repo file:line instead.
    """
    if item.source == "code":
        if item.code_file and item.code_line:
            return f"{item.code_file}:{item.code_line}"
        return item.code_file
    if not item.source_url or not item.quote:
        return item.source_url
    start = " ".join(item.quote.split()[:8])[:120]
    return f"{item.source_url}#:~:text={url_quote(start)}"


def item_record(item: ChangeItem) -> dict:
    """changeset.json item: asdict plus the §5 text-fragment link."""
    record = asdict(item)
    record["link"] = item_link(item)
    return record


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
        # Same UI data as guide items (§8): offset into the file text.
        item.quote_offset = text.index(quote) if ok else -1
        item.verified_mode = "exact" if ok else ""
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


# -- deterministic extraction (M3 §3) --

# Rows are {symbol, quote, replacement, kind, extraction} with
# extraction in {"table", "list", "pattern"}. Quotes are exact
# substrings of the source text by construction.

SYMBOL_ALIASES = {"np": "numpy", "pd": "pandas",
                  "ndarray": "numpy.ndarray"}

_SYMBOL_RE = re.compile(r"^[A-Za-z_]\w*(?:\.\w+)*$")


def normalize_symbol(raw: str, package: str = "") -> str:
    """Canonical symbol from changelog markup ("" when not symbol-like).

    Strips backticks, call/bracket suffixes, and trailing punctuation;
    expands np./pd. aliases; prefixes a bare name with the package.
    """
    text = (raw or "").strip().strip("`").strip().lstrip("~").strip()
    text = re.split(r"[\(\[]", text, maxsplit=1)[0].strip()
    text = text.rstrip(".,:;").strip()
    if not text:
        return ""
    parts = text.split(".")
    if parts[0] in SYMBOL_ALIASES:
        rest = ".".join(parts[1:])
        text = SYMBOL_ALIASES[parts[0]] + (f".{rest}" if rest else "")
    elif package and "." not in text:
        text = f"{package}.{text}"
    return text if _SYMBOL_RE.match(text) else ""


def split_symbols(raw: str, package: str = "") -> list[str]:
    """Comma-separated cell → normalized symbols (order preserved)."""
    seen, out = set(), []
    for chunk in (raw or "").split(","):
        symbol = normalize_symbol(chunk, package)
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


_TABLE_RE = re.compile(r"<table.*?</table>", re.S)
_ROW_RE = re.compile(r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
                     re.S)
_TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.S)

_TABLE_KIND_RES = (
    ("renamed", re.compile(r"\brenam")),
    ("removed", re.compile(r"\bremov|\bexpir")),
    ("deprecated", re.compile(r"\bdeprecat")),
)


def _clean_cell(raw: str) -> str:
    # Same algorithm as the quotable text: chunk-joining inserts spaces
    # where inline tags abut punctuation ("add_docstring ."), so quotes
    # built this way are exact substrings by construction.
    return html_to_text(raw)


def _table_kind(context: str) -> str | None:
    for kind, regex in _TABLE_KIND_RES:
        if regex.search(context):
            return kind
    return None


def _table_ref_re(package: str):
    """Guidance-column reference: `package.x` or an alias spelling."""
    roots = {package} if package else set()
    for alias, canonical in SYMBOL_ALIASES.items():
        if canonical == package or canonical.startswith(package + "."):
            roots.add(alias)
    roots = sorted((r for r in roots if r), key=len, reverse=True)
    return re.compile(r"(?:^|[^\w.])((?:" + "|".join(roots) +
                      r")\.\w[\w.]*)") if roots else None


def _table_symbols(name: str, package: str,
                   method_prefix: str | None) -> list[str]:
    out = []
    for symbol in split_symbols(name, package):
        parts = symbol.split(".")
        if (method_prefix and len(parts) == 2
                and parts[0] == package):
            out.append(f"{package}.{method_prefix}.{parts[1]}")
        else:
            out.append(symbol)
    return out


def extract_table_rows(page: str, package: str = "numpy") -> list[dict]:
    """Deterministic rows from structured member tables (any package).

    A table qualifies when its header row or introducing text/heading
    mentions removed/expired/renamed/deprecated. NumPy's guide keeps
    its exact rows (same markers, same shapes).
    """
    rows = []
    tables = list(_TABLE_RE.finditer(page))
    ref_re = _table_ref_re(package)
    for index, match in enumerate(tables):
        prev_end = tables[index - 1].end() if index else 0
        # Markers may wrap across source lines; match collapsed text.
        context = " ".join(page[prev_end:match.start()][-800:].split())
        heads = list(re.finditer(r"<h[1-4][^>]*>(.*?)</h[1-4]>",
                                 page[:match.start()], re.S))
        heading = _clean_cell(heads[-1].group(1)).lower() if heads else ""
        header = " ".join(_clean_cell(m.group(1))
                         for m in _TH_RE.finditer(match.group(0)))
        kind = _table_kind(f"{context} {heading} {header}".lower())
        if kind is None:
            continue
        method_prefix = ("ndarray" if package == "numpy"
                         and "ndarray" in heading else None)
        for row in _ROW_RE.finditer(match.group(0)):
            name = _clean_cell(row.group(1))
            guidance = _clean_cell(row.group(2))
            if not name:
                continue
            if kind == "renamed":
                cands = split_symbols(guidance, package)
                replacement = cands[0] if cands else None
            else:
                ref = ref_re.search(guidance) if ref_re else None
                replacement = (normalize_symbol(ref.group(1), package)
                               if ref else None)
            for symbol in _table_symbols(name, package, method_prefix):
                rows.append({"symbol": symbol,
                             "quote": f"{name} {guidance}".strip(),
                             "replacement": replacement or None,
                             "kind": kind, "extraction": "table"})
    return rows


def extract_guide_tables(page: str) -> dict:
    """NumPy-flow wrapper: {"removed": [...], "deprecated": [...]}.

    Kept for ctx.fetch and the preset interpreters; rows carry the
    same fields plus kind/extraction. M3 flows use the flat row list.
    """
    rows = extract_table_rows(page, "numpy")
    return {
        "removed": [r for r in rows if r["kind"] == "removed"],
        "deprecated": [r for r in rows if r["kind"] == "deprecated"],
    }


# -- bullet / sentence extraction (Markdown + RST) --

_BULLET_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+")
_RST_UNDERLINE_RE = re.compile(r"^([=\-~^#*\"'])\1{3,}\s*$")
_LITERAL_RE = re.compile(r"`{1,2}([^`]+?)`{1,2}")

_BREAK_HEADING_RES = (
    ("renamed", re.compile(r"renam|replac|\bmov(e|ed|ing)\b")),
    ("removed", re.compile(r"remov|break|incompat|expir|delet|drop")),
    ("deprecated", re.compile(r"deprecat")),
)

_RENAMED_RES = (
    re.compile(r"\brenam\w*\s+(?:to|as)\b", re.I),
    re.compile(r"\breplac\w*\s+by\b", re.I),
    re.compile(r"\bmov\w*\s+to\b", re.I),
    re.compile(r"\bimported\s+from\b", re.I),
)
_REMOVED_RES = (
    re.compile(r"\bremov\w*\b", re.I),
    re.compile(r"\bdelet\w*\b", re.I),
)
_DEPRECATED_RES = (re.compile(r"\bdeprecat\w*\b", re.I),)
_USE_RE = re.compile(r"\b(?:re)?use\s+", re.I)
_FROM_RE = re.compile(r"\b(?:remov\w*|delet\w*)\s+from\b", re.I)
_REPLACED_WITH_RE = re.compile(r"\breplac\w*\s+(.+?)\s+with\s+(.+)", re.I)
# A literal followed by one of these names a call-site word, never a
# member symbol ("the `timeout` parameter was removed" is a signature
# change, not a removed pkg.timeout).
_PARAM_WORD_RE = re.compile(
    r"^\s+(?:parameters?|arguments?|keywords?|options?|flags?|settings?)\b",
    re.I)
_ATTR_WORD_RE = re.compile(r"^\s+(?:attributes?|propert(?:y|ies)|methods?)\b",
                           re.I)
_EXCEPT_RE = re.compile(r"\bexcept\b|\bother\s+than\b|\bexcluding\b", re.I)


def _heading_break_kind(heading: str) -> str | None:
    lowered = heading.lower()
    for kind, regex in _BREAK_HEADING_RES:
        if regex.search(lowered):
            return kind
    return None


def _parse_blocks(text: str):
    """(raw, flat, section_kind) blocks: bullets joined, prose by line.

    raw is an exact substring (the quote); flat is whitespace-collapsed
    (the match surface); section_kind comes from the nearest Markdown
    heading above the block.
    """
    lines = (text or "").splitlines()
    section = None
    i, total = 0, len(lines)
    while i < total:
        line = lines[i]
        if _MD_HEADING_RE.match(line):
            section = _heading_break_kind(line)
            i += 1
            continue
        if (line.strip() and not _BULLET_RE.match(line)
                and i + 1 < total
                and _RST_UNDERLINE_RE.match(lines[i + 1])
                and len(lines[i + 1].strip())
                >= max(4, len(line.strip()))):
            section = _heading_break_kind(line)
            i += 2
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            start = i
            i += 1
            while i < total:
                nxt = lines[i]
                if (not nxt.strip() or _MD_HEADING_RE.match(nxt)
                        or _BULLET_RE.match(nxt)):
                    break
                i += 1
            raw = "\n".join(lines[start:i])
            yield raw, " ".join(raw.split()), section
            continue
        if line.strip():
            yield line, " ".join(line.split()), section
        i += 1


def _literals(flat: str, package: str = "") -> list[str]:
    out = []
    for match in _LITERAL_RE.finditer(flat):
        for symbol in split_symbols(match.group(1), package):
            if symbol not in out:
                out.append(symbol)
    return out


def _sentence_end(flat: str, pos: int) -> int:
    end = re.search(r"[.;]", flat[pos:])
    return pos + end.start() if end else len(flat)


def _except_spans(flat: str) -> list:
    """Ranges whose literals are not breakage: except-clauses ("X
    except Y"), replacements ("use Z"), and survivors ("removed from
    W")."""
    spans = []
    for match in _EXCEPT_RE.finditer(flat):
        spans.append((match.start(), _sentence_end(flat, match.end())))
    for match in _USE_RE.finditer(flat):
        spans.append((match.end(), _sentence_end(flat, match.end())))
    for match in _FROM_RE.finditer(flat):
        spans.append((match.end(), _sentence_end(flat, match.end())))
    return spans


def _olds(flat: str, lits: list[str], package: str = "",
          extra_stop: int | None = None):
    """Literals outside except/instead spans (left of extra_stop).

    Literals naming call-site words ("the `x` parameter") are never
    member symbols and are excluded here, for every pass.
    """
    spans = _except_spans(flat)
    out = []
    for symbol in lits:
        for match in _LITERAL_RE.finditer(flat):
            if symbol not in split_symbols(match.group(1), package):
                continue
            pos = match.start()
            if extra_stop is not None and pos > extra_stop:
                continue
            if any(start <= pos < stop for start, stop in spans):
                continue
            if _PARAM_WORD_RE.match(flat[match.end():]):
                continue
            if symbol not in out:
                out.append(symbol)
    return out


def _bare_attr_before_trigger(flat: str, lits: list[str], trigger_pos: int,
                              package: str = "") -> set[str]:
    """Bare literals like "the `x` attribute of Y is deprecated".

    The trigger follows the literal, so the literal describes a member
    of something else (usually a signature word) rather than a member
    being deprecated itself ("Deprecate the `x` attribute" keeps the
    trigger first and is unaffected).
    """
    skipped = set()
    for match in _LITERAL_RE.finditer(flat):
        if match.start() >= trigger_pos:
            continue
        if "." in match.group(1):
            continue  # dotted literals are member references
        if not _ATTR_WORD_RE.match(flat[match.end():]):
            continue
        for symbol in split_symbols(match.group(1), package):
            if symbol in lits:
                skipped.add(symbol)
    return skipped


def _use_replacement(flat: str, package: str = ""):
    """First literal after "use" ("use Z", "use Z instead")."""
    use = _USE_RE.search(flat)
    if not use:
        return None
    stop = _sentence_end(flat, use.end())
    for match in _LITERAL_RE.finditer(flat):
        if use.end() <= match.start() < stop:
            cands = split_symbols(match.group(1), package)
            if cands:
                return cands[0]
    return None


def extract_list_rows(text: str, package: str = "") -> list[dict]:
    """Bullets under breaking headings → symbol = code literal.

    Renamed sections take the last literal as the replacement and the
    rest as old names; other sections emit every literal with no
    replacement.
    """
    rows = []
    for raw, flat, section in _parse_blocks(text):
        if section is None or not _BULLET_RE.match(raw):
            continue
        all_lits = _literals(flat, package)
        lits = _olds(flat, all_lits, package)
        if not lits:
            continue
        instead = _use_replacement(flat, package)
        if section == "renamed" and len(lits) >= 2:
            for old in lits[:-1]:
                rows.append({"symbol": old, "quote": raw,
                             "replacement": lits[-1], "kind": "renamed",
                             "extraction": "list"})
        elif section == "renamed":
            rows.append({"symbol": lits[0], "quote": raw,
                         "replacement": instead, "kind": "renamed",
                         "extraction": "list"})
        else:
            for symbol in lits:
                rows.append({"symbol": symbol, "quote": raw,
                             "replacement": instead, "kind": section,
                             "extraction": "list"})
    return rows


def _pattern_match(flat: str, lits: list[str], package: str = ""):
    """(kind, olds, replacement) for trigger sentences, else None."""
    with_match = _REPLACED_WITH_RE.search(flat)
    if with_match:
        olds = [s for s in lits
                if any(s in split_symbols(m.group(1), package)
                       for m in _LITERAL_RE.finditer(with_match.group(1)))]
        news = [s for s in lits
                if any(s in split_symbols(m.group(1), package)
                       for m in _LITERAL_RE.finditer(with_match.group(2)))]
        olds = [s for s in olds
                if s in set(_olds(flat, lits, package))]
        if olds:
            return "renamed", olds, news[0] if news else None
    for regex in _RENAMED_RES:
        match = regex.search(flat)
        if not match:
            continue
        olds = _olds(flat, lits, package, extra_stop=match.start())
        if not olds:
            continue
        news = [s for s in lits if s not in olds]
        repl = news[0] if news else None
        return "renamed", olds, repl
    for regex in _REMOVED_RES:
        if regex.search(flat):
            olds = _olds(flat, lits, package)
            if olds:
                return ("removed", olds,
                        _use_replacement(flat, package))
    for regex in _DEPRECATED_RES:
        match = regex.search(flat)
        if match:
            olds = [s for s in _olds(flat, lits, package)
                    if s not in _bare_attr_before_trigger(
                        flat, lits, match.start(), package)]
            if olds:
                return ("deprecated", olds,
                        _use_replacement(flat, package))
    return None


def extract_pattern_rows(text: str, package: str = "") -> list[dict]:
    """Trigger-sentence pass over every block (low confidence).

    Catches breaking bullets that sit directly under version headings
    with no breaking subsection (the jinja/werkzeug shape).
    """
    rows = []
    for raw, flat, _ in _parse_blocks(text):
        lits = _literals(flat, package)
        if not lits:
            continue
        matched = _pattern_match(flat, lits, package)
        if matched is None:
            continue
        kind, olds, repl = matched
        for symbol in olds:
            rows.append({"symbol": symbol, "quote": raw,
                         "replacement": repl, "kind": kind,
                         "extraction": "pattern"})
    return rows


_EXTRACTION_PRIORITY = {"table": 0, "list": 1, "pattern": 2}


def extract_deterministic_rows(text: str, package: str = "",
                               page_html: str | None = None) -> list[dict]:
    """Combined deterministic rows, deduped by symbol (table wins).

    A winner with no replacement adopts one from a lower-priority row
    for the same symbol.
    """
    rows = []
    if page_html:
        rows += extract_table_rows(page_html, package or "numpy")
    rows += extract_list_rows(text, package)
    rows += extract_pattern_rows(text, package)
    merged: dict[str, dict] = {}
    for row in rows:
        prior = merged.get(row["symbol"])
        if prior is None:
            merged[row["symbol"]] = dict(row)
        else:
            if (_EXTRACTION_PRIORITY[row["extraction"]]
                    < _EXTRACTION_PRIORITY[prior["extraction"]]):
                row, prior = prior, row
            if not prior["replacement"] and row["replacement"]:
                prior["replacement"] = row["replacement"]
            merged[row["symbol"]] = prior
    return list(merged.values())


def fetch_text(url: str, timeout: int = 60) -> str:
    """Fetch a migration guide / changelog as clean text (raises on failure)."""
    _, raw = fetch_page(url, timeout)
    return page_to_text(raw)


def to_json(changeset: ChangeSet) -> str:
    return json.dumps(asdict(changeset), indent=2)


def _parse_detail(raw) -> BreakDetail | None:
    if not isinstance(raw, dict):
        return None
    return BreakDetail(raw.get("expected"), raw.get("actual"),
                       raw.get("note"))


def _parse_signature(raw) -> SignatureChange | None:
    if not isinstance(raw, dict):
        return None
    removed = [k for k in raw.get("removed_kwargs", []) if k]
    renamed = {p.get("old"): p.get("new")
               for p in raw.get("renamed_kwargs", [])
               if isinstance(p, dict) and p.get("old") and p.get("new")}
    minimum = raw.get("min_positionals")
    if not removed and not renamed and minimum is None:
        return None
    return SignatureChange(removed, renamed, minimum)


def _parse_supporting(raw) -> SupportingEvidence | None:
    if not isinstance(raw, dict) or not raw.get("quote"):
        return None
    return SupportingEvidence(quote=raw.get("quote") or "",
                              source_url=raw.get("source_url") or "",
                              introduced_in=raw.get("introduced_in"))


def _rows_section(rows) -> str:
    """Guided-prompt section listing deterministic rows to confirm."""
    if not rows:
        return ""
    listed = "\n".join(
        f"- {r['symbol']} ({r.get('kind', '?')}): "
        f"{r.get('quote', '')[:160]}"
        for r in rows)
    return (
        "\n\nDeterministic extraction lists these members "
        "(confirm each with a quote from the notes above; add "
        "anything else you find in the prose):\n\n"
        f"{listed}\n")


def _table_rows_legacy(removal_table) -> list:
    return ([dict(r, kind="removed", extraction=r.get("extraction",
                                                      "table"))
             for r in removal_table.get("removed", [])]
            + [dict(r, kind="deprecated", extraction=r.get("extraction",
                                                           "table"))
               for r in removal_table.get("deprecated", [])])


def parse_model_items(data: dict, package: str = ""
                      ) -> tuple[list[ChangeItem], list[DroppedItem]]:
    """Model JSON → (items, unparseable drops). Tolerant of old shapes."""
    items, dropped = [], []
    for raw in data.get("items", []):
        if not isinstance(raw, dict):
            dropped.append(DroppedItem("?", "", "unparseable"))
            continue
        symbol = normalize_symbol(raw.get("symbol") or "", package)
        kind = raw.get("kind") or ""
        quote = raw.get("quote") or ""
        if not symbol or not quote or kind not in BREAK_KINDS:
            dropped.append(DroppedItem(
                raw.get("symbol") or "?", kind, "unparseable",
                raw.get("source_url") or ""))
            continue
        items.append(ChangeItem(
            symbol=symbol, kind=kind, quote=quote,
            replacement=raw.get("replacement"),
            detail=_parse_detail(raw.get("detail")),
            source_url=raw.get("source_url") or "",
            introduced_in=raw.get("introduced_in"),
            signature=(_parse_signature(raw.get("signature"))
                       if kind == "signature_changed" else None),
            supporting=_parse_supporting(raw.get("supporting")),
        ))
    return items, dropped


def interpret(decision: str, guide_text: str, source_url: str = "",
              chat_fn=None, removal_table=None, package: str = "",
              normalized: bool = True, rows=None) -> ChangeSet:
    """Generate the change set with Muse Spark; verify quotes vs the guide.

    Items whose quotes don't string-match are dropped (recorded with
    reason quote_not_found). Raises RuntimeError when MODEL_API_KEY
    is missing. chat_fn defaults to llm.chat; the run store injects
    ctx.llm so calls are recorded. removal_table (from
    extract_guide_tables) lists structured rows for the model to
    confirm; None keeps the prose-only prompt. rows is the M3 flat
    equivalent. Legacy flows pass normalized=False for exact-only
    verification.
    """
    chat_fn = chat_fn or chat
    flat = list(rows) if rows else _table_rows_legacy(removal_table or {})
    user = (
        f"Decision: {decision}\n\n"
        "Migration guide / changelog text (the ONLY quotable source — "
        "every quote must be an EXACT substring of this text):\n\n"
        f"{guide_text}\n"
        f"{_rows_section(flat)}\n"
        "Emit the change set as a single JSON object matching this schema:\n"
        f"{json.dumps(CHANGE_SET_JSON_SCHEMA['schema'])}"
    )
    data = extract_json(chat_fn(
        INTERPRET_SYSTEM_PROMPT, user, max_tokens=12000,
        response_format={"type": "json_schema",
                         "json_schema": CHANGE_SET_JSON_SCHEMA}))
    items, dropped = parse_model_items(data, package)
    verified, unverified = verify_quotes(items, guide_text, source_url,
                                         normalized)
    for item in unverified:
        dropped.append(DroppedItem(item.symbol, item.kind,
                                   "quote_not_found",
                                   item.source_url or source_url))
    for item in verified:
        item.confidence = item_confidence(item.extraction, item.kind, True)
    return ChangeSet(decision, source_url, verified, dropped)


# -- legacy single-guide flow (frozen prompts) --

# Byte-identical prompts for pre-M3 recordings: interpret_with_table
# keeps this path so old presets replay. Do not edit the strings.

LEGACY_CHANGE_SET_JSON_SCHEMA = {
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

LEGACY_INTERPRET_SYSTEM_PROMPT = """\
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

def _interpret_legacy(decision: str, guide_text: str, source_url: str = "",
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
        f"{json.dumps(LEGACY_CHANGE_SET_JSON_SCHEMA['schema'])}"
    )
    data = extract_json(chat_fn(
        LEGACY_INTERPRET_SYSTEM_PROMPT, user, max_tokens=12000,
        response_format={"type": "json_schema",
                         "json_schema": LEGACY_CHANGE_SET_JSON_SCHEMA}))
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
    verified, _ = verify_quotes(items, guide_text,
                                        normalized=False)
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
    rows = _table_rows_legacy(table)
    removed = {r["symbol"] for r in table.get("removed", [])}
    if rows:
        blind_items = _interpret_legacy(decision, guide_text, source_url,
                                        chat_fn, None).items
        guided_items = _interpret_legacy(decision, guide_text, source_url,
                                         chat_fn, removal_table).items
    else:
        blind_items = guided_items = _interpret_legacy(
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
            replacement=row.get("replacement"), verified=True,
            source_url=source_url,
            quote_offset=guide_text.find(row["quote"]),
            confidence="high", extraction="table",
            verified_mode="exact"))
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


# -- multi-release interpretation (M3 §2/§4/§5) --

# One prompt above this many characters splits into per-release calls
# (merged by symbol, earliest release kept).
MULTI_PROMPT_CHAR_LIMIT = 400_000

_PRIORITY_HEADING_RE = re.compile(
    r"remov|break|incompat|deprecat|renam|expir|api\s+chang|"
    r"behavio[u]?r\s+chang|migrat|upgrad|backward", re.I)


def _note_fields(note):
    """(version, source_url, text) from a ReleaseNotes or dict."""
    if isinstance(note, dict):
        return (note.get("version", ""), note.get("source_url", ""),
                note.get("text", ""))
    return (getattr(note, "version", ""), getattr(note, "source_url", ""),
            getattr(note, "text", ""))


def prioritize_sections(text: str) -> tuple[str, str]:
    """(priority, rest): breaking-titled sections first (§2).

    Splits on Markdown headings; a section is priority when its
    heading names removals, breaking changes, deprecations, renames,
    or migrations. Text before the first heading stays in rest.
    """
    chunks, current, priority = [], [], False
    prelude = []
    for line in (text or "").splitlines():
        if _MD_HEADING_RE.match(line):
            if current:
                chunks.append((priority, "\n".join(current)))
                current = []
            priority = bool(_PRIORITY_HEADING_RE.search(line))
            current = [line]
        elif current:
            current.append(line)
        else:
            prelude.append(line)
    if current:
        chunks.append((priority, "\n".join(current)))
    prio = "\n".join(c for p, c in chunks if p).strip()
    rest = "\n".join([*[c for p, c in chunks if not p],
                      "\n".join(prelude)]).strip()
    return prio, rest


def _release_block(version: str, url: str, text: str) -> str:
    prio, rest = prioritize_sections(text)
    parts = [f"=== RELEASE {version} ({url}) ==="]
    if prio:
        parts += ["--- priority sections (read first) ---", prio]
    if rest:
        parts += ["--- other sections ---", rest] if prio else [rest]
    return "\n".join(parts)


def _multi_prompt(decision: str, notes: list, rows=None) -> str:
    blocks = [_release_block(v, u, t)
              for v, u, t in (_note_fields(n) for n in notes)]
    return (
        f"Decision: {decision}\n\n"
        "Release notes (the ONLY quotable sources — every quote must "
        "be an EXACT substring of ONE release's text; report that "
        "release's version as introduced_in and its URL as "
        "source_url):\n\n"
        + "\n\n".join(blocks) + "\n"
        f"{_rows_section(rows or [])}\n"
        "Emit the change set as a single JSON object matching this schema:\n"
        f"{json.dumps(CHANGE_SET_JSON_SCHEMA['schema'])}"
    )


def attribute_quote(quote: str, notes: list,
                    hint_url: str = "",
                    hint_version: str | None = None
                    ) -> tuple[str, str | None, int, str]:
    """(url, version, offset, mode) locating the quote; ("", None, -1, "")
    when absent. notes are (version, url, text); several notes may
    share one URL, so attribution is per note, not per URL. The
    model's claimed note is checked first, then every note in order —
    attribution follows the quote, not the claim.
    """
    ordered = [(_note_fields(n)) for n in notes]
    hinted = [(v, u, t) for v, u, t in ordered
              if u == hint_url and (hint_version is None
                                    or v == hint_version)]
    rest = [(v, u, t) for v, u, t in ordered
            if not (u == hint_url and (hint_version is None
                                       or v == hint_version))]
    for version, url, text in hinted + rest:
        offset, mode = locate_quote(quote, text)
        if offset >= 0:
            return url, version, offset, mode
    return "", None, -1, ""


def _verify_multi_items(items: list[ChangeItem], notes: list
                        ) -> tuple[list, list]:
    """Attribute + verify items; fix introduced_in from true location."""
    verified, dropped = [], []
    for item in items:
        if item.source == "probe":
            item.verified = True
            verified.append(item)
            continue
        if item.source == "code" or not item.quote:
            item.verified = False
            dropped.append(DroppedItem(item.symbol, item.kind,
                                       "quote_not_found",
                                       item.source_url))
            continue
        url, version, offset, mode = attribute_quote(
            item.quote, notes, item.source_url, item.introduced_in)
        if not url:
            item.verified = False
            dropped.append(DroppedItem(item.symbol, item.kind,
                                       "quote_not_found",
                                       item.source_url))
            continue
        item.verified = True
        item.source_url = url
        item.quote_offset = offset
        item.verified_mode = mode
        item.introduced_in = version
        if item.supporting and item.supporting.quote:
            sup = item.supporting
            s_url, s_ver, s_off, s_mode = attribute_quote(
                sup.quote, notes, sup.source_url, sup.introduced_in)
            sup.verified = bool(s_url)
            sup.verified_mode = s_mode
            if s_url:
                sup.source_url = s_url
                sup.quote_offset = s_off
                sup.introduced_in = s_ver
        item.confidence = item_confidence(item.extraction, item.kind,
                                          True)
        verified.append(item)
    return verified, dropped


def _dedupe_model_items(items: list[ChangeItem]
                        ) -> tuple[list[ChangeItem], list[DroppedItem]]:
    """First symbol wins; later ones drop as duplicates."""
    kept, dropped, seen = [], [], set()
    for item in items:
        if item.symbol in seen:
            dropped.append(DroppedItem(item.symbol, item.kind,
                                       "duplicate", item.source_url))
        else:
            seen.add(item.symbol)
            kept.append(item)
    return kept, dropped


def interpret_multi(decision: str, notes: list, package: str = "",
                    chat_fn=None, page_html: dict[str, str] | None = None,
                    emit=None) -> tuple[ChangeSet, dict]:
    """Blind + guided passes over release notes, merged + completed.

    notes carry (version, source_url, text); page_html optionally maps
    source URLs to raw HTML for table extraction. Deterministic rows
    (table/list/pattern per note) guide the second pass and complete
    what both passes miss. Above MULTI_PROMPT_CHAR_LIMIT the notes
    split into per-release calls merged by symbol (earliest kept).
    Returns (ChangeSet, stats); stats keeps the legacy keys
    (recall/missed/table_size/quote_misses/blind_hits/blind_recall)
    plus survival_rate, model_only, det_by_method, source_counts,
    and releases. emit, when given, takes (type, data) for §9 events.
    """
    chat_fn = chat_fn or chat
    page_html = page_html or {}
    note_list = [(_note_fields(n)) for n in notes]
    if not note_list:
        raise ValueError("interpret_multi needs at least one note")
    note_texts = {(u, v): t for v, u, t in note_list}
    total_chars = sum(len(t) for _, _, t in note_list)
    det_rows: list[dict] = []
    for version, url, text in note_list:
        for row in extract_deterministic_rows(
                text, package, page_html.get(url)):
            det_rows.append(dict(row, source_url=url,
                                 introduced_in=version))
    if emit:
        by_method: dict[str, int] = {}
        for row in det_rows:
            by_method[row["extraction"]] = \
                by_method.get(row["extraction"], 0) + 1
        emit("log", {"message":
                     f"interpret: {len(det_rows)} deterministic rows "
                     f"{by_method or '(none)'}"})

    def run_pass(with_rows: bool):
        if total_chars > MULTI_PROMPT_CHAR_LIMIT:
            return _interpret_multi_split(
                decision, note_list, package, chat_fn,
                det_rows if with_rows else [])
        prompt = _multi_prompt(
            decision, notes, det_rows if with_rows else None)
        data = extract_json(chat_fn(
            INTERPRET_SYSTEM_PROMPT, prompt, max_tokens=16000,
            response_format={"type": "json_schema",
                             "json_schema": CHANGE_SET_JSON_SCHEMA}))
        items, dropped = parse_model_items(data, package)
        return items, dropped

    blind_raw, blind_dropped = run_pass(False)
    if emit:
        emit("log", {"message":
                     f"interpret: blind pass done "
                     f"({len(blind_raw)} items)"})
    if det_rows:
        guided_raw, guided_dropped = run_pass(True)
    else:
        guided_raw, guided_dropped = blind_raw, []
    if emit:
        emit("log", {"message":
                     f"interpret: guided pass done "
                     f"({len(guided_raw)} items)"})
    # Guided wins overlaps; blind-only merges in; dupes recorded.
    # Without deterministic rows there is no separate guided call, so
    # nothing is a duplicate of anything.
    guided_symbols = {i.symbol for i in guided_raw}
    if det_rows:
        merged_raw = guided_raw + [i for i in blind_raw
                                   if i.symbol not in guided_symbols]
        for item in blind_raw:
            if item.symbol in guided_symbols:
                blind_dropped.append(DroppedItem(item.symbol, item.kind,
                                                 "duplicate",
                                                 item.source_url))
    else:
        merged_raw = blind_raw
    model_items, dup_dropped = _dedupe_model_items(merged_raw)
    proposed = len(merged_raw)
    verified, unverified = _verify_multi_items(model_items, notes)
    dropped = (blind_dropped + guided_dropped + dup_dropped
               + [DroppedItem(i.symbol, i.kind, "quote_not_found",
                              i.source_url) for i in unverified])
    model_symbols = {i.symbol for i in verified}
    # Recall denominators use the high-precision methods only: the
    # pattern pass is a completion net, not ground truth (the model
    # should NOT reproduce its low-confidence rows).
    det_seed = {r["symbol"] for r in det_rows
                if r.get("kind") in SEED_KINDS
                and r.get("extraction") in ("table", "list")}
    det_pairs = {(r["symbol"], r.get("kind")) for r in det_rows}
    guided_verified = {i.symbol for i in verified
                       if i.symbol in guided_symbols}
    blind_raw_symbols = {i.symbol for i in blind_raw}
    blind_hits = len({i.symbol for i in verified
                      if i.symbol in blind_raw_symbols} & det_seed)
    added, quote_misses = [], []
    for row in det_rows:
        if row["symbol"] in model_symbols:
            continue
        text = note_texts.get((row.get("source_url", ""),
                               row.get("introduced_in")), "")
        offset = text.find(row["quote"]) if text else -1
        if offset < 0:
            quote_misses.append(row["symbol"])
        added.append(ChangeItem(
            symbol=row["symbol"], kind=row["kind"], quote=row["quote"],
            replacement=row.get("replacement"), verified=True,
            source_url=row.get("source_url", ""),
            quote_offset=offset,
            introduced_in=row.get("introduced_in"),
            confidence=item_confidence(row["extraction"], row["kind"],
                                       True),
            extraction=row["extraction"], verified_mode="exact"))
    if emit:
        emit("log", {"message":
                     f"interpret: verification: {len(verified)} verified, "
                     f"{len(dropped)} dropped, {len(added)} completed"})
    det_size = len(det_seed)
    counts: dict[str, dict[str, int]] = {
        url: {"proposed": 0, "verified": 0, "dropped": 0}
        for url in sorted({u for _, u, _ in note_list})}
    counts["unattributed"] = {"proposed": 0, "verified": 0,
                              "dropped": 0}
    for item in merged_raw:
        counts.get(item.source_url, counts["unattributed"])["proposed"] += 1
    for item in verified:
        counts.get(item.source_url, counts["unattributed"])["verified"] += 1
    for drop in dropped:
        counts.get(drop.source_url, counts["unattributed"])["dropped"] += 1
    stats = {
        "recall": ((len(guided_verified & det_seed) / det_size)
                   if det_size else None),
        "missed": sorted(det_seed - model_symbols),
        "table_size": det_size,
        "quote_misses": quote_misses,
        "blind_hits": blind_hits,
        "blind_recall": ((blind_hits / det_size)
                         if det_size else None),
        "survival_rate": (len(verified) / proposed if proposed else None),
        "completed": len(added),
        "model_only": sorted(
            {i.symbol for i in verified
             if (i.symbol, i.kind) not in det_pairs}),
        "det_by_method": {m: sum(1 for r in det_rows
                                 if r["extraction"] == m)
                          for m in ("table", "list", "pattern")},
        "source_counts": counts,
        "releases": [v for v, _, _ in note_list],
    }
    if emit:
        kinds: dict[str, int] = {}
        for item in verified + added:
            kinds[item.kind] = kinds.get(item.kind, 0) + 1
        emit("log", {"message":
                     f"interpret: final change set: "
                     f"{len(verified) + len(added)} items {kinds}"})
    return ChangeSet(decision, note_list[0][1], verified + added,
                     dropped), stats


def _interpret_multi_split(decision: str, note_list: list, package: str,
                           chat_fn, rows: list):
    """Per-release calls for oversized prompts; merged, earliest kept."""
    merged: dict[str, ChangeItem] = {}
    dropped: list[DroppedItem] = []
    for version, url, text in note_list:
        prompt = _multi_prompt(
            decision, [{"version": version, "source_url": url,
                        "text": text}],
            [r for r in rows if r.get("source_url") == url])
        data = extract_json(chat_fn(
            INTERPRET_SYSTEM_PROMPT, prompt, max_tokens=16000,
            response_format={"type": "json_schema",
                             "json_schema": CHANGE_SET_JSON_SCHEMA}))
        items, call_dropped = parse_model_items(data, package)
        dropped += call_dropped
        for item in items:
            if item.symbol not in merged:
                merged[item.symbol] = item
            else:
                dropped.append(DroppedItem(item.symbol, item.kind,
                                           "duplicate",
                                           item.source_url))
    return list(merged.values()), dropped


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
    dropped = []
    for raw in data.get("items", []):
        if not isinstance(raw, dict):
            dropped.append(DroppedItem("?", "", "unparseable"))
            continue
        # No package prefixing: config symbols name our definitions
        # as written; a bare env var must stay out-of-package so the
        # sanitizer drops it.
        symbol = normalize_symbol(raw.get("symbol") or "")
        kind = raw.get("kind") or ""
        if not symbol or kind not in BREAK_KINDS:
            dropped.append(DroppedItem(raw.get("symbol") or "?", kind,
                                       "unparseable",
                                       raw.get("source_url") or ""))
            continue
        item = ChangeItem(
            symbol=symbol,
            kind=kind,
            quote=raw.get("quote") or "",
            replacement=raw.get("replacement"),
            detail=_parse_detail(raw.get("detail")),
            source=("code" if kind in CONFIG_KINDS else "guide"),
            code_quote=raw.get("code_quote") or "",
            code_file=raw.get("code_file") or "",
            code_line=raw.get("code_line") or 0,
            source_url=raw.get("source_url") or "",
            introduced_in=raw.get("introduced_in"),
            signature=(_parse_signature(raw.get("signature"))
                       if kind == "signature_changed" else None),
            supporting=_parse_supporting(raw.get("supporting")),
        )
        (code_items if item.source == "code" else guide_items).append(item)
    verified_guide, unverified_guide = verify_quotes(
        guide_items, guide_text, source_url, normalized=False)
    verified_code, unverified_code = verify_code_quotes(code_items, files)
    for item in unverified_guide + unverified_code:
        dropped.append(DroppedItem(item.symbol, item.kind,
                                   "quote_not_found",
                                   item.source_url or source_url))
    items = verified_guide + verified_code
    for item in items:
        item.confidence = item_confidence(item.extraction, item.kind, True)
    return ChangeSet(decision, source_url, items, dropped)
