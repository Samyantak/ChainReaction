"""Explainer report + claim checker tests (synthetic fixture)."""

import json

from chainreaction.explain import (
    LayerResult,
    Report,
    check_report,
    render_json,
    render_markdown,
)
from chainreaction.graph.repo import build_graph_from_repo
from chainreaction.propagate import propagate


def _setup(tmp_path):
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from . import leaf\n", encoding="utf-8")
    (pkg / "leaf.py").write_text(
        "import numpy as np\n"
        "def ok():\n"
        "    return np.sctypes['float']\n"
        "OK = ok()\n",
        encoding="utf-8",
    )
    g = build_graph_from_repo(tmp_path, "demo")
    impacts = propagate(g, {"numpy.sctypes"})
    hard = sorted(n for n, i in impacts.items() if i.status == "HARD")
    d = g.edges["demo", "demo.leaf"]
    evidence = [("demo", "demo.leaf", d["file"], d["line"])]
    report = Report(
        decision="upgrade numpy to 2.0", target="demo", package="demo",
        numpy_version="2.5.4", change_symbols=["numpy.sctypes"],
        hard=hard, at_risk=[], spine=["demo", "demo.leaf"],
        spine_evidence=evidence,
        layers=[LayerResult("layer1", ["demo", "demo.leaf"], [], 1.0, "")],
        grep_hits=1, ruff_hits=1,
    )
    return g, report


def test_clean_report_passes_and_renders(tmp_path):
    g, report = _setup(tmp_path)
    assert check_report(g, report) == []
    md = render_markdown(report)
    assert "upgrade numpy to 2.0" in md
    assert "MODEL_API_KEY not configured" in md
    assert json.loads(render_json(report))["decision"] == report.decision


def test_checker_catches_bogus_node_and_evidence(tmp_path):
    g, report = _setup(tmp_path)
    report.hard.append("demo.nope")
    report.spine_evidence.append(("demo.leaf", "demo.ghost", "x.py", 1))
    report.layers[0].missing.append("demo.leaf")  # actually predicted HARD
    violations = check_report(g, report)
    assert len(violations) == 3
    assert any("demo.nope" in v for v in violations)
    assert any("demo.ghost" in v for v in violations)
    assert any("missing but predicted" in v for v in violations)


def test_checker_catches_wrong_line_number(tmp_path):
    g, report = _setup(tmp_path)
    u, v, path, _ = report.spine_evidence[0]
    report.spine_evidence[0] = (u, v, path, 999)
    violations = check_report(g, report)
    assert len(violations) == 1 and "999" in violations[0]
