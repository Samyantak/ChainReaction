"""Decision interpreter: change-set schema, quote verification, live LLM call.

`interpret()` generates the change set with Muse Spark and quote-verifies
every item against the guide text; paraphrases are dropped automatically.
Key-free paths (stubs + verification) keep working without MODEL_API_KEY.
"""

import json
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
                            "enum": ["removed", "changed", "deprecated",
                                     "missing_param", "missing_key",
                                     "rejected_value"],
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
- Kinds `removed`/`changed`/`deprecated` cover dependency API breaks.
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


@dataclass
class ChangeSet:
    decision: str
    source_url: str
    items: list[ChangeItem] = field(default_factory=list)


def verify_quotes(
    items: list[ChangeItem], source_text: str
) -> tuple[list[ChangeItem], list[ChangeItem]]:
    """Keep items whose quote string-matches the source; return (verified, dropped)."""
    verified, dropped = [], []
    for item in items:
        item.verified = bool(item.quote) and item.quote in source_text
        (verified if item.verified else dropped).append(item)
    return verified, dropped


# Config/schema kinds name OUR definitions (closed world, resolved to
# graph nodes); every other kind names an external symbol to match uses.
CONFIG_KINDS = ("missing_param", "missing_key", "rejected_value")


def change_symbols(changeset: ChangeSet, verified_only: bool = True) -> set[str]:
    return {
        i.symbol
        for i in changeset.items
        if (not verified_only or i.verified) and i.kind not in CONFIG_KINDS
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


def fetch_text(url: str, timeout: int = 60) -> str:
    """Fetch a migration guide / changelog as clean text (raises on failure)."""
    req = urllib.request.Request(url, headers={"User-Agent": "ChainReaction/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        page = raw.decode("utf-8")
    except UnicodeDecodeError:
        page = raw.decode("latin-1")
    return html_to_text(page)


def to_json(changeset: ChangeSet) -> str:
    return json.dumps(asdict(changeset), indent=2)


def interpret(decision: str, guide_text: str, source_url: str = "") -> ChangeSet:
    """Generate the change set with Muse Spark; verify quotes vs the guide.

    Items whose quotes don't string-match are dropped. Raises
    RuntimeError when MODEL_API_KEY is missing.
    """
    user = (
        f"Decision: {decision}\n\n"
        "Migration guide / changelog text (the ONLY quotable source — "
        "every quote must be an EXACT substring of this text):\n\n"
        f"{guide_text}\n\n"
        "Emit the change set as a single JSON object matching this schema:\n"
        f"{json.dumps(CHANGE_SET_JSON_SCHEMA['schema'])}"
    )
    data = extract_json(chat(INTERPRET_SYSTEM_PROMPT, user,
                            max_tokens=12000))
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
