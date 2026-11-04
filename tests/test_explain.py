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


def test_empty_spine_leads_with_test_chain(tmp_path):
    _, report = _setup(tmp_path)
    report.spine = []
    report.spine_evidence = []
    chain = {"test": "t.py::a", "func": "demo.fn", "symbol": "numpy.NaN",
             "file": "demo/x.py", "line": 10}
    md = render_markdown(report, {"scores": {"test_chain": chain}})
    assert "Predicted layer-1 chain" not in md
    assert "Chain vs layer-1 actual" not in md
    assert "Import chain is clean" in md
    assert "`t.py::a` runs `demo.fn`" in md
    # Without a test chain the (empty) spine block renders as before.
    plain = render_markdown(report)
    assert "Predicted layer-1 chain" in plain
    assert "Chain vs layer-1 actual: MATCH" in plain


def test_render_markdown_generic_extras(tmp_path):
    _, report = _setup(tmp_path)
    extra = {
        "scores": {
            "test_precision": 1.0, "test_recall": 0.5,
            "predicted_failing": ["a.py::t1"],
            "actual_new": ["a.py::t1", "a.py::t2"],
            "baseline_counts": {"passed": 4, "failed": 1},
            "after_counts": {"passed": 3, "failed": 2},
            "unhealthy_baseline": False,
        },
        "collateral": {"scipy": {"before": "1.11", "after": "1.14"}},
        "conflict": None, "untouched": False, "has_suite": True,
    }
    md = render_markdown(report, extra)
    assert "## Test suites" in md
    assert "test precision/recall: 1.00/0.50" in md
    assert "`scipy` 1.11->1.14" in md
    # Preset output is byte-identical with and without the parameter.
    assert render_markdown(report) == render_markdown(report, None)
    conflict_md = render_markdown(report, {**extra, "conflict": {
        "package": "numpy", "to_version": "2.0.2", "detail": "nope"}})
    assert "first domino" in conflict_md
    untouched_md = render_markdown(
        report, {"scores": {"suites": "skipped-untouched"},
                 "untouched": True})
    assert "not imported anywhere" in untouched_md


def test_interpret_m3_metrics_block_and_absent_for_legacy(tmp_path):
    from chainreaction.explain import interpret_m3_metrics

    assert interpret_m3_metrics(None) is None
    assert interpret_m3_metrics({"recall": 0.5}) is None
    stats = {"survival_rate": 0.8, "blind_recall": 0.5, "recall": 1.0,
             "det_by_method": {"table": 0, "list": 1, "pattern": 2},
             "model_only": ["pkg.quiet"], "completed": 2,
             "releases": ["3.0.1", "3.1.0"]}
    block = interpret_m3_metrics(stats)
    assert block == {
        "quote_survival_rate": 0.8, "blind_recall": 0.5,
        "guided_recall": 1.0,
        "deterministic_rows": {"table": 0, "list": 1, "pattern": 2},
        "model_only": ["pkg.quiet"], "completed": 2,
        "releases": ["3.0.1", "3.1.0"],
    }


def test_render_markdown_interpret_m3_section(tmp_path):
    _, report = _setup(tmp_path)
    assert "Interpreter metrics" not in render_markdown(report, {})
    md = render_markdown(report, {"interpret_m3": {
        "quote_survival_rate": 0.8, "blind_recall": 0.5,
        "guided_recall": 1.0,
        "deterministic_rows": {"table": 0, "list": 1, "pattern": 2},
        "model_only": ["pkg.quiet"], "completed": 2,
        "releases": ["3.0.1", "3.1.0"],
    }})
    assert "Interpreter metrics" in md
    assert "quote survival 0.80" in md
    assert "blind recall 0.50" in md
    assert "model-only finds (1): `pkg.quiet`" in md
    assert "completion added 2" in md
    assert "`3.0.1`, `3.1.0`" in md


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
        used_symbols=["numpy.sctypes"],
        hard=["nibabel", "nibabel.analyze"], at_risk=[],
        spine=["nibabel", "nibabel.analyze"],
        spine_evidence=[
            ("nibabel", "nibabel.analyze", "nibabel/__init__.py", 41),
            ("nibabel.casting.ok_floats", "numpy.sctypes",
             "nibabel/casting.py", 717),
        ],
    )
    explain.generate_narrative(report)
    # File travels with the line; module hops are imports, external
    # reads are uses — never "calls".
    assert "nibabel imports nibabel.analyze (nibabel/__init__.py:41)" in \
        seen["user"]
    assert ("nibabel.casting.ok_floats uses numpy.sctypes "
            "(nibabel/casting.py:717)") in seen["user"]
    assert " calls " not in seen["user"]


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


def test_narrative_brief_includes_test_results():
    report = _narrative_report()
    report.spine = []
    report.spine_evidence = []
    extra = {"has_suite": True, "scores": {
        "baseline_counts": {"passed": 10}, "after_counts": {"failed": 3},
        "actual_new": ["t.py::a", "t.py::b"],
        "predicted_failing": ["t.py::a"],
        "test_precision": 0.5, "test_recall": 0.5,
        "failure_groups": [{"message": "AttributeError: x", "count": 2,
                            "examples": []}],
        "test_chain": {"test": "t.py::a", "func": "demo.fn",
                       "symbol": "numpy.NaN", "file": "demo/x.py",
                       "line": 10},
    }}
    brief = explain.narrative_brief(report, extra)
    assert "2 new failures" in brief
    assert "precision 0.50" in brief
    assert "t.py::a runs demo.fn" in brief
    assert "import chain is clean" in brief
    report.narrative = ("2 new failures with precision 0.50. Example: "
                        "t.py::a runs demo.fn at demo/x.py:10.")
    assert check_narrative(report, extra=extra) == []


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


def test_failure_site_maps_installed_copy_frames(tmp_path):
    from chainreaction.pipeline import _failure_site
    from chainreaction.verifier import parse_traceback

    pkgdir = tmp_path / "src" / "flask_login"
    pkgdir.mkdir(parents=True)
    (pkgdir / "utils.py").write_text("x = 1\n", encoding="utf-8")
    scrubbed = parse_traceback(
        '  File "<env:after>/site-packages/flask_login/utils.py",'
        " line 14, in <module>\n")
    assert _failure_site(scrubbed, tmp_path, "flask_login") == \
        "src/flask_login/utils.py:14"
    real = parse_traceback(
        '  File "E:/co/envs/k/after/Lib/site-packages/flask_login/utils.py",'
        " line 14, in <module>\n")
    assert _failure_site(real, tmp_path, "flask_login") == \
        "src/flask_login/utils.py:14"
    foreign = parse_traceback(
        '  File "<env:after>/site-packages/werkzeug/urls.py",'
        " line 1, in <module>\n")
    assert _failure_site(foreign, tmp_path, "flask_login") is None


def test_build_report_separates_warnings_and_flags():
    report = _tiny_report()
    assert report.change_symbols == ["numpy.sctypes"]
    assert report.deprecated_symbols == ["numpy.trapz"]
    assert report.changed_symbols == ["numpy.asarray"]
    assert report.interpret_recall is None
    assert "excluded from scores" in render_markdown(report)


def test_failed_empty_actual_renders_unparsed():
    failed = _pr_report(
        layers=[LayerResult("after-import", [], ["m"], 0.0, "tail",
                            False)])
    assert "Actual: (unparsed)" in render_markdown(failed)
    passed = _pr_report(
        layers=[LayerResult("before-import", [], [], 1.0, "", True)])
    assert "Actual: (import ok)" in render_markdown(passed)


def test_build_report_carries_generic_versions():
    import networkx as nx
    from types import SimpleNamespace

    from chainreaction.interpret import ChangeSet
    from chainreaction.pipeline import build_report

    scen = SimpleNamespace(decision="d", target="t", package="pkg",
                           probe_code="", pr_files=[], pr_behavior=[],
                           pr_url="")
    cs = ChangeSet("d", "u", [])
    layers = [LayerResult("l1", [], [], 1.0, "", True)]
    report = build_report(scen, cs, set(), nx.DiGraph(), {}, layers,
                          None, None, 0, None, None,
                          decision_package="werkzeug",
                          before_version="2.2.3", after_version="3.0.6")
    assert report.decision_package == "werkzeug"
    assert report.before_version == "2.2.3"
    assert report.after_version == "3.0.6"
    assert report.numpy_version is None
    md = render_markdown(report)
    assert "| werkzeug: 2.2.3 -> 3.0.6" in md
    assert "MISSING" not in md
    assert "werkzeug 3.0.6 (previously 2.2.3)" in \
        explain.narrative_brief(report)
    # NumPy decisions keep the legacy alias.
    numpy_report = build_report(
        scen, cs, set(), nx.DiGraph(), {}, layers,
        None, None, 0, None, None, decision_package="numpy",
        before_version="1.26.4", after_version="2.0.2")
    assert numpy_report.numpy_version == "2.0.2"
    assert numpy_report.before_numpy == "1.26.4"


def test_build_report_carries_interpret_stats():
    report = _tiny_report({"recall": 0.5, "missed": ["numpy.mat"],
                           "table_size": 2, "blind_hits": 1,
                           "blind_recall": 0.5})
    assert report.interpret_recall == 0.5
    assert report.interpret_table_size == 2
    assert report.interpret_missed == ["numpy.mat"]
    assert report.interpret_blind_hits == 1
    assert report.interpret_blind_recall == 0.5
    md = render_markdown(report)
    assert ("Removal-table recall: Muse Spark alone 1/2 → with "
            "deterministic table extraction 1/2" in md)
    assert "numpy.mat" in md


def _pr_report(**over):
    from dataclasses import replace

    base = Report(
        decision="upgrade numpy to 2.0", target="t", package="nibabel",
        numpy_version="2.0.2", change_symbols=["numpy.sctypes"],
        hard=["nibabel"], at_risk=[], spine=["nibabel"],
        pr_url="https://example.invalid/pr/1",
        pr_files=[f"f{i}.py" for i in range(8)],
        file_precision=1.0, file_recall=0.625,
        file_missing=["nibabel/conftest.py", "nibabel/freesurfer/io.py",
                      "nibabel/spatialimages.py"],
        file_behavior=["nibabel/conftest.py", "nibabel/freesurfer/io.py",
                       "nibabel/spatialimages.py"],
        layers=[LayerResult("l1", [], [], 1.0, "", True)],
    )
    return replace(base, **over)


def test_fix_loop_section_renders_rounds_status_and_patch():
    report = _pr_report(fix={
        "status": "partial", "final_status": "partial",
        "stop_reason": "max-rounds",
        "rounds": [{"n": 1,
                    "failure": {"kind": "probe",
                                "site": "nibabel/casting.py:717"},
                    "edits": ["nibabel/casting.py"],
                    "probe": {"ok": False}}],
        "counts": {"fixed": ["a.py::t1"], "still_failing": ["b.py::t2"],
                   "newly_broken": [], "baseline_failures": 0,
                   "baseline_measured": True},
        "guards": {"ok": True, "violations": []},
        "patch": "fixes/final.patch",
        "upstream": {"ran": True, "pr_url": "https://example.invalid/pr/1",
                     "overlap": ["nibabel/casting.py"],
                     "missed": ["nibabel/quaternions.py"]},
    })
    md = render_markdown(report)
    assert "## Fix loop" in md
    assert "Status: partial (max-rounds)" in md
    assert "Round 1: probe nibabel/casting.py:717" in md
    assert "1 fixed, 1 still failing, 0 newly broken" in md
    assert "Guards: clean" in md
    assert "Patch: `fixes/final.patch`" in md
    assert "files in common: `nibabel/casting.py`" in md
    assert "PR files missed: `nibabel/quaternions.py`" in md
    assert "## Baselines" in md.split("## Fix loop")[1]
    # No fix data: no section.
    assert "## Fix loop" not in render_markdown(_pr_report())
    assert "## Fix loop" not in render_markdown(_pr_report(fix={}))


def test_brief_and_scoreboard_read_recall_both_ways():
    report = _pr_report()
    brief = explain.narrative_brief(report)
    assert "(5/8 overall; 5/5 of the breaking changes)" in brief
    assert "missed files: none" in brief
    assert ("Behavior-change files (flagged separately, not scored): "
            "nibabel/conftest.py, nibabel/freesurfer/io.py, "
            "nibabel/spatialimages.py.") in brief
    md = render_markdown(report)
    assert "(5/8 PR files flagged; 5/5 of the breaking changes)" in md
    assert "Missed: none" in md
    # A narrative citing the breaking total passes the claim check.
    report.narrative = ("Five of 8 PR files flagged, and all 5 breaking "
                        "ones covered.")
    assert check_narrative(report) == []
    # Mixed case: two true misses plus one behavior file.
    mixed = _pr_report(file_behavior=["nibabel/conftest.py"])
    brief = explain.narrative_brief(mixed)
    assert "(5/8 overall; 5/7 of the breaking changes)" in brief
    assert "missed files: nibabel/freesurfer/io.py" in brief


def test_render_markdown_shows_narrative_claim_check():
    bad = _narrative_report()
    bad.narrative = "Then demo.ghost breaks everything."
    md = render_markdown(bad)
    assert "✗" in md and "demo.ghost" in md
    good = _narrative_report()
    good.narrative = "The verified chain is demo → demo.leaf."
    assert "✓ clean" in render_markdown(good)


def test_brief_names_used_symbols_not_guide_total():
    report = _narrative_report()
    report.change_symbols = ["numpy.a", "numpy.b", "numpy.c"]
    report.used_symbols = ["numpy.b"]
    brief = explain.narrative_brief(report)
    assert "Breaking symbols (3): numpy.a, numpy.b, numpy.c." in brief
    assert ("Removed symbols used by this repo (1): numpy.b. "
            "Only these can cause failures here.") in brief
    # The used count is a grounded number.
    report.narrative = "Only 1 of the 3 symbols is used here."
    assert check_narrative(report) == []


def test_brief_fix_order_marks_importers_not_edit_sites():
    report = _narrative_report()
    report.layers = [
        LayerResult("layer1", ["demo", "demo.leaf"], [], 1.0, "", False,
                    "demo/casting.py:717"),
        LayerResult("layer2 (peeled)", ["demo", "demo.other"], [], 1.0,
                    "", False, "demo/quaternions.py:32"),
        LayerResult("before (numpy1)", [], [], 1.0, "", True, None),
    ]
    brief = explain.narrative_brief(report)
    assert ("Fix order: layer1: demo/casting.py:717; "
            "layer2 (peeled): demo/quaternions.py:32.") in brief
    assert "importers, not edit sites" in brief
    assert "in one step" in brief
    # Both fix sites are citable evidence, not violations.
    report.narrative = ("Fix demo/casting.py:717 first, then "
                        "demo/quaternions.py:32.")
    assert check_narrative(report) == []


def test_brief_executes_verb_for_import_time_defs():
    report = Report(
        decision="d", target="t", package="demo",
        numpy_version="2.0.2", change_symbols=["numpy.sctypes"],
        used_symbols=["numpy.sctypes"],
        hard=["demo"], at_risk=[], spine=["demo", "demo.leaf"],
        spine_evidence=[
            ("demo", "demo.leaf", "demo/__init__.py", 1),
            ("demo.leaf", "demo.leaf.helper", "demo/leaf.py", 10),
            ("demo.leaf.helper", "numpy.sctypes", "demo/leaf.py", 5),
        ],
        spine_match=True,
        layers=[LayerResult("layer1", [], [], 1.0, "", True)],
    )
    brief = explain.narrative_brief(report)
    assert "demo imports demo.leaf (demo/__init__.py:1)" in brief
    assert ("demo.leaf executes demo.leaf.helper at import "
            "(demo/leaf.py:10)") in brief
    assert "demo.leaf.helper uses numpy.sctypes (demo/leaf.py:5)" in brief


def test_brief_lists_code_grounded_config_breaks():
    from chainreaction.interpret import BreakDetail, ChangeItem

    report = Report(
        decision="switch to Meta Model API", target="targets/sponsorfit",
        package="sponsorfit", numpy_version="", change_symbols=[],
        hard=[], at_risk=["sponsorfit.llm"],
        spine=[], spine_evidence=[], probe_kind="decision",
        change_items=[
            ChangeItem("sponsorfit.llm.Provider", "rejected_value", "",
                       detail=BreakDetail('"meta" in Provider',
                                          'Literal["a","o"]'),
                       verified=True, source="code",
                       code_quote='Provider = Literal["a","o"]',
                       code_file="src/sponsorfit/llm.py", code_line=14),
        ],
        layers=[LayerResult("decision", ["sponsorfit.llm"], [], 1.0,
                            "", False, "src/sponsorfit/llm.py:78")],
    )
    brief = explain.narrative_brief(report)
    assert "Config breaks (1): sponsorfit.llm.Provider (rejected_value" in \
        brief
    assert "src/sponsorfit/llm.py:14" in brief
    assert "Fix order: decision: src/sponsorfit/llm.py:78." in brief
    # Code file:line and layer site are both citable evidence.
    report.narrative = ("Provider at src/sponsorfit/llm.py:14 rejects meta; "
                        "the probe fails at src/sponsorfit/llm.py:78.")
    assert check_narrative(report) == []


def test_checker_allows_detail_modules_and_bare_site_lines():
    from chainreaction.interpret import BreakDetail, ChangeItem

    report = Report(
        decision="switch", target="t", package="fit",
        numpy_version="", change_symbols=[], hard=[], at_risk=["fit.llm"],
        spine=[], spine_evidence=[], probe_kind="decision",
        change_items=[
            ChangeItem("fit.llm.Caller", "missing_param", "",
                       detail=BreakDetail("x", "openai.OpenAI() bare"),
                       verified=True, source="code",
                       code_quote="openai.OpenAI()", code_file="fit/a.py",
                       code_line=160),
        ],
        layers=[LayerResult("decision", ["fit.llm"], [], 1.0, "", False,
                            "fit/a.py:78")],
    )
    # SDK dotted name quoted from the brief's detail, and bare line
    # numbers from the code and probe sites, are all grounded.
    report.narrative = ("The openai.OpenAI call at line 160 fails; "
                        "see line 78.")
    assert check_narrative(report) == []


def _vendor_graph(tmp_path):
    pkg = tmp_path / "fit"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "llm.py").write_text(
        "Provider = 1\n"
        "class Client:\n"
        "    def call(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    return build_graph_from_repo(tmp_path, "fit")


def _vendor_item(**over):
    from chainreaction.interpret import ChangeItem

    args = dict(symbol="fit.llm.Provider", kind="rejected_value", quote="",
                verified=True, source="code",
                code_quote="Provider = 1", code_file="fit/llm.py",
                code_line=1)
    args.update(over)
    return ChangeItem(**args)


def test_code_hop_passes_with_verified_quote(tmp_path):
    g = _vendor_graph(tmp_path)
    report = Report(
        decision="switch", target="t", package="fit",
        numpy_version=None, change_symbols=[], hard=["fit.llm"],
        at_risk=["fit.llm.Client.call"],
        spine=["fit.llm"],
        spine_evidence=[("fit.llm.Client.call", "fit.llm",
                         "fit/llm.py", 1)],
        spine_match=True, probe_kind="decision",
        change_items=[_vendor_item()],
        layers=[LayerResult("decision", ["fit.llm"], [], 1.0, "", False,
                            "fit/llm.py:4")],
    )
    assert check_report(g, report) == []
    hop = [c for c in claim_checks(g, report)
           if c.label == "fit.llm.Client.call → fit.llm"][0]
    assert (hop.status, hop.detail) == ("pass", "fit/llm.py:1 (code-grounded)")
    brief = explain.narrative_brief(report)
    assert ("fit.llm.Client.call reaches fit.llm (fit/llm.py:1)") in brief


def test_code_hop_fails_without_matching_quote(tmp_path):
    g = _vendor_graph(tmp_path)
    report = Report(
        decision="switch", target="t", package="fit",
        numpy_version=None, change_symbols=[], hard=["fit.llm"],
        at_risk=[], spine=["fit.llm"],
        spine_evidence=[("fit.llm.Client.call", "fit.llm",
                         "fit/llm.py", 99)],
        spine_match=True, probe_kind="decision",
        change_items=[_vendor_item()], layers=[],
    )
    assert any("missing from graph" in v for v in check_report(g, report))


def test_brief_vendor_fix_order_peels_layers():
    report = Report(
        decision="switch", target="t", package="fit",
        numpy_version=None, change_symbols=[], hard=["fit.llm"],
        at_risk=[], spine=["fit.llm"], spine_evidence=[],
        spine_match=True, probe_kind="decision",
        fix_order=["fit.llm.Provider: fit/llm.py:1",
                   "fit.llm.Other: fit/llm.py:9",
                   "decision: fit/llm.py:4"],
        layers=[LayerResult("decision", ["fit.llm"], [], 1.0, "", False,
                            "fit/llm.py:4")],
    )
    brief = explain.narrative_brief(report)
    assert ("Fix order: fit.llm.Provider: fit/llm.py:1; "
            "fit.llm.Other: fit/llm.py:9; decision: fit/llm.py:4.") in brief
    assert "reveals the next layer" in brief
    assert "importers" not in brief


def test_render_hides_numpy_rows_for_decisions():
    report = _narrative_report()
    report.probe_kind = "decision"
    report.numpy_version = None
    report.ruff_hits = None
    md = render_markdown(report)
    assert "numpy:" not in md
    assert "ruff" not in md
    assert "Predicted call chain:" in md
    assert "Chain vs decision actual:" in md


def test_render_keeps_numpy_rows_for_imports():
    md = render_markdown(_narrative_report())
    assert "numpy: 2.0.2" in md
    assert "ruff NPY201: 1 hits" in md
    assert "Predicted layer-1 chain:" in md


def test_narrate_prompt_bans_reliability_claims():
    assert "past runs or general reliability" in \
        explain.NARRATE_SYSTEM_PROMPT


def test_build_report_vendor_chain_and_fix_order(tmp_path):
    from types import SimpleNamespace

    from chainreaction.interpret import ChangeSet
    from chainreaction.pipeline import build_report
    from chainreaction.propagate import probe_entries, propagate

    pkg = tmp_path / "fit"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "llm.py").write_text(
        "Provider = 1\n"
        "class Client:\n"
        "    def call(self):\n"
        "        return self._do()\n"
        "    def _do(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    g = build_graph_from_repo(tmp_path, "fit")
    probe = ("from fit.llm import Client\nclient = Client()\n"
             "client.call()\n")
    scen = SimpleNamespace(decision="switch", target=str(tmp_path),
                           package="fit", probe_code=probe, pr_files=[],
                           pr_behavior=[], pr_url="")
    items = [
        _vendor_item(),
        _vendor_item(symbol="fit.llm.Client._do", kind="missing_param",
                     code_quote="return 1", code_line=6),
    ]
    entries = probe_entries(probe, "fit", g)
    impacts = propagate(g, set(), items, "fit", entries=tuple(entries))
    layers = [LayerResult("decision", ["fit.llm"], [], 1.0, "", False,
                          "fit/llm.py:4")]
    report = build_report(scen, ChangeSet("switch", "u", items), set(), g,
                          impacts, layers, None, None, 0, None)
    assert report.spine == ["fit.llm"]
    assert report.spine_match is True
    assert report.fix_order == ["fit.llm.Provider: fit/llm.py:1",
                                "fit.llm.Client._do: fit/llm.py:6",
                                "decision: fit/llm.py:4"]
    assert check_report(g, report) == []
