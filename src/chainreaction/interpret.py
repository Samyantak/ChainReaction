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
    # Grounding: "guide" (quote must string-match) or "probe" (a break
    # the verifier discovered, e.g. a KeyError — quoted by no document).
    source: str = "guide"


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
    their grounding is the probe run, labeled at render time.
    """
    verified, dropped = [], []
    for item in items:
        if item.source != "guide":
            item.verified = True
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
    """Model change set merged with deterministic guide-table rows.

    Returns (ChangeSet, stats) with recall (fraction of the removal
    table the model covered, None without a table), missed (table
    symbols the model skipped, added deterministically), table_size,
    and quote_misses (deterministic quotes absent from the cleaned
    text — an extraction drift alarm). Model classification wins on
    overlap; missed rows are added as verified items with exact row
    quotes.
    """
    changeset = interpret(decision, guide_text, source_url, chat_fn,
                          removal_table)
    table = removal_table or {"removed": [], "deprecated": []}
    model_symbols = {i.symbol for i in changeset.items}
    rows = ([dict(r, kind="removed") for r in table.get("removed", [])]
            + [dict(r, kind="deprecated")
               for r in table.get("deprecated", [])])
    added, quote_misses = [], []
    for row in rows:
        if row["symbol"] in model_symbols:
            continue
        if row["quote"] not in guide_text:
            quote_misses.append(row["symbol"])
        added.append(ChangeItem(
            symbol=row["symbol"], kind=row["kind"], quote=row["quote"],
            replacement=row.get("replacement"), verified=True))
    removed = {r["symbol"] for r in table.get("removed", [])}
    recall = (len(model_symbols & removed) / len(removed)) if removed else None
    stats = {"recall": recall,
             "missed": sorted(removed - model_symbols),
             "table_size": len(removed),
             "quote_misses": quote_misses}
    return ChangeSet(changeset.decision, changeset.source_url,
                     changeset.items + added), stats
