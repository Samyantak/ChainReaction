"""Repo-level graph tests on a synthetic package fixture."""

from pathlib import Path

from chainreaction.graph.repo import build_graph_from_repo


def _pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from . import mid\n", encoding="utf-8")
    (pkg / "mid.py").write_text("from . import leaf\n", encoding="utf-8")
    (pkg / "leaf.py").write_text(
        "import numpy as np\n"
        "\n"
        "def ok():\n"
        "    return np.sctypes['float'][:]\n"
        "\n"
        "OK = ok()\n",
        encoding="utf-8",
    )
    return tmp_path


def test_importer_path_and_executes_at_import(tmp_path):
    root = _pkg(tmp_path)
    g = build_graph_from_repo(root, "demo")

    assert ("demo", "demo.mid") in g.edges
    assert ("demo.mid", "demo.leaf") in g.edges
    d = g.edges["demo", "demo.mid"]
    assert d["kind"] == "IMPORTS" and d["import_time"] is True

    assert ("demo.leaf", "demo.leaf.ok") in g.edges
    assert g.edges["demo.leaf", "demo.leaf.ok"]["kind"] == "EXECUTES_AT_IMPORT"


def test_subpackage_init_relative_import(tmp_path):
    root = tmp_path
    sub = root / "demo" / "sub"
    sub.mkdir(parents=True)
    (root / "demo" / "__init__.py").write_text("", encoding="utf-8")
    (sub / "__init__.py").write_text(
        "from .mod import thing\n", encoding="utf-8"
    )
    (sub / "mod.py").write_text("thing = 1\n", encoding="utf-8")
    g = build_graph_from_repo(root, "demo")
    assert ("demo.sub", "demo.sub.mod") in g.edges


def test_repeated_import_keeps_first_line_and_import_time(tmp_path):
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from . import leaf\n"
        "from .leaf import thing\n",
        encoding="utf-8",
    )
    (pkg / "leaf.py").write_text("thing = 1\n", encoding="utf-8")
    g = build_graph_from_repo(tmp_path, "demo")
    d = g.edges["demo", "demo.leaf"]
    assert d["line"] == 1  # first occurrence, not the line-2 repeat
    assert d["import_time"] is True


def test_external_use_with_line_evidence(tmp_path):
    root = _pkg(tmp_path)
    g = build_graph_from_repo(root, "demo")

    uses = [
        (u, v, d) for u, v, d in g.edges(data=True)
        if d["kind"] == "USES_EXTERNAL" and v == "numpy.sctypes"
    ]
    assert uses, "expected demo.leaf.ok -> numpy.sctypes"
    u, _, d = uses[0]
    assert u == "demo.leaf.ok"
    assert d["line"] == 4
    assert d["file"].endswith("leaf.py")
