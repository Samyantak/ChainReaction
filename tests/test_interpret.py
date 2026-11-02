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


def test_only_removed_and_renamed_seed_breakage():
    from chainreaction.interpret import changed_symbols, deprecated_symbols

    cs = ChangeSet("d", "u", [
        ChangeItem("numpy.sctypes", "removed", "q", verified=True),
        ChangeItem("numpy.old", "renamed", "q", verified=True),
        ChangeItem("numpy.trapz", "deprecated", "q", verified=True),
        ChangeItem("numpy.asarray", "changed", "q", verified=True),
        ChangeItem("numpy.other", "changed", "q", verified=False),
    ])
    assert change_symbols(cs) == {"numpy.sctypes", "numpy.old"}
    assert deprecated_symbols(cs) == {"numpy.trapz"}
    assert changed_symbols(cs) == {"numpy.asarray"}


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
        "removed", "renamed", "changed", "deprecated", "missing_param",
        "missing_key", "rejected_value",
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


TABLE_HTML = """\
<html><body>
<h2>Main namespace</h2>
<p>Some members of the main np namespace were removed. The table below
shows members that have been removed:</p>
<table><tbody>
<tr class="row-odd"><td><p>maximum_sctype</p></td><td><p>Use a specific dtype instead.</p></td></tr>
<tr class="row-even"><td><p>mat</p></td><td><p>Use <code><span class="pre">np.asmatrix</span></code> instead.</p></td></tr>
</tbody></table>
<p>The next table presents deprecated members, which will go later:</p>
<table><tbody>
<tr class="row-odd"><td><p>trapz</p></td><td><p>Use np.trapezoid instead.</p></td></tr>
</tbody></table>
<h2>ndarray and scalar methods</h2>
<p>A few methods were removed. The table below provides replacements for
the removed members:</p>
<table><tbody>
<tr class="row-odd"><td><p>newbyteorder</p></td><td><p>Use arr.view instead.</p></td></tr>
</tbody></table>
<table><tbody>
<tr class="row-odd"><td><p>zzz</p></td><td><p>Unrelated table.</p></td></tr>
</tbody></table>
</body></html>
"""


def test_extract_guide_tables_parses_numpy_shape():
    from chainreaction.interpret import extract_guide_tables

    table = extract_guide_tables(TABLE_HTML)
    removed = {r["symbol"]: r for r in table["removed"]}
    assert set(removed) == {"numpy.maximum_sctype", "numpy.mat",
                            "numpy.ndarray.newbyteorder"}
    assert removed["numpy.maximum_sctype"]["quote"] == \
        "maximum_sctype Use a specific dtype instead."
    assert removed["numpy.maximum_sctype"]["replacement"] is None
    assert removed["numpy.mat"]["replacement"] == "numpy.asmatrix"
    deprecated = {r["symbol"]: r for r in table["deprecated"]}
    assert set(deprecated) == {"numpy.trapz"}
    assert deprecated["numpy.trapz"]["replacement"] == "numpy.trapezoid"
    assert extract_guide_tables("<html><body>no tables</body></html>") == \
        {"removed": [], "deprecated": []}


def test_interpret_with_table_completes_missed_rows():
    from chainreaction.interpret import interpret_with_table

    guide = ("maximum_sctype Use a specific dtype instead. "
             "mat Use np.asmatrix instead. "
             "trapz Use np.trapezoid instead.")
    table = {"removed": [
        {"symbol": "numpy.maximum_sctype",
         "quote": "maximum_sctype Use a specific dtype instead.",
         "replacement": None},
        {"symbol": "numpy.mat", "quote": "mat Use np.asmatrix instead.",
         "replacement": "numpy.asmatrix"}],
        "deprecated": [{"symbol": "numpy.trapz",
                        "quote": "trapz Use np.trapezoid instead.",
                        "replacement": "numpy.trapezoid"}]}

    calls = []

    def fake(system, user, max_tokens=8000, response_format=None):
        guided = "structured tables" in user
        calls.append("guided" if guided else "blind")
        return json.dumps({"items": [{
            "symbol": "numpy.maximum_sctype",
            "kind": "renamed" if guided else "removed",
            "quote": "maximum_sctype Use a specific dtype instead.",
            "replacement": "numpy.dtype" if guided else None}]})

    cs, stats = interpret_with_table("d", guide, "u", table, chat_fn=fake)
    assert calls == ["blind", "guided"]
    assert stats["recall"] == 0.5
    assert stats["blind_hits"] == 1
    assert stats["blind_recall"] == 0.5
    assert stats["missed"] == ["numpy.mat"]
    assert stats["table_size"] == 2
    assert stats["quote_misses"] == []
    kinds = {i.symbol: i.kind for i in cs.items}
    assert kinds == {"numpy.maximum_sctype": "renamed",  # guided wins
                     "numpy.mat": "removed", "numpy.trapz": "deprecated"}
    assert all(i.verified for i in cs.items)

    # Blind-only finds merge in (guided order kept).
    def fake_extra(system, user, max_tokens=8000, response_format=None):
        if "structured tables" in user:
            items = []
        else:
            items = [
                {"symbol": "numpy.mat", "kind": "removed",
                 "quote": "mat Use np.asmatrix instead.",
                 "replacement": None}]
        return json.dumps({"items": items})

    cs, stats = interpret_with_table("d", guide, "u", table,
                                     chat_fn=fake_extra)
    assert stats["blind_hits"] == 1
    assert stats["missed"] == ["numpy.maximum_sctype"]
    assert {i.symbol for i in cs.items} == {
        "numpy.maximum_sctype", "numpy.mat", "numpy.trapz"}

    # No table: one call, blind by construction.
    plain_calls = []

    def fake_plain(system, user, max_tokens=8000, response_format=None):
        assert "structured tables" not in user
        plain_calls.append(user)
        return json.dumps({"items": []})

    _, stats = interpret_with_table("d", guide, "u", None, chat_fn=fake_plain)
    assert stats["recall"] is None and stats["table_size"] == 0
    assert stats["blind_hits"] == 0 and stats["blind_recall"] is None
    assert len(plain_calls) == 1


def test_drop_out_of_package_items_keeps_external_kinds():
    from chainreaction.interpret import drop_out_of_package_items

    cs = ChangeSet("d", "u", [
        ChangeItem("MODEL_API_KEY", "missing_param", "q"),
        ChangeItem("sponsorfit.llm.Provider", "rejected_value", "q"),
        ChangeItem("muse-voice", "changed", "q"),
        ChangeItem("numpy.sctypes", "removed", "q"),
    ])
    kept, dropped = drop_out_of_package_items(cs, "sponsorfit")
    assert dropped == ["MODEL_API_KEY"]
    assert [i.symbol for i in kept.items] == [
        "sponsorfit.llm.Provider", "muse-voice", "numpy.sctypes"]


def test_verify_all_splits_groundings_in_order(tmp_path):
    from chainreaction.interpret import verify_all

    pkg = tmp_path / "src" / "fit"
    pkg.mkdir(parents=True)
    (pkg / "llm.py").write_text("X = 1\n", encoding="utf-8")
    items = [
        ChangeItem("numpy.sctypes", "removed", "sctypes was removed"),
        ChangeItem("fit.llm.X", "missing_key", "", source="code",
                   code_quote="X = 1", code_file="src/fit/llm.py",
                   code_line=1),
        ChangeItem("fit.llm.Y", "missing_key", "", source="code",
                   code_quote="not in source", code_file="src/fit/llm.py",
                   code_line=1),
        ChangeItem("numpy.gone", "removed", "paraphrase, not quoted"),
    ]
    verified, dropped = verify_all(items, "guide: sctypes was removed",
                                   tmp_path, "fit")
    assert [i.symbol for i in verified] == ["numpy.sctypes", "fit.llm.X"]
    assert [i.symbol for i in dropped] == ["fit.llm.Y", "numpy.gone"]


def test_interpret_no_key_names_the_blocker(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MODEL_API_KEY"):
        interpret("upgrade numpy to 2.0", "guide text")


def test_collect_package_sources_numbers_lines(tmp_path):
    from chainreaction.interpret import collect_package_sources

    pkg = tmp_path / "src" / "fit"
    pkg.mkdir(parents=True)
    (pkg / "llm.py").write_text(
        'Provider = Literal["a"]\nself._x = openai.OpenAI()\n',
        encoding="utf-8")
    text, files = collect_package_sources(tmp_path, "fit")
    assert files == {"src/fit/llm.py":
                     'Provider = Literal["a"]\n'
                     "self._x = openai.OpenAI()\n"}
    assert "=== FILE: src/fit/llm.py ===" in text
    assert '1: Provider = Literal["a"]' in text
    assert "2: self._x = openai.OpenAI()" in text


def test_verify_code_quotes_needs_exact_span_and_line():
    from chainreaction.interpret import verify_code_quotes

    files = {"src/fit/llm.py":
             'Provider = Literal["anthropic", "openai"]\n'
             "x = 1\n"}
    good = ChangeItem("fit.llm.Provider", "rejected_value", "",
                      source="code",
                      code_quote='Provider = Literal["anthropic", "openai"]',
                      code_file="src/fit/llm.py", code_line=1)
    paraphrase = ChangeItem("fit.llm.Provider", "rejected_value", "",
                            source="code",
                            code_quote="Provider allows two providers",
                            code_file="src/fit/llm.py", code_line=1)
    drifted = ChangeItem("fit.llm.Provider", "rejected_value", "",
                         source="code",
                         code_quote='Provider = Literal["anthropic", "openai"]',
                         code_file="src/fit/llm.py", code_line=2)
    missing = ChangeItem("fit.llm.Provider", "rejected_value", "",
                         source="code",
                         code_quote='Provider = Literal["anthropic", "openai"]',
                         code_file="src/fit/nope.py", code_line=1)
    verified, dropped = verify_code_quotes(
        [good, paraphrase, drifted, missing], files)
    assert verified == [good] and good.verified is True
    assert len(dropped) == 3
    assert all(i.verified is False for i in dropped)


def test_verify_quotes_drops_code_items_without_files():
    verified, dropped = verify_quotes(
        [ChangeItem("fit.a", "missing_param", "", source="code",
                    code_quote="x", code_file="f.py", code_line=1)],
        "guide text")
    assert verified == [] and len(dropped) == 1


def test_interpret_vendor_grounds_config_in_code(tmp_path):
    import json as json_mod

    from chainreaction.interpret import interpret_vendor

    pkg = tmp_path / "src" / "fit"
    pkg.mkdir(parents=True)
    (pkg / "llm.py").write_text(
        'Provider = Literal["anthropic", "openai"]\n', encoding="utf-8")
    guide = "Point your client at https://api.meta.ai/v1 for Meta."

    def fake(system, user, max_tokens=8000, response_format=None):
        assert "Vendor guide text" in user
        assert "=== FILE: src/fit/llm.py ===" in user
        assert response_format["json_schema"]["name"] == "vendor_change_set"
        return json_mod.dumps({"items": [
            {"symbol": "fit.llm.Provider", "kind": "rejected_value",
             "quote": "", "replacement": None,
             "code_quote": 'Provider = Literal["anthropic", "openai"]',
             "code_file": "src/fit/llm.py", "code_line": 1,
             "detail": {"expected": '"meta" in Provider',
                        "actual": 'Literal["anthropic","openai"]',
                        "note": None}},
            {"symbol": "fit.llm.Other", "kind": "missing_param",
             "quote": "", "replacement": None,
             "code_quote": "paraphrased, not in source",
             "code_file": "src/fit/llm.py", "code_line": 1,
             "detail": {"expected": "x", "actual": "y", "note": None}},
            {"symbol": "meta.voice", "kind": "changed",
             "quote": "Point your client at https://api.meta.ai/v1",
             "replacement": None, "code_quote": "", "code_file": "",
             "code_line": 0, "detail": None},
        ]})

    cs = interpret_vendor("switch", guide, tmp_path, "fit", "u",
                          chat_fn=fake)
    by_symbol = {i.symbol: i for i in cs.items}
    assert set(by_symbol) == {"fit.llm.Provider", "meta.voice"}
    assert by_symbol["fit.llm.Provider"].source == "code"
    assert by_symbol["fit.llm.Provider"].verified is True
    assert by_symbol["meta.voice"].source == "guide"
