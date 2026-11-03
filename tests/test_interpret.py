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
        "symbol", "kind", "quote", "replacement", "detail", "source_url",
        "introduced_in", "signature", "supporting"}
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
        "removed", "renamed", "signature_changed", "changed", "deprecated",
        "missing_param",
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


def test_verify_code_quotes_records_offset_and_mode():
    from chainreaction.interpret import item_link, verify_code_quotes

    files = {"src/fit/llm.py": 'Provider = Literal["a"]\n'}
    items = [ChangeItem("fit.Provider", "rejected_value", "",
                        source="code", code_quote='Literal["a"]',
                        code_file="src/fit/llm.py", code_line=1)]
    verified, dropped = verify_code_quotes(items, files)
    assert dropped == []
    assert verified[0].quote_offset == 11
    assert verified[0].verified_mode == "exact"
    assert item_link(verified[0]) == "src/fit/llm.py:1"


# -- M3 §3: deterministic extraction beyond NumPy --

def test_normalize_symbol_shapes():
    from chainreaction.interpret import normalize_symbol, split_symbols

    assert normalize_symbol("np.NaN()", "numpy") == "numpy.NaN"
    assert normalize_symbol("``jinja2.pass_context``", "jinja2") == \
        "jinja2.pass_context"
    assert normalize_symbol("contextfunction", "jinja2") == \
        "jinja2.contextfunction"
    assert normalize_symbol("urllib.parse", "werkzeug") == "urllib.parse"
    assert normalize_symbol("~pkg.mod.fn", "pkg") == "pkg.mod.fn"
    assert normalize_symbol("pd.DataFrame") == "pandas.DataFrame"
    assert normalize_symbol("3.6", "pkg") == ""
    assert normalize_symbol("not a symbol", "pkg") == ""
    assert split_symbols("old_a, old_b", "pkg") == ["pkg.old_a", "pkg.old_b"]
    assert split_symbols("np.mat, mat", "numpy") == ["numpy.mat"]


def test_extract_table_rows_generic_package():
    from chainreaction.interpret import extract_table_rows

    page = """<h2>Renamed API</h2>
<p>The following members were renamed:</p>
<table><tr><th>Old</th><th>New</th></tr>
<tr><td>old_a, old_b</td><td>new_a</td></tr>
</table>
<h2>Other changes</h2>
<table><tr><td>unrelated</td><td>stuff</td></tr></table>
"""
    rows = extract_table_rows(page, "pkg")
    assert [(r["symbol"], r["replacement"], r["kind"], r["extraction"])
            for r in rows] == [
        ("pkg.old_a", "pkg.new_a", "renamed", "table"),
        ("pkg.old_b", "pkg.new_a", "renamed", "table"),
    ]


JINJA_NOTES = """\
## Version 3.1.0

Released 2022-03-24

- `contextfunction` is replaced by `pass_context`.
- `environmentfilter` and `environmentfunction` are replaced by
  `pass_environment`.
- `Markup` and `escape` should be imported from MarkupSafe.
- Add `items` filter.
"""

WERKZEUG_NOTES = """\
## Version 2.3.0

Released 2023-04-25

- Deprecate the `werkzeug.urls` module, except for the `uri_to_iri` and
  `iri_to_uri` functions. Use the `urllib.parse` library instead.
- Remove previously deprecated code.
"""

RST_NOTES = """\
Version 3.1.0
-------------

Removals
--------

- ``old_func`` has been removed, use ``new_func`` instead.
- ``gone.mod`` was deleted.

Deprecations
------------

- ``tired`` is deprecated.
"""


def test_extract_pattern_rows_jinja_shape():
    from chainreaction.interpret import extract_pattern_rows

    rows = extract_pattern_rows(JINJA_NOTES, "jinja2")
    by_symbol = {r["symbol"]: r for r in rows}
    assert set(by_symbol) == {
        "jinja2.contextfunction", "jinja2.environmentfilter",
        "jinja2.environmentfunction", "jinja2.Markup", "jinja2.escape"}
    assert by_symbol["jinja2.contextfunction"]["replacement"] == \
        "jinja2.pass_context"
    assert by_symbol["jinja2.environmentfilter"]["replacement"] == \
        "jinja2.pass_environment"
    assert by_symbol["jinja2.Markup"]["replacement"] is None
    assert {r["kind"] for r in rows} == {"renamed"}
    assert {r["extraction"] for r in rows} == {"pattern"}
    assert all(r["quote"] in JINJA_NOTES for r in rows)


def test_extract_pattern_rows_except_clause():
    from chainreaction.interpret import extract_pattern_rows

    rows = extract_pattern_rows(WERKZEUG_NOTES, "werkzeug")
    assert [(r["symbol"], r["kind"], r["replacement"]) for r in rows] == [
        ("werkzeug.urls", "deprecated", "urllib.parse")]
    assert rows[0]["quote"] in WERKZEUG_NOTES


def test_extract_pattern_rows_skip_call_site_words():
    from chainreaction.interpret import extract_pattern_rows

    text = ("## 2.0\n\n"
            "- The `timeout` parameter was removed from `connect`.\n"
            "- The `is_filename` parameter to `unquote_it` is deprecated.\n"
            "- The `charset` attribute of `Request` is deprecated.\n"
            "- Deprecate the `__version__` attribute.\n"
            "- Remove the `stale` option entirely.\n")
    rows = extract_pattern_rows(text, "pkg")
    by_symbol = {r["symbol"]: r for r in rows}
    # Parameters/options ("removed from" survivors too) are signature
    # words, never member symbols; a bare attribute named before the
    # trigger is one too. Owners ("of `Request`") and member-first
    # deprecations survive as low-confidence rows.
    assert set(by_symbol) == {"pkg.unquote_it", "pkg.Request",
                              "pkg.__version__"}
    assert by_symbol["pkg.unquote_it"]["kind"] == "deprecated"
    assert by_symbol["pkg.__version__"]["kind"] == "deprecated"


def test_heading_break_kind_removed_not_renamed():
    from chainreaction.interpret import _heading_break_kind

    assert _heading_break_kind("### Removed") == "removed"
    assert _heading_break_kind("### Moved") == "renamed"
    assert _heading_break_kind("### Renamed") == "renamed"
    assert _heading_break_kind("### Changed") is None


def test_extract_list_rows_rst_sections():
    from chainreaction.interpret import extract_list_rows

    rows = extract_list_rows(RST_NOTES, "pkg")
    by_symbol = {r["symbol"]: r for r in rows}
    assert set(by_symbol) == {"pkg.old_func", "gone.mod", "pkg.tired"}
    assert by_symbol["pkg.old_func"]["kind"] == "removed"
    assert by_symbol["pkg.tired"]["kind"] == "deprecated"
    assert all(r["extraction"] == "list" for r in rows)
    assert all(r["quote"] in RST_NOTES for r in rows)


def test_extract_deterministic_rows_merge_priority():
    from chainreaction.interpret import extract_deterministic_rows

    rows = extract_deterministic_rows(RST_NOTES, "pkg")
    by_symbol = {r["symbol"]: r for r in rows}
    # Every bullet also matches the pattern pass; list wins each merge,
    # keeping the "use X instead" replacement.
    assert by_symbol["pkg.old_func"]["extraction"] == "list"
    assert by_symbol["pkg.old_func"]["replacement"] == "pkg.new_func"
    assert by_symbol["gone.mod"]["extraction"] == "list"
    assert by_symbol["pkg.tired"]["extraction"] == "list"
    assert all(r["quote"] in RST_NOTES for r in rows)


# -- M3 §4/§5: expanded items, verification modes, multi-release flow --

def test_verify_quotes_normalized_mode():
    from chainreaction.interpret import verify_quotes

    text = "Deprecate  the `foo`   module\u2014use `bar\u2019s` replacement."
    items = [
        ChangeItem("pkg.foo", "deprecated",
                   "Deprecate the `foo` module—use `bar's` replacement."),
        ChangeItem("pkg.paraphrase", "removed", "foo is gone, roughly"),
    ]
    verified, dropped = verify_quotes(items, text, "https://x/notes")
    assert [i.symbol for i in verified] == ["pkg.foo"]
    assert verified[0].verified_mode == "normalized"
    assert verified[0].quote_offset >= 0
    assert verified[0].source_url == "https://x/notes"
    assert [i.symbol for i in dropped] == ["pkg.paraphrase"]
    # Exact-only legacy flows don't rescue punctuation drift.
    legacy, _ = verify_quotes(
        [ChangeItem("pkg.foo", "deprecated", items[0].quote)],
        text, normalized=False)
    assert legacy == []


def test_locate_quote_exact_offset():
    from chainreaction.interpret import locate_quote

    text = "aaa `foo` was removed here."
    offset, mode = locate_quote("`foo` was removed", text)
    assert (offset, mode) == (4, "exact")
    assert locate_quote("missing", text) == (-1, "")


def test_parse_model_items_new_fields():
    from chainreaction.interpret import parse_model_items

    data = {"items": [
        {"symbol": "pkg.fn()", "kind": "signature_changed",
         "quote": "`fn` drops `gone`.", "replacement": None,
         "source_url": "https://x/n", "introduced_in": "2.0",
         "signature": {"removed_kwargs": ["gone"],
                       "renamed_kwargs": [{"old": "a", "new": "b"}],
                       "min_positionals": None},
         "supporting": {"quote": "Removed deprecated code.",
                        "source_url": "https://x/n2",
                        "introduced_in": "3.0"}},
        {"symbol": "pkg.bad", "kind": "bogus_kind",
         "quote": "x", "source_url": ""},
        {"symbol": "not a symbol!!", "kind": "removed", "quote": "x"},
        "junk",
    ]}
    items, dropped = parse_model_items(data, "pkg")
    assert len(items) == 1
    item = items[0]
    assert item.symbol == "pkg.fn"
    assert item.signature.removed_kwargs == ["gone"]
    assert item.signature.renamed_kwargs == {"a": "b"}
    assert item.supporting.quote == "Removed deprecated code."
    assert item.supporting.introduced_in == "3.0"
    assert [(d.symbol, d.reason) for d in dropped] == [
        ("pkg.bad", "unparseable"), ("not a symbol!!", "unparseable"),
        ("?", "unparseable")]


MULTI_NOTES = [
    {"version": "3.0.1", "source_url": "https://x/changes-3.0",
     "text": ("## Version 3.0.1\n\n"
              "- `oldthing` was removed, use `newthing` instead.\n\n"
              "### Removed\n\n"
              "- `listgone` is gone for good.\n")},
    {"version": "3.1.0", "source_url": "https://x/changes-3.1",
     "text": ("## Version 3.1.0\n\n"
              "- `contextfunction` is replaced by `pass_context`.\n"
              "- Remove previously deprecated code.\n"
              "- `quiet` behaves differently now.\n")},
]


def _multi_fake(system, user, max_tokens=8000, response_format=None):
    guided = "Deterministic extraction" in user
    if not guided:
        items = [
            {"symbol": "pkg.contextfunction", "kind": "renamed",
             "quote": "`contextfunction` is replaced by `pass_context`.",
             "replacement": "pkg.pass_context",
             "source_url": "https://x/changes-3.0",
             "introduced_in": "3.0.1",
             "supporting": {
                 "quote": "Remove previously deprecated code.",
                 "source_url": "https://x/changes-3.1",
                 "introduced_in": "3.1.0"}},
            {"symbol": "pkg.quiet", "kind": "removed",
             "quote": "`quiet` behaves differently now.",
             "replacement": None, "source_url": "https://x/changes-3.1",
             "introduced_in": "3.1.0"},
            {"symbol": "pkg.bogus", "kind": "removed",
             "quote": "this never appears anywhere.",
             "replacement": None, "source_url": "https://x/changes-3.1",
             "introduced_in": "3.1.0"},
        ]
    else:
        items = [
            {"symbol": "pkg.contextfunction", "kind": "renamed",
             "quote": "`contextfunction` is replaced by `pass_context`.",
             "replacement": "pkg.pass_context",
             "source_url": "https://x/changes-3.0",
             "introduced_in": "3.0.1",
             "supporting": {
                 "quote": "Remove previously deprecated code.",
                 "source_url": "https://x/changes-3.1",
                 "introduced_in": "3.1.0"}},
            {"symbol": "pkg.oldthing", "kind": "removed",
             "quote": "`oldthing` was removed, use `newthing` instead.",
             "replacement": "pkg.newthing",
             "source_url": "https://x/changes-3.0",
             "introduced_in": "3.0.1"},
            {"symbol": "pkg.listgone", "kind": "removed",
             "quote": "`listgone` is gone for good.",
             "replacement": None,
             "source_url": "https://x/changes-3.0",
             "introduced_in": "3.0.1"},
        ]
    return json.dumps({"items": items})


def test_interpret_multi_two_releases():
    from chainreaction.interpret import interpret_multi

    events = []
    cs, stats = interpret_multi(
        "upgrade pkg to 3.1", MULTI_NOTES, "pkg",
        chat_fn=_multi_fake, emit=lambda t, d: events.append((t, d)))
    by_symbol = {i.symbol: i for i in cs.items}
    assert set(by_symbol) == {"pkg.contextfunction", "pkg.quiet",
                              "pkg.oldthing", "pkg.listgone"}
    # Attribution follows the quote: both passes mislabeled the
    # release, verification fixed it.
    ctx_item = by_symbol["pkg.contextfunction"]
    assert ctx_item.source_url == "https://x/changes-3.1"
    assert ctx_item.introduced_in == "3.1.0"
    assert ctx_item.quote_offset >= 0
    assert ctx_item.verified_mode == "exact"
    assert ctx_item.confidence == "high"
    assert ctx_item.supporting.verified is True
    assert ctx_item.supporting.introduced_in == "3.1.0"
    assert by_symbol["pkg.oldthing"].introduced_in == "3.0.1"
    assert {(d.symbol, d.reason) for d in cs.dropped} == {
        ("pkg.bogus", "quote_not_found"),
        ("pkg.contextfunction", "duplicate"),
    }
    assert stats["releases"] == ["3.0.1", "3.1.0"]
    assert stats["recall"] == 1.0
    assert stats["blind_hits"] == 0
    assert stats["blind_recall"] == 0.0
    assert stats["survival_rate"] == 0.8
    assert stats["completed"] == 0
    assert stats["model_only"] == ["pkg.quiet"]
    assert stats["missed"] == []
    assert stats["det_by_method"] == {"table": 0, "list": 1,
                                      "pattern": 2}
    counts = stats["source_counts"]["https://x/changes-3.1"]
    assert counts["verified"] == 2
    assert stats["source_counts"]["https://x/changes-3.0"]["verified"] == 2
    assert any("deterministic rows" in d["message"] for _, d in events)
    assert any("blind pass done" in d["message"] for _, d in events)
    assert any("final change set" in d["message"] for _, d in events)


def test_interpret_multi_completes_missed_rows():
    from chainreaction.interpret import interpret_multi

    calls = []

    def fake(system, user, max_tokens=8000, response_format=None):
        calls.append(user)
        return json.dumps({"items": []})

    cs, stats = interpret_multi("d", MULTI_NOTES, "pkg", chat_fn=fake)
    assert len(calls) == 2  # blind + guided (rows exist)
    assert {i.symbol for i in cs.items} == {"pkg.contextfunction",
                                           "pkg.oldthing",
                                           "pkg.listgone"}
    assert {i.extraction for i in cs.items} == {"pattern", "list"}
    assert all(i.verified and i.quote_offset >= 0 for i in cs.items)
    by_symbol = {i.symbol: i for i in cs.items}
    assert by_symbol["pkg.contextfunction"].introduced_in == "3.1.0"
    assert by_symbol["pkg.oldthing"].introduced_in == "3.0.1"
    assert by_symbol["pkg.listgone"].introduced_in == "3.0.1"
    assert stats["completed"] == 3
    assert stats["blind_recall"] == 0.0
    assert stats["quote_misses"] == []
    assert stats["survival_rate"] is None


def test_interpret_multi_single_blind_call_without_rows():
    from chainreaction.interpret import interpret_multi

    calls = []

    def fake(system, user, max_tokens=8000, response_format=None):
        calls.append(user)
        return json.dumps({"items": []})

    notes = [{"version": "1.0", "source_url": "https://x/n",
              "text": "## 1.0\n\n- Add things.\n"}]
    _, stats = interpret_multi("d", notes, "pkg", chat_fn=fake)
    assert len(calls) == 1
    assert stats["blind_recall"] is None
    assert stats["recall"] is None


def test_interpret_multi_split_merges_earliest(monkeypatch):
    import chainreaction.interpret as interpret_mod
    from chainreaction.interpret import interpret_multi

    monkeypatch.setattr(interpret_mod, "MULTI_PROMPT_CHAR_LIMIT", 50)

    def fake(system, user, max_tokens=8000, response_format=None):
        if "3.0.1" in user:
            quote = "`oldthing` was removed, use `newthing` instead."
            items = [{"symbol": "pkg.oldthing", "kind": "removed",
                      "quote": quote, "replacement": None,
                      "source_url": "https://x/changes-3.0",
                      "introduced_in": "3.0.1"}]
        else:
            items = [{"symbol": "pkg.oldthing", "kind": "removed",
                      "quote": "`oldthing` was removed, use `newthing` instead.",
                      "replacement": None,
                      "source_url": "https://x/changes-3.1",
                      "introduced_in": "3.1.0"}]
        return json.dumps({"items": items})

    cs, _ = interpret_multi("d", MULTI_NOTES, "pkg", chat_fn=fake)
    by_symbol = {i.symbol: i for i in cs.items}
    # Earliest release kept; the later dup recorded.
    assert by_symbol["pkg.oldthing"].introduced_in == "3.0.1"
    assert ("pkg.oldthing", "duplicate") in {
        (d.symbol, d.reason) for d in cs.dropped}


def test_seed_exclusions_and_signature_seeds():
    from chainreaction.interpret import (
        ChangeSet,
        SignatureChange,
        seed_exclusions,
        signature_seeds,
    )

    cs = ChangeSet("d", "u", [
        ChangeItem("werkzeug.urls", "removed",
                   "Deprecate the `werkzeug.urls` module, except for the "
                   "`uri_to_iri` and `iri_to_uri` functions.",
                   verified=True),
        ChangeItem("pkg.fn", "signature_changed", "`fn` drops `gone`.",
                   verified=True,
                   signature=SignatureChange(["gone"], {"a": "b"}, None)),
        ChangeItem("pkg.plain", "signature_changed", "q", verified=True),
        ChangeItem("pkg.unverified", "removed", "except `x`.",
                   verified=False),
        ChangeItem("pkg.warn", "deprecated", "except `y`.", verified=True),
    ])
    assert seed_exclusions(cs) == {
        "werkzeug.urls": {"uri_to_iri", "iri_to_uri"}}
    assert signature_seeds(cs) == {
        "pkg.fn": {"kwargs": {"gone", "a"}, "min_positionals": None}}


def test_signature_changed_excluded_from_plain_symbols():
    from chainreaction.interpret import change_symbols

    cs = ChangeSet("d", "u", [
        ChangeItem("pkg.gone", "removed", "q", verified=True),
        ChangeItem("pkg.fn", "signature_changed", "q", verified=True),
    ])
    assert change_symbols(cs) == {"pkg.gone"}


def test_item_confidence_matrix():
    from chainreaction.interpret import item_confidence

    assert item_confidence("table", "removed", True) == "high"
    assert item_confidence("list", "deprecated", True) == "high"
    assert item_confidence("model", "removed", True) == "high"
    assert item_confidence("model", "renamed", True) == "high"
    assert item_confidence("model", "deprecated", True) == "medium"
    assert item_confidence("model", "signature_changed", True) == "medium"
    assert item_confidence("model", "changed", True) == "low"
    assert item_confidence("pattern", "removed", True) == "low"
    assert item_confidence("model", "removed", False) == "low"


def test_item_link_and_record():
    from chainreaction.interpret import item_link, item_record

    item = ChangeItem("pkg.a", "removed", "Remove the `a` helper now.",
                      verified=True, source_url="https://x/notes#frag")
    assert item_link(item) == (
        "https://x/notes#frag#:~:text=Remove%20the%20%60a%60%20helper%20now.")
    assert item_record(item)["link"] == item_link(item)
    assert item_record(ChangeItem("s", "removed", "")).get("link", "") == ""


def test_split_out_of_package_records():
    from chainreaction.interpret import split_out_of_package

    cs = ChangeSet("d", "u", [
        ChangeItem("fit.Provider", "rejected_value", "q", verified=True),
        ChangeItem("MODEL_API_KEY", "missing_param", "q", verified=True),
        ChangeItem("numpy.sctypes", "removed", "q", verified=True),
    ])
    kept, dropped = split_out_of_package(cs, "fit")
    assert [i.symbol for i in kept.items] == ["fit.Provider",
                                             "numpy.sctypes"]
    assert [(d.symbol, d.reason) for d in dropped] == [
        ("MODEL_API_KEY", "out_of_package")]


def test_prioritize_sections():
    from chainreaction.interpret import prioritize_sections

    text = ("## Version 2.0\n\n- boring.\n\n"
            "### Breaking changes\n\n- `gone` removed.\n\n"
            "### Features\n\n- shiny.\n")
    prio, rest = prioritize_sections(text)
    assert "`gone` removed." in prio
    assert "boring." in rest and "shiny." in rest
    assert "Breaking" not in rest


def test_interpret_multi_same_url_notes_attribute_per_note():
    from chainreaction.interpret import interpret_multi

    # Several notes from ONE changelog page: attribution must search
    # every note, not just one text per URL.
    notes = [
        {"version": "2.3.0", "source_url": "https://x/changes",
         "text": "## 2.3.0\n\n- `alpha` was removed.\n"},
        {"version": "3.0.0", "source_url": "https://x/changes",
         "text": "## 3.0.0\n\n- `beta` was removed.\n"},
    ]

    def fake(system, user, max_tokens=8000, response_format=None):
        return json.dumps({"items": [
            {"symbol": "pkg.alpha", "kind": "removed",
             "quote": "`alpha` was removed.", "replacement": None,
             "source_url": "https://x/changes",
             "introduced_in": "2.3.0"},
            {"symbol": "pkg.beta", "kind": "removed",
             "quote": "`beta` was removed.", "replacement": None,
             "source_url": "https://x/changes",
             "introduced_in": "3.0.0"},
        ]})

    cs, _ = interpret_multi("d", notes, "pkg", chat_fn=fake)
    by_symbol = {i.symbol: i for i in cs.items}
    assert set(by_symbol) == {"pkg.alpha", "pkg.beta"}
    assert by_symbol["pkg.alpha"].introduced_in == "2.3.0"
    assert by_symbol["pkg.beta"].introduced_in == "3.0.0"


def test_interpret_multi_no_bogus_dupes_without_rows():
    from chainreaction.interpret import interpret_multi

    def fake(system, user, max_tokens=8000, response_format=None):
        return json.dumps({"items": [
            {"symbol": "pkg.a", "kind": "removed",
             "quote": "`a` looks different.",
             "replacement": None, "source_url": "https://x/n",
             "introduced_in": "1.0"},
        ]})

    notes = [{"version": "1.0", "source_url": "https://x/n",
              "text": "## 1.0\n\n- `a` looks different.\n"}]
    cs, _ = interpret_multi("d", notes, "pkg", chat_fn=fake)
    assert [i.symbol for i in cs.items] == ["pkg.a"]
    assert cs.dropped == []
