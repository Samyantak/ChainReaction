"""Test-map tests: pure mapping plus a tiny live coverage build."""

import networkx as nx

from chainreaction.testmap import (
    TestMap,
    build_map,
    build_map_with,
    failing_tests,
    file_to_module,
    parse_dump,
    parse_failures,
    parse_suite,
    parse_suite_counts,
    predicted_failing,
)


def test_file_to_module():
    assert file_to_module("t/nibabel/casting.py", "t", "nibabel") == \
        "nibabel.casting"
    assert file_to_module("t/nibabel/__init__.py", "t", "nibabel") == "nibabel"
    assert file_to_module("t/nibabel/conftest.py", "t", "nibabel") == \
        "nibabel.conftest"
    assert file_to_module("t/nibabel/tests/test_x.py", "t", "nibabel") is None
    assert file_to_module("t/other/x.py", "t", "nibabel") is None
    assert file_to_module("t/nibabel/data.bin", "t", "nibabel") is None


def test_file_to_module_handles_foreign_absolute():
    # Windows recording replayed on Linux: backslashes, drive prefix,
    # doubled separators from JSON-escaped dump text.
    win = ("E:\\ChainReaction\\workspaces\\scverse__anndata@1461fecd1712"
           "\\src\\anndata\\compat\\exceptiongroups.py")
    assert file_to_module(win, "workspaces/scverse__anndata@1461fecd1712",
                          "anndata") == "anndata.compat.exceptiongroups"
    doubled = win.replace("\\", "\\\\")
    assert file_to_module(doubled, "workspaces/scverse__anndata@1461fecd1712",
                          "anndata") == "anndata.compat.exceptiongroups"
    # Scrubbed placeholder form.
    assert file_to_module("<ws>/src/anndata/x.py", "ws", "anndata") == \
        "anndata.x"
    # Out-of-package foreign paths stay None.
    assert file_to_module("E:\\other\\pkg\\x.py", "ws", "anndata") is None


def test_covering_and_roundtrip(tmp_path):
    m = TestMap("t", "pkg", "env", {"t1": ["pkg.a"], "t2": ["pkg.b"]})
    assert m.covering({"pkg.a", "pkg.zz"}) == ["t1"]
    assert m.covering(set()) == []
    p = tmp_path / "map.json"
    m.save(p)
    assert TestMap.load(p) == m


def test_live_build_on_tiny_package(tmp_path):
    import sys

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (pkg / "b.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    (tmp_path / "test_a.py").write_text(
        "from pkg.a import f\ndef test_f():\n    assert f() == 1\n",
        encoding="utf-8",
    )
    m = build_map(sys.executable, tmp_path, "pkg", "test_a.py", timeout=300)
    assert m.tests == {"test_a.py::test_f": ["pkg.a"]}


def test_parse_failures_splits_ids_and_files():
    out = ("FAILED a/test_x.py::test_1 - assert False\n"
           "FAILED b.py::t - err\n"
           "ERROR c/test_y.py - collection\n"
           "ERROR d.py::t2 - setup\n")
    failed, errfiles = parse_failures(out, "t")
    assert failed == {"a/test_x.py::test_1", "b.py::t", "d.py::t2"}
    assert errfiles == {"c/test_y.py"}


def test_parse_failures_strips_target_prefix():
    out = "FAILED targets/nibabel/nibabel/tests/test_a.py::t - x\n"
    failed, _ = parse_failures(out, "targets/nibabel")
    assert failed == {"nibabel/tests/test_a.py::t"}


def test_predicted_failing_covers_importers():
    g = nx.DiGraph()
    g.add_node("pkg.h", kind="module", file="t/pkg/h.py")
    g.add_node("pkg.tests.test_m", kind="module",
               file="t/pkg/tests/test_m.py")
    g.add_edge("pkg.tests.test_m", "pkg.h", kind="IMPORTS",
               file="t/pkg/tests/test_m.py", line=1, import_time=True)
    m = TestMap("t", "pkg", "", {
        "pkg/tests/test_m.py::t1": ["pkg.other"],  # imports HARD file
        "pkg/tests/test_o.py::t2": ["pkg.h"],  # covers HARD module
        "pkg/tests/test_p.py::t3": ["pkg.other"],  # healthy
    })
    assert sorted(predicted_failing(m, g, {"pkg.h"}, "t")) == [
        "pkg/tests/test_m.py::t1",
        "pkg/tests/test_o.py::t2",
    ]


def test_file_to_module_unwraps_src_layout(tmp_path):
    assert file_to_module("t/src/pkg/x.py", "t", "pkg") == "pkg.x"
    assert file_to_module("t/src/pkg/__init__.py", "t", "pkg") == "pkg"
    assert file_to_module("t/src/pkg/tests/t.py", "t", "pkg") is None


def test_parse_suite_counts_and_expansion():
    out = ("FAILED a.py::t1 - x\n"
           "ERROR b/test_e.py - collection\n"
           "2 failed, 5 passed in 0.4s\n")
    assert parse_suite_counts(out) == {"failed": 2, "passed": 5}
    assert parse_suite_counts("1 error in 0.1s") == {"errors": 1}
    m = TestMap("t", "pkg", "", {"a.py::t1": ["pkg.a"],
                                   "b/test_e.py::t9": ["pkg.a"]})
    res = parse_suite(out, "t", m)
    assert res.failed == {"a.py::t1", "b/test_e.py::t9"}
    assert res.error_files == {"b/test_e.py"}
    assert res.total == 7
    # Without a map, ERROR files stay unattributed but reported.
    res = parse_suite(out, "t", None)
    assert res.failed == {"a.py::t1"}


def test_parse_dump_inverts_and_strips(tmp_path):
    import json

    text = json.dumps({
        f"{tmp_path}/pkg/a.py": ["ws/t1", "ws/t2"],
        f"{tmp_path}/pkg/tests/t.py": ["ws/t3"],
        f"{tmp_path}/other/x.py": ["ws/t4"],
    })
    assert parse_dump(text, str(tmp_path), "pkg",
                      "ws/") == {"t1": ["pkg.a"], "t2": ["pkg.a"]}
    assert parse_dump("not json", str(tmp_path), "pkg") == {}
    assert parse_dump("[]", str(tmp_path), "pkg") == {}


def test_call_chain_links_test_to_break_line():
    import networkx as nx

    from chainreaction.testmap import test_call_chain

    g = nx.DiGraph()
    g.add_node("pkg.r", kind="module", file="pkg/r.py")
    g.add_node("pkg.r.fn", kind="function", file="pkg/r.py", line=3)
    g.add_edge("pkg.r.fn", "numpy.NaN", kind="USES_EXTERNAL",
               file="pkg/r.py", line=10)
    m = TestMap("t", "pkg", "", {"t1": ["pkg.r"]},
                line_hits={"t1": ["pkg/r.py:10"]})
    chain = test_call_chain(m, g, {"t1"}, {"numpy.NaN"}, "t")
    assert chain == ("t1", "pkg.r.fn", "numpy.NaN", "pkg/r.py", 10)
    assert test_call_chain(m, g, {"t9"}, {"numpy.NaN"}, "t") is None


def test_failure_groups_attribute_by_message():
    from chainreaction.testmap import failure_groups

    out = ("FAILED a.py::t1 - AttributeError: `np.NaN` was removed\n"
           "FAILED a.py::t2 - AttributeError: `np.NaN` was removed\n"
           "FAILED b.py::t3 - ValueError: bad shape\n"
           "FAILED c.py::t4\n"
           "1 failed\n")
    groups = failure_groups({"a.py::t1", "a.py::t2", "b.py::t3", "c.py::t4"},
                            out, "t")
    assert groups[0]["count"] == 2
    assert "np.NaN" in groups[0]["message"]
    assert groups[0]["examples"] == ["a.py::t1", "a.py::t2"]
    assert any(g["message"] == "other" and g["count"] == 1 for g in groups)


def test_focused_dump_survives_scrub_roundtrip():
    """Scrubbed <ws> dump text stays valid JSON through untext+parse.

    Regression: native-backslash untext corrupted the dump's JSON
    escapes, json.loads failed, and the run fell back to plain pytest.
    """
    import json

    from chainreaction.runs.scrub import Scrubber
    from chainreaction.testmap import parse_dump_full

    scrubbed = json.dumps({
        "files": {"<ws>//src//pkg//a.py": ["t1"]},
        "lines": {"<ws>//src//pkg//a.py": {"10": ["t1"]}},
    })
    scr = Scrubber.default(ws_pin="E:\\fake\\ws")
    mods, lines = parse_dump_full(scr.untext(scrubbed), "ws", "pkg")
    assert mods == {"t1": ["pkg.a"]}
    assert lines == {"t1": ["src/pkg/a.py:10"]}


def test_dump_script_reports_focused_lines(tmp_path):
    """The real dump script honors the focus list (live coverage run)."""
    import json
    import subprocess
    import sys

    from chainreaction.testmap import (
        _DUMP_CODE,
        file_to_rel,
        parse_dump_full,
    )

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "test_a.py").write_text(
        "from pkg.a import f\ndef test_f():\n    assert f() == 1\n",
        encoding="utf-8",
    )
    env = dict(__import__("os").environ)
    data_file = tmp_path / ".coverage-focus"
    env["COVERAGE_FILE"] = str(data_file)
    subprocess.run(
        [sys.executable, "-m", "pytest", "test_a.py", "-q", "--tb=no",
         "-p", "no:cacheprovider", "--cov=pkg", "--cov-context=test",
         "--cov-report="],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=300,
        env=env, check=False)
    assert data_file.exists()
    proc = subprocess.run(
        [sys.executable, "-c", _DUMP_CODE, data_file.name,
         json.dumps(["pkg/a.py"])],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=120,
        check=False)
    assert proc.returncode == 0, proc.stderr[-500:]
    raw = json.loads(proc.stdout)
    assert set(raw) == {"files", "lines"}
    mods, lines = parse_dump_full(proc.stdout, str(tmp_path), "pkg")
    assert "pkg.a" in mods["test_a.py::test_f"]
    assert file_to_rel(str(tmp_path / "pkg" / "a.py"), str(tmp_path),
                       "pkg") == "pkg/a.py"
    hits = lines["test_a.py::test_f"]
    assert "pkg/a.py:2" in hits
    assert all(h.startswith("pkg/a.py:") for h in hits)
    # Files outside the focus list contribute no per-line detail.
    proc = subprocess.run(
        [sys.executable, "-c", _DUMP_CODE, data_file.name, "[]"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=120,
        check=False)
    _, no_lines = parse_dump_full(proc.stdout, str(tmp_path), "pkg")
    assert no_lines == {}


def test_parse_dump_handles_foreign_absolute():
    import json

    win = ("E:\\ChainReaction\\workspaces\\k\\src\\pkg\\a.py")
    text = json.dumps({win: ["t1"]})
    assert parse_dump(text, "workspaces/k", "pkg") == {"t1": ["pkg.a"]}


def test_failing_tests_accepts_injected_runner():
    from types import SimpleNamespace

    m = TestMap("t", "pkg", "", {"a.py::t1": ["pkg.a"],
                                   "e.py::t2": ["pkg.a"]})
    seen = []

    def run(cmd, cwd):
        seen.append((cmd, cwd))
        return SimpleNamespace(
            stdout="FAILED a.py::t1 - x\nERROR e.py - boom\n"
                   "1 failed, 1 passed\n")

    assert failing_tests("py", "t", ".", m, run=run) == {"a.py::t1",
                                                         "e.py::t2"}
    assert seen[0][0][:3] == ["py", "-m", "pytest"]
    assert "--noconftest" in seen[0][0]


def test_predicted_failing_spans_at_risk_modules():
    g = nx.DiGraph()
    g.add_node("pkg.h", kind="module", file="t/pkg/h.py")
    g.add_node("pkg.r", kind="module", file="t/pkg/r.py")
    g.add_node("pkg.r.fn", kind="function", file="t/pkg/r.py", line=3)
    m = TestMap("t", "pkg", "", {
        "t1.py::a": ["pkg.h"],
        "t2.py::b": ["pkg.r"],
        "t3.py::c": ["pkg.other"],
    })
    assert predicted_failing(m, g, {"pkg.h"}, "t") == {"t1.py::a"}
    assert predicted_failing(m, g, {"pkg.h"}, "t",
                             {"pkg.r.fn"}) == {"t1.py::a", "t2.py::b"}


def test_predicted_failing_uses_break_lines_when_available():
    import networkx as nx

    g = nx.DiGraph()
    g.add_node("pkg.r", kind="module", file="pkg/r.py")
    g.add_node("pkg.r.fn", kind="function", file="pkg/r.py", line=3)
    g.add_edge("pkg.r.fn", "numpy.NaN", kind="USES_EXTERNAL",
               file="pkg/r.py", line=10)
    # Both tests cover the at-risk module, but only t1 hits the break line.
    m = TestMap("t", "pkg", "", {"t1": ["pkg.r"], "t2": ["pkg.r"]},
                line_hits={"t1": ["pkg/r.py:10"], "t2": ["pkg/r.py:99"]})
    pred = predicted_failing(m, g, set(), "t", {"pkg.r.fn"},
                             {"numpy.NaN"})
    assert pred == {"t1"}


def test_build_map_with_parses_recorded_dump(tmp_path):
    import json
    from types import SimpleNamespace

    target = tmp_path / "ws"
    target.mkdir()
    dump = json.dumps({f"{target}/pkg/a.py": ["test_a.py::test_f"]})
    out = "1 passed in 0.1s\n"

    def run(cmd, cwd, extra_env=None):
        assert cwd == str(target)
        if cmd[1] == "-c":
            assert extra_env is None
            return SimpleNamespace(returncode=0, stdout=dump, stderr="")
        assert extra_env == {"COVERAGE_FILE": ".coverage-chainreaction",
                             "COLUMNS": "200"}
        assert "--cov-context=test" in cmd
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    testmap, output = build_map_with(run, "py", str(target), "pkg")
    assert output == out
    assert testmap.tests == {"test_a.py::test_f": ["pkg.a"]}
    assert testmap.line_hits == {}  # old file dump carries no lines
    assert not (target / ".coverage-chainreaction").exists()
    # Per-line dumps populate both maps.
    lined = json.dumps({f"{target}/pkg/a.py": {"10": ["test_a.py::test_f"],
                                                "99": ["test_a.py::test_f"]}})

    def run_lined(cmd, cwd, extra_env=None):
        if cmd[1] == "-c":
            return SimpleNamespace(returncode=0, stdout=lined, stderr="")
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    lined_map, _ = build_map_with(run_lined, "py", str(target), "pkg")
    assert lined_map.tests == {"test_a.py::test_f": ["pkg.a"]}
    assert lined_map.line_hits == {
        "test_a.py::test_f": ["pkg/a.py:10", "pkg/a.py:99"]}
    # Focused dumps: file-level for all, lines for focus files only, and
    # the focus list travels as stable argv.
    focused = json.dumps({
        "files": {f"{target}/pkg/a.py": ["test_a.py::test_f"],
                  f"{target}/pkg/b.py": ["test_a.py::test_f"]},
        "lines": {f"{target}/pkg/a.py": {"10": ["test_a.py::test_f"]}},
    })
    seen_cmds = []

    def run_focused(cmd, cwd, extra_env=None):
        if cmd[1] == "-c":
            seen_cmds.append(cmd)
            return SimpleNamespace(returncode=0, stdout=focused, stderr="")
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    focus_map, _ = build_map_with(run_focused, "py", str(target), "pkg",
                                  focus_files={"pkg/a.py"})
    assert focus_map.tests == {"test_a.py::test_f": ["pkg.a", "pkg.b"]}
    assert focus_map.line_hits == {"test_a.py::test_f": ["pkg/a.py:10"]}
    assert json.loads(seen_cmds[0][4]) == ["pkg/a.py"]
    # Replay of a V1 recording falls back to the old dump command.
    from chainreaction.runs.context import ReplayMiss
    from chainreaction.testmap import _DUMP_CODE_V1

    def run_old(cmd, cwd, extra_env=None):
        if cmd[1] == "-c":
            if cmd[2] == _DUMP_CODE_V1:
                return SimpleNamespace(returncode=0, stdout=dump,
                                       stderr="")
            raise ReplayMiss("no recorded cmd call")
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    old_map, _ = build_map_with(run_old, "py", str(target), "pkg")
    assert old_map.tests == {"test_a.py::test_f": ["pkg.a"]}
    assert old_map.line_hits == {}

    def empty(cmd, cwd, extra_env=None):
        if cmd[1] == "-c":
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    import pytest

    with pytest.raises(RuntimeError, match="no data"):
        build_map_with(empty, "py", str(target), "pkg")

    def slow(cmd, cwd, extra_env=None):
        return SimpleNamespace(returncode=0, stdout="", stderr="",
                               timed_out=True)

    with pytest.raises(RuntimeError, match="timed out"):
        build_map_with(slow, "py", str(target), "pkg")
