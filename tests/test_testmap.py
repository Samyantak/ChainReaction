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
        assert extra_env == {"COVERAGE_FILE": ".coverage-chainreaction"}
        assert "--cov-context=test" in cmd
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    testmap, output = build_map_with(run, "py", str(target), "pkg")
    assert output == out
    assert testmap.tests == {"test_a.py::test_f": ["pkg.a"]}
    assert not (target / ".coverage-chainreaction").exists()

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
