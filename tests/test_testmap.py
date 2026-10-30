"""Test-map tests: pure mapping plus a tiny live coverage build."""

import networkx as nx

from chainreaction.testmap import (
    TestMap,
    build_map,
    file_to_module,
    parse_failures,
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
