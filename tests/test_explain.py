"""Explainer report + claim checker tests (synthetic fixture)."""

import json

from chainreaction import explain
from chainreaction.explain import (
    LayerResult,
    Report,
    check_narrative,
    check_report,
    claim_checks,
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
        spine_evidence=evidence, spine_match=True,
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
    assert "Chain vs layer-1 actual: MATCH" in md
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


def test_narrative_facts_cite_file_not_imported_module(monkeypatch):
    seen = {}

    def fake_chat(system, user, **kw):
        seen["user"] = user
        return "NARRATIVE"

    monkeypatch.setattr("chainreaction.explain.chat", fake_chat)
    report = Report(
        decision="d", target="t", package="nibabel",
        numpy_version="2.0.2", change_symbols=["numpy.sctypes"],
        hard=["nibabel", "nibabel.analyze"], at_risk=[],
        spine=["nibabel", "nibabel.analyze"],
        spine_evidence=[
            ("nibabel", "nibabel.analyze", "nibabel/__init__.py", 41),
            ("nibabel.casting.ok_floats", "numpy.sctypes",
             "nibabel/casting.py", 717),
        ],
    )
    explain.generate_narrative(report)
    # File travels with the line; no "X imports Y:line" misattribution,
    # and the use edge is not called an import.
    assert "nibabel calls nibabel.analyze (nibabel/__init__.py:41)" in \
        seen["user"]
    assert ("nibabel.casting.ok_floats calls numpy.sctypes "
            "(nibabel/casting.py:717)") in seen["user"]
    assert " imports " not in seen["user"]


def test_narrative_brief_skips_numpy_for_decision_probes():
    report = Report(
        decision="switch to Meta Model API", target="targets/sponsorfit",
        package="sponsorfit", numpy_version="", change_symbols=[],
        spine=[], hard=[], at_risk=["sponsorfit.llm"],
        probe_kind="decision",
        layers=[LayerResult("decision", [], [], 1.0, "")],
    )
    brief = explain.narrative_brief(report)
    assert "NumPy" not in brief
    assert "unknown" not in brief
    assert "0 modules fail, 1 more are at risk" in brief
    # The version decision keeps its exact wording.
    nib = _narrative_report()
    nib.before_numpy = "1.26.4"
    brief = explain.narrative_brief(nib)
    assert "tested under NumPy 2.0.2 (previously 1.26.4)" in brief
    assert "2 modules fail at import" in brief


def _narrative_report():
    return Report(
        decision="upgrade numpy to 2.0", target="t", package="demo",
        numpy_version="2.0.2", change_symbols=["numpy.sctypes"],
        hard=["demo", "demo.leaf"], at_risk=[],
        spine=["demo", "demo.leaf"],
        spine_evidence=[("demo", "demo.leaf", "demo/__init__.py", 1)],
        spine_match=True,
        layers=[LayerResult("layer1", ["demo", "demo.leaf"], [], 1.0, "")],
        grep_hits=1, ruff_hits=1,
    )


def test_narrative_checker_passes_grounded_claims():
    report = _narrative_report()
    report.narrative = (
        "Upgrading to numpy 2.0 breaks 2 HARD modules. The verified chain "
        "is demo → demo.leaf at demo/__init__.py:1, ending at the "
        "numpy.sctypes use. Layer recall is 1.00 with 1 grep hit."
    )
    assert check_narrative(report) == []


def test_narrative_checker_flags_invented_module():
    report = _narrative_report()
    report.narrative = "The chain demo → demo.leaf → demo.hallucinated breaks."
    violations = check_narrative(report)
    assert len(violations) == 1
    assert "demo.hallucinated" in violations[0]


def test_narrative_checker_flags_wrong_count_and_evidence():
    report = _narrative_report()
    report.narrative = (
        "All 9 HARD modules break via demo/__init__.py:999."
    )
    violations = check_narrative(report)
    assert any("9" in v for v in violations)
    assert any("999" in v for v in violations)


def test_check_report_includes_narrative_violations(tmp_path):
    g, report = _setup(tmp_path)
    report.narrative = "Then demo.ghost breaks everything."
    violations = check_report(g, report)
    assert any("demo.ghost" in v for v in violations)


def test_narrative_checker_ignores_bare_filenames():
    report = _narrative_report()
    report.spine_evidence = [
        ("demo.leaf.ok_floats", "numpy.sctypes", "demo/casting.py", 717)]
    report.narrative = "Starts at demo/casting.py:717; fix casting.py first."
    assert check_narrative(report) == []


def test_narrative_checker_allows_percent_roundings_and_flagged_count():
    report = _narrative_report()
    report.pr_files = [f"demo/f{chr(97 + i)}.py" for i in range(8)]
    report.file_missing = report.pr_files[:3]
    report.file_precision = 1.0
    report.file_recall = 5 / 8
    report.narrative = ("Flagged 5 of the 8 files (recall 63%, about 62.5%, "
                        "at least 62%).")
    assert check_narrative(report) == []


def _statuses(checks):
    return [(c.label, c.status) for c in checks]


def test_claim_checks_pass_with_pending_narrative(tmp_path):
    g, report = _setup(tmp_path)
    by_label = dict(_statuses(claim_checks(g, report)))
    assert by_label["demo → demo.leaf"] == "pass"
    assert by_label["predicted nodes exist in graph"] == "pass"
    assert by_label["layer1: actual modules accounted for"] == "pass"
    assert by_label["narrative module names"] == "pending"
    assert by_label["narrative file:line citations"] == "pending"
    assert by_label["narrative numbers"] == "pending"


def test_claim_checks_flag_bad_hop_and_narrative(tmp_path):
    g, report = _setup(tmp_path)
    u, v, path, _ = report.spine_evidence[0]
    report.spine_evidence[0] = (u, v, path, 999)
    report.narrative = "Then demo.ghost breaks 9 things."
    by_label = {c.label: c for c in claim_checks(g, report)}
    assert by_label["demo → demo.leaf"].status == "fail"
    assert "999" in by_label["demo → demo.leaf"].detail
    assert by_label["narrative module names"].status == "fail"
    assert "demo.ghost" in by_label["narrative module names"].detail
    assert by_label["narrative numbers"].status == "fail"
    assert by_label["predicted nodes exist in graph"].status == "pass"


def _tiny_report(stats=None):
    import networkx as nx
    from types import SimpleNamespace

    from chainreaction.interpret import ChangeItem, ChangeSet
    from chainreaction.pipeline import build_report

    scen = SimpleNamespace(decision="d", target="t", package="pkg",
                           probe_code="", pr_files=[], pr_behavior=[],
                           pr_url="")
    cs = ChangeSet("d", "u", [
        ChangeItem("numpy.sctypes", "removed", "q", verified=True),
        ChangeItem("numpy.trapz", "deprecated", "q", verified=True),
        ChangeItem("numpy.asarray", "changed", "q", verified=True),
    ])
    layers = [LayerResult("l1", [], [], 1.0, "", True)]
    return build_report(scen, cs, {"numpy.sctypes"}, nx.DiGraph(), {},
                        layers, "2.0.2", None, 0, None, stats)


def test_build_report_separates_warnings_and_flags():
    report = _tiny_report()
    assert report.change_symbols == ["numpy.sctypes"]
    assert report.deprecated_symbols == ["numpy.trapz"]
    assert report.changed_symbols == ["numpy.asarray"]
    assert report.interpret_recall is None
    assert "excluded from scores" in render_markdown(report)


def test_build_report_carries_interpret_stats():
    report = _tiny_report({"recall": 0.5, "missed": ["numpy.mat"],
                           "table_size": 2})
    assert report.interpret_recall == 0.5
    assert report.interpret_table_size == 2
    assert report.interpret_missed == ["numpy.mat"]
    md = render_markdown(report)
    assert "Removal-table recall: 0.50 (1/2)" in md
    assert "numpy.mat" in md


def test_render_markdown_shows_narrative_claim_check():
    bad = _narrative_report()
    bad.narrative = "Then demo.ghost breaks everything."
    md = render_markdown(bad)
    assert "✗" in md and "demo.ghost" in md
    good = _narrative_report()
    good.narrative = "The verified chain is demo → demo.leaf."
    assert "✓ clean" in render_markdown(good)
