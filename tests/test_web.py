"""Backend API tests (live pipeline; skipped when fastapi is missing)."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from chainreaction.web import app  # noqa: E402

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json() == {"ok": True}


def test_scenarios_lists_nibabel():
    body = client.get("/api/scenarios").json()
    assert body[0]["id"] == "nibabel-numpy2"


def test_graph_marks_spine_hard():
    body = client.get("/api/graph?scenario=nibabel-numpy2").json()
    status = {n["id"]: n["status"] for n in body["nodes"]}
    for module in body["spine"]:
        assert status[module] == "HARD"
    assert any(e["kind"] == "IMPORTS" for e in body["edges"])
    assert all(e["line"] >= 1 for e in body["edges"])


def test_report_matches_pipeline():
    body = client.get("/api/report?scenario=nibabel-numpy2").json()
    assert body["decision"] == "upgrade numpy to 2.0"
    assert body["numpy_version"] == "2.0.2"
    assert body["before_numpy"] == "1.26.4"
    assert body["layers"][0]["recall"] == 1.0
    assert body["layers"][1]["recall"] == 1.0
    before = body["layers"][2]
    assert before["ok"] is True and before["actual"] == []


def test_unknown_scenario_404s():
    assert client.get("/api/report?scenario=nope").status_code == 404
