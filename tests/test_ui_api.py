"""UI API tests (M6 endpoints; no targets or model key needed)."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from chainreaction.web import app  # noqa: E402

client = TestClient(app)


def test_status_shape():
    body = client.get("/api/status").json()
    assert set(body) == {"key_present", "live_available", "version"}
    assert isinstance(body["version"], str) and body["version"]
    assert body["live_available"] == body["key_present"]


def test_status_no_key_by_default(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    body = client.get("/api/status").json()
    assert body["key_present"] is False
    assert body["live_available"] is False


def test_status_with_key(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    body = client.get("/api/status").json()
    assert body["key_present"] is True
    assert body["live_available"] is True


def _disk_preset_ids():
    """Preset ids on disk (dirs with a readable manifest), sorted.

    Mirrors /api/presets discovery so new or re-recorded presets don't
    break the tests; the API must list exactly what's on disk.
    """
    from chainreaction.runs import store as run_store

    root = run_store.presets_dir()
    if not root.is_dir():
        return []
    ids = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        manifest = child / run_store.MANIFEST
        if not manifest.is_file():
            continue
        try:
            run_store.read_json(manifest)
        except (ValueError, OSError):
            continue
        ids.append(child.name)
    return ids


def _expected_files(preset_id, report):
    """File-overlap stats derived from report.json on disk."""
    import re

    pr_files = list((report or {}).get("pr_files") or [])
    if not pr_files:
        return None
    try:
        from chainreaction import scenarios

        behavior = set(scenarios.get(preset_id).pr_behavior)
    except KeyError:
        behavior = set((report or {}).get("file_behavior") or [])
    missing = set((report or {}).get("file_missing") or [])
    total = len(pr_files)
    behavior_in_pr = set(pr_files) & behavior
    breaking_total = total - len(behavior_in_pr)
    breaking_found = breaking_total - len(missing - behavior_in_pr)
    pr_url = (report or {}).get("pr_url") or ""
    match = re.search(r"/pull/(\d+)", pr_url)
    if match:
        pr_ref = f"PR #{match.group(1)}"
    else:
        pr_ref = "upstream PR" if pr_url else None
    return {
        "breaking_found": breaking_found,
        "breaking_total": breaking_total,
        "found": total - len(missing),
        "total": total,
        "pr_ref": pr_ref,
    }


def _expected_suite(scores):
    """Suite stats derived from scores.json on disk (None when absent)."""
    if not isinstance(scores, dict):
        return None
    actual_new = scores.get("actual_new")
    if not isinstance(actual_new, list):
        return None
    return {
        "new_failures": len(actual_new),
        "precision": scores.get("test_precision"),
        "recall": scores.get("test_recall"),
    }


def _expected_interpret(report):
    table_size = (report or {}).get("interpret_table_size") or 0
    missed = (report or {}).get("interpret_missed") or []
    return {
        "found": (table_size - len(missed)) if table_size else None,
        "table_size": table_size or None,
        "blind_hits": (report or {}).get("interpret_blind_hits"),
        "recall": (report or {}).get("interpret_recall"),
    }


def _expected_first_break(report):
    evidence = (report or {}).get("spine_evidence") or []
    if not evidence:
        return None
    hop = evidence[-1]
    if not isinstance(hop, (list, tuple)) or len(hop) < 4:
        return None
    return {"from": hop[0], "symbol": hop[1], "path": hop[2], "line": hop[3]}


def _expected_fix(fix):
    if not isinstance(fix, dict):
        return None
    rounds = fix.get("rounds") or []
    return {
        "rounds": len(rounds),
        "status": fix.get("final_status") or fix.get("status"),
    }


def test_presets_lists_all_on_disk():
    from chainreaction.generic import parse_decision
    from chainreaction.runs import store as run_store
    from chainreaction.ui_api import _repo_short

    body = client.get("/api/presets").json()
    expected_ids = _disk_preset_ids()
    assert expected_ids, "expected at least one preset on disk"
    assert [p["id"] for p in body] == expected_ids
    for preset in body:
        assert {"id", "title", "repo", "decision", "target",
                "decision_parsed", "has_recording", "headlines"} <= set(preset)
        directory = run_store.preset_dir(preset["id"])
        manifest = run_store.read_json(directory / run_store.MANIFEST)
        report_path = directory / run_store.ARTIFACTS / "report.json"
        report = (run_store.read_json(report_path)
                  if report_path.is_file() else None)
        assert preset["decision"] == (manifest.get("decision") or "")
        assert preset["repo"] == (manifest.get("repo_url") or "")
        assert preset["has_recording"] is (
            manifest.get("status") == "done" and report is not None)
        target = (report or {}).get("target") or ""
        assert preset["target"] == target
        assert preset["title"] == _repo_short(
            manifest.get("repo_url") or "", target, preset["id"])
        parsed = parse_decision(preset["decision"])
        expected_parsed = (None if parsed is None else
                           {"package": parsed.package,
                            "version": parsed.to_version})
        assert preset["decision_parsed"] == expected_parsed


def test_presets_file_breaking_stats():
    from chainreaction.runs import store as run_store

    body = {p["id"]: p for p in client.get("/api/presets").json()}
    assert body, "expected at least one preset on disk"
    for preset_id, preset in body.items():
        report_path = (run_store.preset_dir(preset_id)
                       / run_store.ARTIFACTS / "report.json")
        report = (run_store.read_json(report_path)
                  if report_path.is_file() else None)
        assert preset["headlines"]["files"] == _expected_files(
            preset_id, report)


def test_presets_suite_failures():
    from chainreaction.runs import store as run_store

    body = {p["id"]: p for p in client.get("/api/presets").json()}
    assert body, "expected at least one preset on disk"
    for preset_id, preset in body.items():
        scores_path = (run_store.preset_dir(preset_id)
                       / run_store.ARTIFACTS / "scores.json")
        scores = (run_store.read_json(scores_path)
                  if scores_path.is_file() else None)
        assert preset["headlines"]["suite"] == _expected_suite(scores)


def test_meta_usage_real_presets():
    body = client.get("/api/meta-usage").json()
    ids = [c["id"] for c in body["capabilities"]]
    # Search grounding is evidenced by searches.jsonl citations; tool
    # calling and 1M context appear once M4 lands and bigger prompts record.
    assert ids == ["structured-output", "search-grounding", "muse-code"]
    first = body["capabilities"][0]
    assert first["label"] == "Structured output"
    assert "change_set" in first["evidence"]
    grounded = body["capabilities"][1]
    assert "recorded calls" in grounded["evidence"]


def test_meta_usage_lights_up_with_evidence(tmp_path, monkeypatch):
    import json

    runs = tmp_path / "runs"
    preset = runs / "presets" / "ev"
    preset.mkdir(parents=True)
    calls = [
        {"params": {"tools": [{"type": "function", "name": "find_usages"}]},
         "usage": {"prompt_tokens": 10}},
        {"params": {}, "usage": {"prompt_tokens": 150000}},
    ]
    searches = [
        {"query": "numpy 2.0 migration",
         "citations": [{"url": "https://example.com/guide"}]},
        {"query": "nothing found", "citations": []},
    ]
    (preset / "llm_calls.jsonl").write_text(
        "\n".join(json.dumps(c) for c in calls) + "\n")
    (preset / "searches.jsonl").write_text(
        "\n".join(json.dumps(s) for s in searches) + "\n")
    (preset / "manifest.json").write_text(json.dumps({"status": "done"}))
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    body = client.get("/api/meta-usage").json()
    ids = [c["id"] for c in body["capabilities"]]
    assert ids == ["tool-calling", "search-grounding", "context-1m",
                   "muse-code"]
    # Only the cited search counts (no web_search tool entry present).
    grounded = [c for c in body["capabilities"]
                if c["id"] == "search-grounding"][0]
    assert grounded["evidence"] == "1 recorded calls"


def test_presets_headlines_match_report_files():
    from chainreaction.runs import store as run_store

    body = {p["id"]: p for p in client.get("/api/presets").json()}
    assert body, "expected at least one preset on disk"
    for preset_id, preset in body.items():
        directory = run_store.preset_dir(preset_id)
        manifest = run_store.read_json(directory / run_store.MANIFEST)
        assert preset["decision"] == (manifest.get("decision") or "")
        headlines = preset["headlines"]
        report_path = directory / run_store.ARTIFACTS / "report.json"
        if not report_path.is_file():
            assert headlines is None
            continue
        report = run_store.read_json(report_path)
        assert headlines["hard"] == len(report.get("hard") or [])
        assert headlines["at_risk"] == len(report.get("at_risk") or [])
        assert headlines["chain"] == list(report.get("spine") or [])
        assert headlines["chain_match"] == report.get("spine_match")
        assert headlines["first_break"] == _expected_first_break(report)
        assert headlines["interpret"] == _expected_interpret(report)
        assert headlines["files"] == _expected_files(preset_id, report)
        scores_path = directory / run_store.ARTIFACTS / "scores.json"
        scores = (run_store.read_json(scores_path)
                  if scores_path.is_file() else None)
        assert headlines["suite"] == _expected_suite(scores)
        fix_path = directory / run_store.ARTIFACTS / "fix.json"
        fix = run_store.read_json(fix_path) if fix_path.is_file() else None
        assert headlines["fix"] == _expected_fix(fix)
        # Session A fields: absent until recorded; UI hides them.
        assert headlines["test_groups"] == report.get("test_groups")
        assert headlines["test_prediction"] == report.get("test_prediction")
        assert headlines["vendor_chains"] == report.get("vendor_chains")


def test_presets_tolerate_missing_files(tmp_path, monkeypatch):
    import json

    from chainreaction.runs import store as run_store

    runs = tmp_path / "runs"
    (runs / "presets" / "empty").mkdir(parents=True)
    (runs / "presets" / "empty" / "manifest.json").write_text(
        json.dumps({"status": "done", "decision": "do X"}))
    (runs / "presets" / "no-manifest").mkdir()
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    body = client.get("/api/presets").json()
    assert [p["id"] for p in body] == ["empty"]
    assert body[0]["has_recording"] is False
    assert body[0]["headlines"] is None


def test_presets_fix_headlines_when_recorded(tmp_path, monkeypatch):
    import json

    runs = tmp_path / "runs"
    art = runs / "presets" / "fixed" / "artifacts"
    art.mkdir(parents=True)
    (runs / "presets" / "fixed" / "manifest.json").write_text(
        json.dumps({"status": "done", "decision": "do X",
                    "repo_url": "https://github.com/o/r"}))
    (art / "report.json").write_text(json.dumps(
        {"hard": ["a"], "spine_evidence": [["a", "b", "a.py", 1]]}))
    (art / "fix.json").write_text(json.dumps(
        {"rounds": [{}, {}], "final_status": "green"}))
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    body = client.get("/api/presets").json()
    assert body[0]["headlines"]["fix"] == {"rounds": 2, "status": "green"}
    assert body[0]["headlines"]["first_break"] == {
        "from": "a", "symbol": "b", "path": "a.py", "line": 1}


def test_parse_decision_versions():
    body = client.post(
        "/api/parse-decision",
        json={"decision": "upgrade werkzeug from 2.2 to 3.0"}).json()
    assert body == {"package": "werkzeug", "from_version": "2.2",
                    "to_version": "3.0"}
    body = client.post(
        "/api/parse-decision",
        json={"decision": "upgrade numpy to 2.0"}).json()
    assert body == {"package": "numpy", "from_version": "",
                    "to_version": "2.0"}
    body = client.post(
        "/api/parse-decision",
        json={"decision": "switch to Meta Model API"}).json()
    assert body == {"package": None, "from_version": "",
                    "to_version": None}
    body = client.post("/api/parse-decision", json={}).json()
    assert body["package"] is None


def test_preset_snapshot_for_feature_pages():
    body = client.get("/api/runs/preset:nibabel-numpy2").json()
    assert body["status"] == "done"
    assert body["manifest"]["preset"] == "nibabel-numpy2"
    assert body["report"]["decision"] == "upgrade numpy to 2.0"
    assert client.get("/api/runs/preset:nope").status_code == 404
    assert client.get("/api/runs/preset:..").status_code == 404


def test_main_help_and_unknown_command():
    from chainreaction.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert main(["bogus"]) == 2


def _source_fixture(tmp_path, monkeypatch):
    """Tmp preset whose target is a scratch dir under workspaces/.

    workspaces/ is gitignored, so the fixture never pollutes history; it
    is removed after the test.
    """
    import json
    import shutil

    from chainreaction.runs.scrub import ROOT

    target = ROOT / "workspaces" / "_test_src"
    target.mkdir(parents=True, exist_ok=True)
    (target / "a.py").write_text(
        "".join(f"line {i}\n" for i in range(1, 31)))
    runs = tmp_path / "runs"
    art = runs / "presets" / "fixed" / "artifacts"
    art.mkdir(parents=True)
    (runs / "presets" / "fixed" / "manifest.json").write_text(
        json.dumps({"status": "done", "decision": "do X"}))
    (art / "report.json").write_text(
        json.dumps({"target": "workspaces/_test_src"}))
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    try:
        yield
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_source_preset_lines(tmp_path, monkeypatch):
    for _ in _source_fixture(tmp_path, monkeypatch):
        body = client.get(
            "/api/runs/preset:fixed/source",
            params={"path": "a.py", "start": 5, "end": 7}).json()
        assert body["path"] == "a.py"
        assert body["total_lines"] == 30
        assert [(l["no"], l["text"]) for l in body["lines"]] == [
            (5, "line 5"), (6, "line 6"), (7, "line 7")]
        assert body["truncated"] is False


def test_source_rejects_escape_and_nonpy(tmp_path, monkeypatch):
    for _ in _source_fixture(tmp_path, monkeypatch):
        assert client.get("/api/runs/preset:fixed/source",
                          params={"path": "../a.py"}).status_code == 400
        assert client.get("/api/runs/preset:fixed/source",
                          params={"path": "/abs/a.py"}).status_code == 400
        assert client.get("/api/runs/preset:fixed/source",
                          params={"path": "a.txt"}).status_code == 400
        assert client.get("/api/runs/preset:fixed/source",
                          params={"path": "missing.py"}).status_code == 404
        assert client.get("/api/runs/preset:nope/source",
                          params={"path": "a.py"}).status_code == 404


def test_source_missing_target_is_404(tmp_path, monkeypatch):
    import json

    runs = tmp_path / "runs"
    art = runs / "presets" / "gone" / "artifacts"
    art.mkdir(parents=True)
    (runs / "presets" / "gone" / "manifest.json").write_text(
        json.dumps({"status": "done", "decision": "do X"}))
    (art / "report.json").write_text(
        json.dumps({"target": "targets/_does_not_exist"}))
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    resp = client.get("/api/runs/preset:gone/source",
                      params={"path": "a.py"})
    assert resp.status_code == 404


def test_artifact_preset_prefix():
    body = client.get(
        "/api/runs/preset:nibabel-numpy2/artifacts/report.json").json()
    assert body["decision"] == "upgrade numpy to 2.0"
    resp = client.get("/api/runs/preset:nope/artifacts/report.json")
    assert resp.status_code == 404


def test_artifact_optional_empty_not_404():
    # nibabel has no scores.json (generic-path runs only): optional
    # fetches get an empty 200, never a console 404.
    missing = client.get(
        "/api/runs/preset:nibabel-numpy2/artifacts/scores.json",
        params={"optional": "1"})
    assert missing.status_code == 200
    assert missing.headers["content-type"].startswith("application/json")
    assert missing.json() is None
    log = client.get(
        "/api/runs/preset:nibabel-numpy2/artifacts/verify/nope.log",
        params={"optional": "1"})
    assert log.status_code == 200 and log.text == ""
    # Real errors still surface; present files are unaffected.
    assert client.get(
        "/api/runs/preset:nibabel-numpy2/artifacts/scores.json"
    ).status_code == 404
    assert client.get(
        "/api/runs/preset:nope/artifacts/scores.json",
        params={"optional": "1"}).status_code == 404
    present = client.get(
        "/api/runs/preset:nibabel-numpy2/artifacts/report.json",
        params={"optional": "1"})
    assert present.json()["decision"] == "upgrade numpy to 2.0"


def test_build_tiles_break_to_root():
    from chainreaction.ui_api import build_tiles

    spine = ["pkg", "pkg.mid", "pkg.leaf"]
    evidence = [["pkg", "pkg.mid", "pkg/__init__.py", 3],
                ["pkg.mid", "pkg.leaf", "pkg/mid.py", 9],
                ["pkg.leaf", "pkg.leaf.f", "pkg/leaf.py", 40],
                ["pkg.leaf.f", "numpy.sctypes", "pkg/leaf.py", 37]]
    tiles = build_tiles(spine, evidence)
    assert [(t["module"], t["role"]) for t in tiles] == [
        ("pkg.leaf", "decision"), ("pkg.mid", "link"), ("pkg", "root")]
    assert (tiles[0]["file"], tiles[0]["line"]) == ("pkg/leaf.py", 37)
    assert tiles[0]["symbol"] == "numpy.sctypes"
    assert (tiles[1]["file"], tiles[1]["line"]) == ("pkg/mid.py", 9)
    assert (tiles[2]["file"], tiles[2]["line"]) == ("pkg/__init__.py", 3)
    assert build_tiles([], []) == []
    single = build_tiles(["pkg.only"], [])
    assert [(t["module"], t["role"]) for t in single] == [("pkg.only", "decision")]


def test_cluster_impacted_budgets_and_groups():
    from chainreaction.ui_api import cluster_impacted

    modules = {f"m{i:03d}": {"status": "HARD" if i % 3 else "AT_RISK",
                             "file": f"m{i:03d}.py"}
               for i in range(200)}
    modules["pkg"] = {"status": "HARD", "file": "pkg/__init__.py"}
    edges = [{"source": "pkg", "target": f"m{i:03d}"} for i in range(200)]
    edges += [{"source": "pkg", "target": "m002"}] * 3  # most connected
    out = cluster_impacted(modules, edges, ["pkg"])
    assert len(out["nodes"]) == 36  # chain + 35 neighbors
    assert out["nodes"][0]["id"] == "pkg"
    assert out["nodes"][1]["id"] == "m002"  # most chain edges first
    total = sum(c["count"] for c in out["clusters"])
    assert total == 201 - 36
    assert out["clusters"] == [
        {"id": "cluster:HARD", "status": "HARD", "count": 98,
         "label": "+98 more HARD"},
        {"id": "cluster:AT_RISK", "status": "AT_RISK", "count": 67,
         "label": "+67 more AT_RISK"}]
    small = cluster_impacted(
        {"a": {"status": "HARD", "file": "a.py"}}, [], ["a"])
    assert small["clusters"] == [] and len(small["nodes"]) == 1


def _graph_fixture(tmp_path, monkeypatch, n_modules):
    """Tmp preset + synthetic target package with n modules."""
    import json
    import shutil

    from chainreaction import ui_api
    from chainreaction.runs.scrub import ROOT

    ui_api.cached_repo_graph.cache_clear()
    target = ROOT / "workspaces" / "_test_graph"
    if target.exists():
        shutil.rmtree(target)
    pkg = target / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from . import mod000\n")
    for i in range(n_modules):
        nxt = f"from . import mod{i + 1:03d}\n" if i + 1 < n_modules else ""
        (pkg / f"mod{i:03d}.py").write_text(f"X = {i}\n{nxt}")
    runs = tmp_path / "runs"
    art = runs / "presets" / "gx" / "artifacts"
    art.mkdir(parents=True)
    (runs / "presets" / "gx" / "manifest.json").write_text(
        json.dumps({"status": "done", "decision": "do X"}))
    hard = ["mypkg"] + [f"mypkg.mod{i:03d}" for i in range(n_modules)]
    (art / "prediction.json").write_text(json.dumps({
        "hard": hard, "at_risk": [],
        "spine": ["mypkg", "mypkg.mod000"],
        "spine_evidence": [
            ["mypkg", "mypkg.mod000", "mypkg/__init__.py", 1]]}))
    (art / "report.json").write_text(json.dumps(
        {"target": "workspaces/_test_graph", "package": "mypkg"}))
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    try:
        yield
    finally:
        shutil.rmtree(target, ignore_errors=True)
        ui_api.cached_repo_graph.cache_clear()


def test_graph_endpoint_small(tmp_path, monkeypatch):
    for _ in _graph_fixture(tmp_path, monkeypatch, 3):
        body = client.get("/api/runs/preset:gx/graph").json()
        assert [t["module"] for t in body["tiles"]] == [
            "mypkg.mod000", "mypkg"]
        assert body["tiles"][0]["role"] == "decision"
        assert body["clusters"] == []
        assert body["counts"]["hard"] == 4
        assert {n["id"] for n in body["nodes"]} == {
            "mypkg", "mypkg.mod000", "mypkg.mod001", "mypkg.mod002"}
        assert any(e["kind"] == "IMPORTS" for e in body["edges"])


def test_graph_endpoint_clusters_and_full(tmp_path, monkeypatch):
    for _ in _graph_fixture(tmp_path, monkeypatch, 160):
        body = client.get("/api/runs/preset:gx/graph").json()
        # Chain (mypkg, mod000) + its one first-degree neighbor.
        assert len(body["nodes"]) == 3
        assert body["clusters"] == [
            {"id": "cluster:HARD", "status": "HARD", "count": 158,
             "label": "+158 more HARD"}]
        assert body["truncated"] is True
        full = client.get("/api/runs/preset:gx/graph",
                          params={"full": "1"}).json()
        assert len(full["nodes"]) == 161
        assert full["clusters"] == []


def test_graph_endpoint_function_nodes(tmp_path, monkeypatch):
    """Function-level AT_RISK hits render instead of an empty canvas.

    Regression for aiohttp-jinja2 ("showing 0 of 2"): the prediction
    names functions, so the graph must include function nodes, not
    just modules.
    """
    import json
    import shutil

    from chainreaction import ui_api
    from chainreaction.runs.scrub import ROOT

    ui_api.cached_repo_graph.cache_clear()
    target = ROOT / "workspaces" / "_test_graph_fn"
    if target.exists():
        shutil.rmtree(target)
    pkg = target / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "helpers.py").write_text(
        "def static_url():\n    return 1\n\n"
        "def url_for():\n    return 2\n")
    runs = tmp_path / "runs"
    art = runs / "presets" / "gx" / "artifacts"
    art.mkdir(parents=True)
    (runs / "presets" / "gx" / "manifest.json").write_text(
        json.dumps({"status": "done", "decision": "do X"}))
    (art / "prediction.json").write_text(json.dumps({
        "hard": [], "at_risk": ["mypkg.helpers.static_url",
                               "mypkg.helpers.url_for"],
        "spine": [], "spine_evidence": []}))
    (art / "report.json").write_text(json.dumps(
        {"target": "workspaces/_test_graph_fn", "package": "mypkg"}))
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    try:
        body = client.get("/api/runs/preset:gx/graph").json()
        assert body["tiles"] == []
        assert body["counts"]["at_risk"] == 2
        assert body["counts"]["shown"] == 2
        assert {n["id"] for n in body["nodes"]} == {
            "mypkg.helpers.static_url", "mypkg.helpers.url_for"}
        assert all(n["status"] == "AT_RISK" for n in body["nodes"])
    finally:
        shutil.rmtree(target, ignore_errors=True)
        ui_api.cached_repo_graph.cache_clear()


def test_graph_endpoint_behavior_tags(tmp_path, monkeypatch):
    """Nodes in report file_behavior files carry behavior=True for the filter."""
    import json
    import shutil

    from chainreaction import ui_api
    from chainreaction.runs.scrub import ROOT

    ui_api.cached_repo_graph.cache_clear()
    target = ROOT / "workspaces" / "_test_graph_beh"
    if target.exists():
        shutil.rmtree(target)
    pkg = target / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "alpha.py").write_text("X = 1\n")
    (pkg / "beta.py").write_text("Y = 2\n")
    runs = tmp_path / "runs"
    art = runs / "presets" / "gx" / "artifacts"
    art.mkdir(parents=True)
    (runs / "presets" / "gx" / "manifest.json").write_text(
        json.dumps({"status": "done", "decision": "do X"}))
    (art / "prediction.json").write_text(json.dumps({
        "hard": ["mypkg.alpha", "mypkg.beta"], "at_risk": [],
        "spine": [], "spine_evidence": []}))
    (art / "report.json").write_text(json.dumps(
        {"target": "workspaces/_test_graph_beh", "package": "mypkg",
         "file_behavior": ["mypkg/alpha.py"]}))
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    try:
        body = client.get("/api/runs/preset:gx/graph").json()
        by_id = {n["id"]: n for n in body["nodes"]}
        assert by_id["mypkg.alpha"]["behavior"] is True
        assert by_id["mypkg.beta"]["behavior"] is False
    finally:
        shutil.rmtree(target, ignore_errors=True)
        ui_api.cached_repo_graph.cache_clear()


def test_graph_endpoint_no_prediction_404(tmp_path, monkeypatch):
    import json

    runs = tmp_path / "runs"
    preset = runs / "presets" / "early"
    preset.mkdir(parents=True)
    (preset / "manifest.json").write_text(
        json.dumps({"status": "running", "decision": "do X"}))
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    resp = client.get("/api/runs/preset:early/graph")
    assert resp.status_code == 404


def test_export_md_and_json_match_artifacts():
    from chainreaction.runs import store as run_store

    base = run_store.preset_dir("nibabel-numpy2") / run_store.ARTIFACTS
    md = client.get("/api/runs/preset:nibabel-numpy2/export",
                    params={"format": "md"})
    assert md.headers["content-type"].startswith("text/markdown")
    assert "attachment" in md.headers["content-disposition"]
    assert "nibabel-numpy2" in md.headers["content-disposition"]
    assert md.content == (base / "report.md").read_bytes()
    js = client.get("/api/runs/preset:nibabel-numpy2/export",
                    params={"format": "json"})
    assert js.headers["content-type"].startswith("application/json")
    assert js.content == (base / "report.json").read_bytes()
    assert js.json()["decision"] == "upgrade numpy to 2.0"


def test_export_bundle_round_trips():
    import io
    import zipfile

    from chainreaction.runs import store as run_store

    resp = client.get("/api/runs/preset:nibabel-numpy2/export",
                      params={"format": "bundle"})
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["content-disposition"].endswith('.zip"')
    names = []
    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        for info in archive.infolist():
            names.append(info.filename)
            disk = (run_store.preset_dir("nibabel-numpy2")
                    / info.filename)
            assert archive.read(info.filename) == disk.read_bytes()
    assert "manifest.json" in names
    assert "artifacts/report.json" in names
    assert "artifacts/report.md" in names


def test_export_rejects_bad_format_and_missing():
    resp = client.get("/api/runs/preset:nibabel-numpy2/export",
                      params={"format": "pdf"})
    assert resp.status_code == 400
    resp = client.get("/api/runs/preset:nope/export",
                      params={"format": "md"})
    assert resp.status_code == 404


def test_export_missing_report_404(tmp_path, monkeypatch):
    import json

    runs = tmp_path / "runs"
    preset = runs / "presets" / "early"
    preset.mkdir(parents=True)
    (preset / "manifest.json").write_text(
        json.dumps({"status": "running", "decision": "do X"}))
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    assert client.get("/api/runs/preset:early/export",
                      params={"format": "md"}).status_code == 404
    assert client.get("/api/runs/preset:early/export",
                      params={"format": "json"}).status_code == 404


def test_provenance_counts_recordings(tmp_path, monkeypatch):
    import json

    runs = tmp_path / "runs"
    preset = runs / "presets" / "prov"
    preset.mkdir(parents=True)
    (preset / "manifest.json").write_text(
        json.dumps({"status": "done", "decision": "do X"}))
    (preset / "llm_calls.jsonl").write_text('{"a": 1}\n{"a": 2}\n\n')
    (preset / "cmds.jsonl").write_text('{"a": 1}\n')
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    body = client.get("/api/runs/preset:prov/provenance").json()
    assert body == {"llm_calls": 2, "cmds": 1, "fetches": 0}
    assert client.get("/api/runs/preset:nope/provenance").status_code == 404


def _claims_fixture(tmp_path, monkeypatch):
    """Tmp preset + 2-module package + report.json for /claims."""
    import json
    import shutil

    from chainreaction import ui_api
    from chainreaction.runs.scrub import ROOT

    ui_api.cached_repo_graph.cache_clear()
    target = ROOT / "workspaces" / "_test_claims"
    if target.exists():
        shutil.rmtree(target)
    pkg = target / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from . import mod000\n")
    (pkg / "mod000.py").write_text("X = 0\n")
    runs = tmp_path / "runs"
    art = runs / "presets" / "cx" / "artifacts"
    art.mkdir(parents=True)
    (runs / "presets" / "cx" / "manifest.json").write_text(
        json.dumps({"status": "done", "decision": "do X"}))
    (art / "report.json").write_text(json.dumps({
        "target": "workspaces/_test_claims", "package": "mypkg",
        "decision": "do X", "hard": ["mypkg", "mypkg.mod000"],
        "at_risk": [], "spine": ["mypkg", "mypkg.mod000"],
        "spine_evidence": [
            ["mypkg", "mypkg.mod000", "mypkg/__init__.py", 1]],
        "spine_match": True,
        "layers": [{"name": "layer1", "actual": ["mypkg", "mypkg.mod000"],
                    "missing": [], "recall": 1.0, "error_tail": "",
                    "ok": False, "failure_site": "mypkg/mod000.py:1"}],
        "narrative": "mypkg has 2 modules.",
        "change_items": [
            {"symbol": "mypkg.mod000", "kind": "rejected_value",
             "quote": "", "replacement": "", "detail": None,
             "verified": True, "source": "code",
             "code_quote": "X = 0", "code_file": "mypkg/mod000.py",
             "code_line": 1}],
    }))
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    try:
        yield
    finally:
        shutil.rmtree(target, ignore_errors=True)
        ui_api.cached_repo_graph.cache_clear()


def test_claims_shape_and_narrative(tmp_path, monkeypatch):
    for _ in _claims_fixture(tmp_path, monkeypatch):
        body = client.get("/api/runs/preset:cx/claims").json()
        assert body["claims"], "expected at least one claim check"
        for claim in body["claims"]:
            assert set(claim) == {"label", "status", "detail"}
            assert claim["status"] in ("pass", "fail", "pending")
        by_label = {c["label"]: c for c in body["claims"]}
        assert by_label["predicted nodes exist in graph"]["status"] == "pass"
        assert by_label["layer1: actual modules accounted for"][
            "status"] == "pass"
        assert "mypkg → mypkg.mod000" in by_label
        assert by_label["narrative module names"]["status"] == "pass"
        assert by_label["narrative numbers"]["status"] == "pass"


def test_claims_missing_report_404(tmp_path, monkeypatch):
    import json

    runs = tmp_path / "runs"
    preset = runs / "presets" / "early"
    preset.mkdir(parents=True)
    (preset / "manifest.json").write_text(
        json.dumps({"status": "running", "decision": "do X"}))
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    assert client.get("/api/runs/preset:early/claims").status_code == 404
    assert client.get("/api/runs/preset:nope/claims").status_code == 404


def _baselines_fixture(tmp_path, monkeypatch):
    """Tmp preset + package with one dirty source file, one clean, and
    one dirty test file (excluded from file scoring like the file score).
    """
    import json
    import shutil

    from chainreaction.runs.scrub import ROOT

    target = ROOT / "workspaces" / "_test_base"
    if target.exists():
        shutil.rmtree(target)
    pkg = target / "mypkg"
    (pkg / "tests").mkdir(parents=True)
    (pkg / "__init__.py").write_text("X = 0\n")
    (pkg / "a.py").write_text("x = sctypes\n")
    (pkg / "b.py").write_text("y = 1\n")
    (pkg / "tests" / "test_x.py").write_text("z = sctypes\n")
    runs = tmp_path / "runs"
    art = runs / "presets" / "bx" / "artifacts"
    art.mkdir(parents=True)
    (runs / "presets" / "bx" / "manifest.json").write_text(
        json.dumps({"status": "done", "decision": "do X"}))
    (art / "changeset.json").write_text(json.dumps({"items": [
        {"symbol": "numpy.sctypes", "kind": "removed",
         "verified": True}]}))
    (art / "report.json").write_text(json.dumps({
        "target": "workspaces/_test_base", "package": "mypkg",
        "pr_files": ["mypkg/a.py", "mypkg/b.py"],
        "pr_url": "https://github.com/o/r/pull/7"}))
    (runs / "presets" / "bx" / "cmds.jsonl").write_text(json.dumps({
        "cmd": ["<tool:ruff>", "check", "--select", "NPY201",
                "--output-format", "concise",
                "workspaces/_test_base/mypkg"],
        "missing": None,
        "stdout": ("workspaces/_test_base/mypkg/a.py:1:1: NPY201 hit\n"
                   "workspaces/_test_base/mypkg/tests/test_x.py:1:1: "
                   "NPY201 hit\n")}) + "\n")
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    try:
        yield
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_baseline_files_scores_vs_pr(tmp_path, monkeypatch):
    for _ in _baselines_fixture(tmp_path, monkeypatch):
        body = client.get("/api/runs/preset:bx/baseline-files").json()
        assert body["pr_files"] == ["mypkg/a.py", "mypkg/b.py"]
        assert body["pr_ref"] == "PR #7"
        # Both baselines hit a.py (+ the excluded test file): precision
        # 1.0, recall 0.5 against the two PR files.
        assert body["grep"] == {
            "hits": 2, "files": ["mypkg/a.py"],
            "precision": 1.0, "recall": 0.5}
        assert body["ruff"] == {
            "hits": 2, "files": ["mypkg/a.py"],
            "precision": 1.0, "recall": 0.5}


def test_baseline_files_nulls_and_404s(tmp_path, monkeypatch):
    import json

    runs = tmp_path / "runs"
    preset = runs / "presets" / "thin"
    preset.mkdir(parents=True)
    (preset / "manifest.json").write_text(
        json.dumps({"status": "done", "decision": "do X"}))
    (preset / "artifacts").mkdir()
    (preset / "artifacts" / "report.json").write_text(json.dumps({
        "target": "workspaces/_does_not_exist", "package": "mypkg",
        "pr_files": ["mypkg/a.py"],
        "changeset": None}))
    (preset / "artifacts" / "changeset.json").write_text(
        json.dumps({"items": []}))
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(runs))
    body = client.get("/api/runs/preset:thin/baseline-files").json()
    assert body["grep"] is None  # no checkout
    assert body["ruff"] is None  # no recording
    assert body["pr_ref"] is None
    nopr = runs / "presets" / "nopr"
    nopr.mkdir(parents=True)
    (nopr / "manifest.json").write_text(
        json.dumps({"status": "done", "decision": "do X"}))
    (nopr / "artifacts").mkdir()
    (nopr / "artifacts" / "report.json").write_text(
        json.dumps({"target": "x", "package": "mypkg", "pr_files": []}))
    assert client.get("/api/runs/preset:nopr/baseline-files").status_code == 404
    assert client.get(
        "/api/runs/preset:nope/baseline-files").status_code == 404
