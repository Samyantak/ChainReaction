"""M3 §1-§2 tests: search gateway, source discovery, release collection.

Discovery and collection run against hermetic RunContexts (fake fetch
map + fake search); one test replays a full discovery live→replay to
prove probe determinism.
"""

import json
import urllib.error
from types import SimpleNamespace as NS

import pytest

from chainreaction import llm as llm_mod
from chainreaction import sources as sources_mod
from chainreaction.generic import GuideNotFound
from chainreaction.runs import store
from chainreaction.runs.context import ReplayMiss, RunContext
from chainreaction.runs import context as context_mod


@pytest.fixture()
def tmp_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(tmp_path / "runs"))
    return tmp_path / "runs"


def _live_manifest(run_id, **fields):
    manifest = {"run_id": run_id, "mode": "live",
                "model": llm_mod.model_name(), "status": "running"}
    manifest.update(fields)
    store.write_json(store.run_dir(run_id) / store.MANIFEST, manifest)
    return manifest


def _live_ctx(run_id, **hooks):
    return RunContext(run_id=run_id, manifest=_live_manifest(run_id),
                      mode="live", **hooks)


class _FetchMap:
    """live_fetch hook: URL -> (status, raw); default 404."""

    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        return self.mapping.get(url, (404, b""))


def _search_result(citations, text="search reply", searches=1):
    return llm_mod.WebSearchResult(
        text, [llm_mod.WebCitation(url, title, span)
               for url, title, span in citations],
        searches, "fake-model", None, False, None)


PAD = ("padding prose to exceed the minimum usable length threshold "
       "for discovery sources. " * 6)

GUIDE_HTML = ("<html><body><h1>Migration guide</h1><p>migration guide "
              f"for pkg with plenty of quotable prose. {PAD}</p></body></html>")

CHANGELOG_MD = (f"# Changelog\n\n## 3.1.0 (2022-03-28)\n\n"
                f"- `contextfunction` replaced by `pass_context`. {PAD}\n\n"
                f"## 3.0.0\n\n- old notes. {PAD}\n")

RELEASES_JSON = [
    {"tag_name": "v3.1.0", "html_url": "https://github.com/org/pkg/r1",
     "body": f"## 3.1.0\n\n- big change. {PAD}"},
    {"tag_name": "v3.0.0", "html_url": "https://github.com/org/pkg/r0",
     "body": f"## 3.0.0\n\n- old change. {PAD}"},
]


def _pypi_body(project_urls=None, releases=None, home=None):
    return json.dumps({
        "info": {"home_page": home or "https://github.com/org/pkg",
                 "project_urls": project_urls or {}},
        "releases": releases or {"2.9.0": [], "3.0.0": [], "3.1.0": []},
    }).encode()


PYPI_URL = "https://pypi.org/pypi/pkg/json"
API_URL = "https://api.github.com/repos/org/pkg/releases"
RAW_CHANGELOG = ("https://raw.githubusercontent.com/org/pkg/3.1.0/"
                 "CHANGELOG.md")


# -- web_search gateway --

def test_web_search_records_and_replays(tmp_runs):
    live = _live_ctx("u1", live_search=lambda q: _search_result(
        [("https://a/", "A", "span a")], text="reply text"))
    res = live.web_search("some query")
    assert res.text == "reply text"
    assert [(c.url, c.span) for c in res.citations] == \
        [("https://a/", "span a")]
    recs = store.read_jsonl(store.run_dir("u1") / store.SEARCHES)
    assert [r["query"] for r in recs] == ["some query"]
    assert recs[0]["citations"][0]["url"] == "https://a/"

    replay = RunContext(run_id="u2", manifest=_live_manifest("u2"),
                        mode="replay", source_dir=store.run_dir("u1"))
    again = replay.web_search("some query")
    assert again.text == "reply text"
    assert [(c.url, c.span) for c in again.citations] == \
        [("https://a/", "span a")]
    assert replay.replay_warnings() == []


def test_web_search_replay_miss(tmp_runs):
    live = _live_ctx("u1", live_search=lambda q: _search_result([]))
    live.web_search("q1")
    replay = RunContext(run_id="u2", manifest=_live_manifest("u2"),
                        mode="replay", source_dir=store.run_dir("u1"))
    with pytest.raises(ReplayMiss):
        replay.web_search("other query")


def test_replay_warnings_include_unused_search(tmp_runs):
    live = _live_ctx("u1", live_search=lambda q: _search_result([]))
    live.web_search("q1")
    replay = RunContext(run_id="u2", manifest=_live_manifest("u2"),
                        mode="replay", source_dir=store.run_dir("u1"))
    assert replay.replay_warnings() == [
        "1 unused search recording(s); "
        "the stages diverged from the recording"]


def test_message_texts_parses_citations_and_tolerates_drift():
    output = [
        NS(type="web_search_call", status="completed"),
        NS(type="reasoning"),
        NS(type="message", content=[
            NS(text="Latest is 2.5.4 per PyPI.", annotations=[
                NS(type="url_citation",
                   url="https://pypi.org/project/numpy/2.5.4/",
                   title="numpy · PyPI", start_index=20, end_index=24),
                NS(type="other", url="https://x/"),
                NS(type="url_citation", url="", title="t",
                   start_index=0, end_index=1),
                NS(type="url_citation", url="https://bad/",
                   title="b", start_index="x", end_index=None),
            ]),
            NS(text="", annotations=None),
        ]),
        NS(type="message", content=None),
    ]
    text, citations, searches = llm_mod._message_texts(output)
    assert text == "Latest is 2.5.4 per PyPI."
    assert searches == 1
    assert [(c["url"], c["span"]) for c in citations] == [
        ("https://pypi.org/project/numpy/2.5.4/", "PyPI"),
        ("https://bad/", ""),
    ]


def test_web_search_serves_cache_without_key(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    query = "cache-probe-query-xyz"
    key = llm_mod.cache_key_for(llm_mod.model_name(), "web_search:v1",
                                query, None)
    llm_mod._write_cache(key, json.dumps({
        "text": "cached reply",
        "citations": [{"url": "https://u/", "title": "T", "span": "s"}],
        "searches": 2, "usage": None}))
    res = llm_mod.web_search(query)
    assert res.cached and res.text == "cached reply"
    assert res.searches == 2
    assert res.citations[0].url == "https://u/"


# -- fetch: clean=text + recorded HTTP errors + provenance --

def test_fetch_clean_text_preserves_newlines(tmp_runs):
    body = CHANGELOG_MD.encode()
    live = _live_ctx("u1", live_fetch=_FetchMap(
        {"https://x/cl.md": (200, body)}))
    fetched = live.fetch("https://x/cl.md", clean="text")
    assert "## 3.1.0" in fetched and "\n" in fetched
    assert fetched.removal_table == {"removed": [], "deprecated": []}
    recs = store.read_jsonl(store.run_dir("u1") / store.FETCHES)
    assert recs[0]["clean"] == "text"
    replay = RunContext(run_id="u2", manifest=_live_manifest("u2"),
                        mode="replay", source_dir=store.run_dir("u1"))
    assert str(replay.fetch("https://x/cl.md")) == str(fetched)


def test_fetch_records_http_error_status(tmp_runs, monkeypatch):
    def boom(url, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(context_mod, "fetch_page", boom)
    live = _live_ctx("u1")
    fetched = live.fetch("https://x/missing")
    assert fetched.status == 404
    assert str(fetched) == ""
    recs = store.read_jsonl(store.run_dir("u1") / store.FETCHES)
    assert recs[0]["status"] == 404 and recs[0]["text"] == ""
    replay = RunContext(run_id="u2", manifest=_live_manifest("u2"),
                        mode="replay", source_dir=store.run_dir("u1"))
    again = replay.fetch("https://x/missing")
    assert again.status == 404 and str(again) == ""


def test_fetch_result_carries_provenance(tmp_runs):
    raw = b"<html><body><p>hello guide</p></body></html>"
    live = _live_ctx("u1", live_fetch=_FetchMap({"https://x/g": (200, raw)}))
    fetched = live.fetch("https://x/g")
    assert fetched.status == 200
    assert fetched.bytes == len(raw)
    import hashlib
    assert fetched.sha256 == hashlib.sha256(raw).hexdigest()
    replay = RunContext(run_id="u2", manifest=_live_manifest("u2"),
                        mode="replay", source_dir=store.run_dir("u1"))
    again = replay.fetch("https://x/g")
    assert (again.status, again.bytes, again.sha256) == \
        (fetched.status, fetched.bytes, fetched.sha256)


# -- discovery --

def _ctx(fetch_map, citations="raise"):
    if citations == "raise":
        def fail_search(query):
            raise AssertionError("search must not run")
        search = fail_search
    else:
        search = lambda query: _search_result(citations)  # noqa: E731
    return _live_ctx("u1", live_fetch=_FetchMap(fetch_map),
                     live_search=search)


def test_discover_guide_url_first(tmp_runs):
    ctx = _ctx({PYPI_URL: (200, _pypi_body()),
                "https://guide/": (200, GUIDE_HTML.encode())})
    found = sources_mod.discover_sources(ctx, "pkg", "3.0.0", "3.1.0",
                                         guide_url="https://guide/")
    assert found.guide_url == "https://guide/"
    assert "migration guide" in found.texts["https://guide/"].lower()
    assert [s.found_via for s in found.sources] == ["pypi_meta",
                                                   "guide_url"]


def test_discover_search_skips_off_domain(tmp_runs):
    ctx = _ctx(
        {PYPI_URL: (200, _pypi_body())},
        citations=[("https://evil.com/notes", "E", "s"),
                   ("https://org.github.io/pkg/changelog", "C", "s")])
    with pytest.raises(GuideNotFound) as excinfo:
        sources_mod.discover_sources(ctx, "pkg", "3.0.0", "3.1.0")
    message = str(excinfo.value)
    assert "skip off-domain https://evil.com/notes" in message
    assert "skip off-domain https://org.github.io/pkg/changelog" in message


def test_discover_search_uses_on_domain_citation(tmp_runs):
    changelog_url = "https://github.com/org/pkg/blob/main/CHANGELOG.md"
    ctx = _ctx(
        {PYPI_URL: (200, _pypi_body()),
         changelog_url: (200, CHANGELOG_MD.encode())},
        citations=[("https://evil.com/notes", "E", "s"),
                   (changelog_url, "C", "s")])
    found = sources_mod.discover_sources(ctx, "pkg", "3.0.0", "3.1.0")
    assert changelog_url in found.texts
    assert "## 3.1.0" in found.texts[changelog_url]
    assert any("skip off-domain https://evil.com/notes" in t
               for t in found.tried)
    assert "https://evil.com/notes" not in [
        s.url for s in found.sources]


def test_discover_search_failure_falls_through_to_pypi(tmp_runs):
    changelog_url = "https://github.com/org/pkg/blob/main/CHANGELOG.md"

    def fail_search(query):
        raise RuntimeError("no key")

    ctx = _live_ctx("u1", live_fetch=_FetchMap(
        {PYPI_URL: (200, _pypi_body(
            project_urls={"Changelog": changelog_url})),
         changelog_url: (200, CHANGELOG_MD.encode())}),
        live_search=fail_search)
    found = sources_mod.discover_sources(ctx, "pkg", "3.0.0", "3.1.0")
    assert changelog_url in found.texts
    assert any(t.startswith("search: RuntimeError") for t in found.tried)


def test_discover_github_releases(tmp_runs):
    ctx = _ctx({PYPI_URL: (200, _pypi_body()),
                API_URL: (200, json.dumps(RELEASES_JSON).encode())},
               citations=[])
    found = sources_mod.discover_sources(ctx, "pkg", "3.0.0", "3.1.0")
    assert [e["tag"] for e in found.github_releases] == ["v3.1.0", "v3.0.0"]
    assert any(s.found_via == "github_releases" and s.used
               for s in found.sources)


def test_discover_repo_file(tmp_runs):
    ctx = _ctx({PYPI_URL: (200, _pypi_body()),
                API_URL: (404, b""),
                RAW_CHANGELOG: (200, CHANGELOG_MD.encode())},
               citations=[])
    found = sources_mod.discover_sources(ctx, "pkg", "3.0.0", "3.1.0")
    assert RAW_CHANGELOG in found.texts
    assert any(s.found_via == "repo_file" for s in found.sources)


def test_discover_guide_not_found_lists_tried(tmp_runs):
    ctx = _ctx({PYPI_URL: (200, _pypi_body(releases={"3.1.0": []}))},
               citations=[])
    with pytest.raises(GuideNotFound) as excinfo:
        sources_mod.discover_sources(ctx, "pkg", "3.0.0", "3.1.0")
    message = str(excinfo.value)
    assert "pkg 3.1.0" in message
    assert "github releases" in message


def test_discovery_collection_replay_identical(tmp_runs):
    dead_url = "https://github.com/org/pkg/blob/main/OLD.md"
    good_url = "https://github.com/org/pkg/blob/main/CHANGELOG.md"
    fetch_map = {PYPI_URL: (200, _pypi_body()),
                 dead_url: (404, b""),
                 good_url: (200, CHANGELOG_MD.encode())}
    citations = [(dead_url, "D", "s"), (good_url, "G", "s")]
    live = _live_ctx("u1", live_fetch=_FetchMap(fetch_map),
                     live_search=lambda q: _search_result(citations))
    found_live = sources_mod.discover_sources(live, "pkg", "3.0.0", "3.1.0")
    notes_live = sources_mod.collect_release_notes(live, found_live)

    replay = RunContext(run_id="u2", manifest=_live_manifest("u2"),
                        mode="replay", source_dir=store.run_dir("u1"))
    found_replay = sources_mod.discover_sources(replay, "pkg", "3.0.0",
                                                "3.1.0")
    notes_replay = sources_mod.collect_release_notes(replay, found_replay)

    assert found_replay.texts == found_live.texts
    assert [(n.version, n.source_url, n.text) for n in notes_replay] == \
        [(n.version, n.source_url, n.text) for n in notes_live]
    assert [n.version for n in notes_live] == ["3.1.0"]
    assert replay.replay_warnings() == []
    assert (store.run_dir("u1") / store.SEARCHES).is_file()


def test_discover_continues_until_covered(tmp_runs):
    page31 = ("https://github.com/org/pkg/blob/main/CHANGES-3.1.md",
              (f"# Changes\n\n## 3.1.0\n\n- new. {PAD}\n").encode())
    page30 = ("https://github.com/org/pkg/blob/main/CHANGES-3.0.md",
              (f"# Changes\n\n## 3.0.1\n\n- fix. {PAD}\n").encode())
    ctx = _live_ctx(
        "u1",
        live_fetch=_FetchMap({
            PYPI_URL: (200, _pypi_body(
                project_urls={"Changelog": page30[0]},
                releases={"3.0.0": [], "3.0.1": [], "3.1.0": []})),
            page31[0]: (200, page31[1]),
            page30[0]: (200, page30[1]),
        }),
        live_search=lambda q: _search_result([(page31[0], "C", "s")]))
    found = sources_mod.discover_sources(ctx, "pkg", "3.0.0", "3.1.0")
    # Step 2's page covered only 3.1.0, so step 3 ran for 3.0.1.
    assert set(found.texts) == {page31[0], page30[0]}
    notes = sources_mod.collect_release_notes(ctx, found)
    assert [n.version for n in notes] == ["3.0.1", "3.1.0"]


def test_discover_partial_coverage_returns(tmp_runs):
    page31 = ("https://github.com/org/pkg/blob/main/CHANGES-3.1.md",
              (f"# Changes\n\n## 3.1.0\n\n- new. {PAD}\n").encode())
    ctx = _live_ctx(
        "u1",
        live_fetch=_FetchMap({
            PYPI_URL: (200, _pypi_body(
                releases={"3.0.0": [], "3.0.1": [], "3.1.0": []})),
            API_URL: (200, b"[]"),
            page31[0]: (200, page31[1]),
        }),
        live_search=lambda q: _search_result([(page31[0], "C", "s")]))
    found = sources_mod.discover_sources(ctx, "pkg", "3.0.0", "3.1.0")
    assert set(found.texts) == {page31[0]}
    assert ("partial coverage: missing notes for 3.0.1"
            in found.tried)


# -- collection --

def _discovery(**fields):
    base = {"package": "pkg", "from_version": "3.0.0",
            "to_version": "3.1.0", "sources": [], "tried": [],
            "pypi": {}, "github_releases": [], "texts": {},
            "guide_url": None}
    base.update(fields)
    return sources_mod.Discovery(**base)


def test_wanted_releases_range_and_prereleases(tmp_runs):
    ctx = _live_ctx("u1")
    pypi = {"releases": {"3.0.0": [], "3.1.0a1": [], "3.1.0": [],
                         "3.2.0": [], "bogus": []}}
    found = _discovery(pypi=pypi)
    assert sources_mod._wanted_releases(found) == ["3.1.0"]
    pre = _discovery(pypi=pypi, to_version="3.1.0a1")
    assert sources_mod._wanted_releases(pre) == ["3.1.0a1"]
    no_pypi = _discovery(pypi={})
    assert sources_mod._wanted_releases(no_pypi) == ["3.1.0"]
    assert sources_mod.collect_release_notes(ctx, no_pypi) == []


def test_collect_github_per_release(tmp_runs):
    ctx = _live_ctx("u1")
    pypi = json.loads(_pypi_body().decode())
    found = _discovery(
        pypi=pypi, github_releases=sources_mod._parse_releases(json.dumps(RELEASES_JSON)),
        sources=[sources_mod.Source(API_URL, "github_releases", 10,
                                    "ab" * 32, used=True)])
    notes = sources_mod.collect_release_notes(ctx, found)
    assert [(n.version, n.source_url) for n in notes] == [
        ("3.1.0", "https://github.com/org/pkg/r1")]
    assert "big change" in notes[0].text


def test_collect_splits_changelog(tmp_runs):
    ctx = _live_ctx("u1")
    pypi = json.loads(_pypi_body().decode())
    url = "https://github.com/org/pkg/blob/main/CHANGELOG.md"
    found = _discovery(pypi=pypi, texts={url: CHANGELOG_MD},
                       sources=[sources_mod.Source(url, "search", 10,
                                                   "ab" * 32)])
    notes = sources_mod.collect_release_notes(ctx, found)
    assert [(n.version, n.source_url) for n in notes] == [("3.1.0", url)]
    assert "contextfunction" in notes[0].text
    assert "3.0.0" not in notes[0].text  # sliced at the next heading
    assert found.sources[0].used


def test_collect_github_wins_over_changelog(tmp_runs):
    ctx = _live_ctx("u1")
    pypi = json.loads(_pypi_body().decode())
    url = "https://x/cl.md"
    found = _discovery(pypi=pypi, github_releases=sources_mod._parse_releases(json.dumps(RELEASES_JSON)),
                       texts={url: CHANGELOG_MD},
                       sources=[sources_mod.Source(API_URL,
                                                   "github_releases", 10,
                                                   "ab" * 32, used=True)])
    notes = sources_mod.collect_release_notes(ctx, found)
    assert [n.source_url for n in notes] == [
        "https://github.com/org/pkg/r1"]


def test_collect_guide_covers_jump(tmp_runs):
    ctx = _live_ctx("u1")
    pypi = json.loads(_pypi_body().decode())
    guide = "https://guide/"
    found = _discovery(pypi=pypi, guide_url=guide,
                       texts={guide: "guide body " + PAD},
                       sources=[sources_mod.Source(guide, "guide_url", 10,
                                                   "ab" * 32)])
    notes = sources_mod.collect_release_notes(ctx, found)
    assert [(n.version, n.source_url) for n in notes] == [("3.1.0", guide)]
    assert found.sources[0].used


def test_sources_record_shape(tmp_runs):
    found = _discovery(
        sources=[sources_mod.Source("https://x/", "search", 12, "cd" * 32,
                                    used=True, clean="text", note="")],
        tried=["search: 1 citation(s)"])
    record = sources_mod.sources_record(found)
    assert record["package"] == "pkg"
    assert record["sources"] == [
        {"url": "https://x/", "found_via": "search", "bytes": 12,
         "sha256": "cd" * 32, "used": True, "clean": "text", "note": ""}]
    assert record["tried"] == ["search: 1 citation(s)"]


# -- pure text helpers --

def test_split_changelog_heading_forms():
    text = ("\n".join([
        "# Changelog",
        "",
        "## [3.1.0] - 2022-03-28",
        "",
        "- bracket form",
        "",
        "## 3.0.1",
        "",
        "- plain form",
        "",
        "Version 3.0.0",
        "-------------",
        "",
        "- rst form",
        "",
        "2.9.0 (2021-01-01)",
        "",
        "- bare form",
        "",
        "3.14 is just prose, not a heading",
        "",
    ]))
    sections = sources_mod.split_changelog(
        text, ["3.1.0", "3.0.1", "3.0.0", "2.9.0"])
    assert set(sections) == {"3.1.0", "3.0.1", "3.0.0", "2.9.0"}
    assert "bracket form" in sections["3.1.0"]
    assert "plain form" in sections["3.0.1"]
    assert "rst form" in sections["3.0.0"]
    assert "bare form" in sections["2.9.0"]
    # The prose line is section content, not a boundary.
    assert "prose" in sections["2.9.0"]


def test_heading_version_tolerates_pilcrow():
    from packaging.version import Version
    assert sources_mod._heading_version("## Version 3.1.0¶") == \
        Version("3.1.0")


def test_structured_text_skips_headerlinks():
    html = ("<html><body><h2>Version 3.1.0"
            '<a class="headerlink" href="#v" title="x">¶</a></h2>'
            "<p>notes</p></body></html>")
    assert sources_mod.coerce_text(html) == \
        "## Version 3.1.0\n\nnotes"


def test_coerce_text_html_and_markdown():
    html = ("<html><body><h2>Version 3.1.0</h2><ul><li><code>f</code> "
            "gone</li></ul><script>var x = 1;</script></body></html>")
    assert sources_mod.coerce_text(html) == \
        "## Version 3.1.0\n\n- `f` gone"
    assert sources_mod.coerce_text("## 3.1.0\r\n- x\r\n") == "## 3.1.0\n- x\n"


def test_on_domain_hosts_orgs_subdomains():
    pypi = {"info": {
        "home_page": "https://github.com/Pallets/jinja",
        "project_urls": {"Documentation": "https://jinja.palletsprojects.com",
                         "Code": "https://github.com/pallets/jinja"}}}
    hosts, orgs = sources_mod._package_hosts(pypi)
    assert orgs == {"pallets"}
    assert sources_mod._on_domain(
        "https://github.com/pallets/jinja/blob/main/CHANGES.rst",
        hosts, orgs)
    assert sources_mod._on_domain(
        "https://raw.githubusercontent.com/pallets/jinja/3.1/CHANGES",
        hosts, orgs)
    assert sources_mod._on_domain("https://jinja.palletsprojects.com/x",
                                  hosts, orgs)
    assert not sources_mod._on_domain("https://github.com/other/r", hosts,
                                      orgs)
    assert not sources_mod._on_domain("https://evil.com/x", hosts, orgs)


def test_structured_text_preserves_code_spans():
    html = ("<html><body><ul><li>Deprecate the <code>pkg.mod</code> "
            "module, except <code>keep</code>.</li></ul></body></html>")
    assert sources_mod.coerce_text(html) == \
        "- Deprecate the `pkg.mod` module, except `keep`."
