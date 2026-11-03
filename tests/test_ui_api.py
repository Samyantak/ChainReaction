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


def test_presets_lists_all_three_on_disk():
    body = client.get("/api/presets").json()
    assert [p["id"] for p in body] == [
        "anndata-numpy2", "nibabel-numpy2", "sponsorfit-meta"]
    for preset in body:
        assert {"id", "title", "repo", "decision", "target",
                "has_recording", "headlines"} <= set(preset)
        assert preset["has_recording"] is True
        assert "×" in preset["title"] and preset["decision"]


def test_presets_headlines_match_report_files():
    from chainreaction.runs import store as run_store

    body = {p["id"]: p for p in client.get("/api/presets").json()}
    nibabel = body["nibabel-numpy2"]["headlines"]
    report = run_store.read_json(
        run_store.preset_dir("nibabel-numpy2") / "artifacts" / "report.json")
    assert nibabel["hard"] == len(report["hard"])
    assert nibabel["at_risk"] == len(report["at_risk"])
    assert nibabel["chain"] == report["spine"]
    assert nibabel["chain_match"] == report["spine_match"]
    assert nibabel["first_break"] == {
        "from": "nibabel.casting.ok_floats", "symbol": "numpy.sctypes",
        "path": "nibabel/casting.py", "line": 717}
    assert nibabel["fix"] is None  # M5 hasn't recorded yet; UI hides it
    assert nibabel["test_groups"] is None  # Session A hasn't added it yet
    # Generic-path recording (no scenarios.py entry) still listed.
    assert body["anndata-numpy2"]["decision"] == "upgrade numpy to 2.0"
    assert body["anndata-numpy2"]["headlines"]["interpret"] == {
        "found": 62, "table_size": 62, "blind_hits": 9, "recall": 1.0}


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


def test_main_help_and_unknown_command():
    from chainreaction.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert main(["bogus"]) == 2
