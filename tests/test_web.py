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
    assert body["pr_url"].endswith("/pull/1250")
    assert body["file_precision"] == pytest.approx(1.00)
    assert body["file_recall"] == pytest.approx(5 / 8)
    assert body["file_missing"] == [
        "nibabel/conftest.py",
        "nibabel/freesurfer/io.py",
        "nibabel/spatialimages.py",
    ]
    assert body["file_behavior"] == body["file_missing"]


def test_report_includes_per_claim_checks():
    body = client.get("/api/report?scenario=nibabel-numpy2").json()
    checks = body["claim_checks"]
    assert any("nibabel → nibabel.analyze" in c["label"] for c in checks)
    assert {c["status"] for c in checks} <= {"pass", "pending"}
    assert any(c["status"] == "pending" for c in checks)  # no narrative yet


def test_unknown_scenario_404s():
    assert client.get("/api/report?scenario=nope").status_code == 404


def test_sponsorfit_decision_report():
    body = client.get("/api/report?scenario=sponsorfit-meta").json()
    assert body["decision"] == "switch to Meta Model API"
    assert body["probe_kind"] == "decision"
    assert len(body["layers"]) == 1
    assert body["layers"][0]["recall"] == 1.0
    assert "sponsorfit.corpus.extract.extract_sponsors" in body["at_risk"]
