"""Interpreter scaffolding tests (canned guide text, no network, no key)."""

import json

import pytest

from chainreaction.interpret import (
    CHANGE_SET_JSON_SCHEMA,
    BreakDetail,
    ChangeItem,
    ChangeSet,
    change_symbols,
    config_items,
    html_to_text,
    interpret,
    to_json,
    verify_quotes,
)

# Already-cleaned guide text, as fetch_text returns it (tags stripped,
# whitespace collapsed to one line).
GUIDE = ("NumPy 2.0 migration guide (excerpt). Removed members: "
         "sctypes Access dtypes explicitly instead. "
         "maximum_sctype Use a specific dtype instead.")


def _items():
    return [
        ChangeItem("numpy.sctypes", "removed",
                   "sctypes Access dtypes explicitly instead."),
        ChangeItem("numpy.maximum_sctype", "removed",
                   "maximum_sctype Use a specific dtype instead."),
        ChangeItem("numpy.something", "removed",
                   "sctypes is gone, roughly speaking"),  # paraphrase: dropped
        ChangeItem("numpy.empty", "removed", ""),  # empty quote: dropped
    ]


def test_html_to_text_strips_tags_and_skips_scripts():
    page = ("<html><head><script>var sctypes = 1;</script></head>"
            "<body><table><tr><td><p>sctypes</p></td>\n"
            "<td><p>Access dtypes &amp; stuff instead.</p></td></tr></table>"
            "</body></html>")
    assert html_to_text(page) == "sctypes Access dtypes & stuff instead."


def test_verify_quotes_keeps_exact_matches_only():
    verified, dropped = verify_quotes(_items(), GUIDE)
    assert {i.symbol for i in verified} == {
        "numpy.sctypes", "numpy.maximum_sctype"}
    assert {i.symbol for i in dropped} == {"numpy.something", "numpy.empty"}
    assert all(i.verified for i in verified)


def test_verify_quotes_keeps_probe_items_quoteless():
    items = [ChangeItem("pkg.PRICING", "missing_key", "", source="probe")]
    verified, dropped = verify_quotes(items, GUIDE)
    assert verified == items and dropped == []
    assert items[0].verified is True


def test_change_symbols_respects_verified_flag():
    verified, _ = verify_quotes(_items(), GUIDE)
    cs = ChangeSet("upgrade numpy to 2.0", "https://example.invalid/guide",
                   verified + [ChangeItem("numpy.x", "removed", "nope")])
    assert change_symbols(cs) == {"numpy.sctypes", "numpy.maximum_sctype"}
    assert "numpy.x" in change_symbols(cs, verified_only=False)


def test_schema_shape_and_json_roundtrip():
    text = json.dumps(CHANGE_SET_JSON_SCHEMA)  # must be JSON-serializable
    assert json.loads(text)["name"] == "change_set"
    item_schema = CHANGE_SET_JSON_SCHEMA["schema"]["properties"]["items"]["items"]
    assert set(item_schema["required"]) == {
        "symbol", "kind", "quote", "replacement", "detail"}
    cs = ChangeSet("d", "u", [ChangeItem("s", "removed", "q", None, True)])
    assert json.loads(to_json(cs))["items"][0]["symbol"] == "s"


META_GUIDE = """\
Meta Model API quickstart (excerpt).

Set the base URL to https://api.meta.ai/v1 when constructing an
OpenAI-compatible client. Then call the muse-spark-1.3 model by name.
"""


def test_config_and_schema_break_kinds():
    item_schema = CHANGE_SET_JSON_SCHEMA["schema"]["properties"]["items"]["items"]
    assert set(item_schema["properties"]["kind"]["enum"]) == {
        "removed", "changed", "deprecated", "missing_param", "missing_key",
        "rejected_value",
    }
    detail_schema = item_schema["properties"]["detail"]
    assert set(detail_schema["required"]) == {"expected", "actual", "note"}
    items = [
        ChangeItem("sponsorfit.llm.Provider", "rejected_value",
                   "call the muse-spark-1.3 model by name",
                   detail=BreakDetail('"meta" in Provider',
                                      'Literal["anthropic","openai","ollama"]')),
        ChangeItem("sponsorfit.llm.MeteredLLMClient._call_openai",
                   "missing_param",
                   "Set the base URL to https://api.meta.ai/v1",
                   detail=BreakDetail("base_url=https://api.meta.ai/v1",
                                      "openai.OpenAI() with no base_url")),
        ChangeItem("sponsorfit.pricing.PRICING", "missing_key",
                   "call the muse-spark-1.3 model by name",
                   detail=BreakDetail('PRICING["meta"]["muse-spark-1.3"]',
                                      "KeyError: no meta provider table")),
    ]
    verified, dropped = verify_quotes(items, META_GUIDE)
    assert len(verified) == 3 and dropped == []
    cs = ChangeSet("switch to Meta Model API", "https://example.invalid/meta",
                   verified)
    assert change_symbols(cs) == set()  # config kinds aren't external symbols
    assert [i.symbol for i in config_items(cs)] == [
        "sponsorfit.llm.Provider",
        "sponsorfit.llm.MeteredLLMClient._call_openai",
        "sponsorfit.pricing.PRICING",
    ]


def test_interpret_no_key_names_the_blocker(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MODEL_API_KEY"):
        interpret("upgrade numpy to 2.0", "guide text")
