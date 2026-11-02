"""Repo-level graph tests on a synthetic package fixture."""

from pathlib import Path

from chainreaction.graph.repo import build_graph_from_repo, relative_posix


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


def test_evidence_paths_are_repo_relative_posix(tmp_path):
    g = build_graph_from_repo(_pkg(tmp_path), "demo")
    assert g.edges["demo", "demo.mid"]["file"] == "demo/__init__.py"
    assert g.nodes["demo.leaf"]["file"] == "demo/leaf.py"
    for _, _, d in g.edges(data=True):
        assert "\\" not in d["file"], d["file"]
        assert not d["file"].startswith(str(tmp_path)), d["file"]
    for _, d in g.nodes(data=True):
        if "file" in d:
            assert "\\" not in d["file"], d["file"]


def test_relative_posix_strips_target_prefix_idempotently(tmp_path):
    target = str(tmp_path / "t")
    assert relative_posix("t/pkg/a.py", "t") == "pkg/a.py"
    assert relative_posix("pkg/a.py", "t") == "pkg/a.py"
    assert relative_posix(str(tmp_path / "t" / "pkg" / "a.py"), target) == \
        "pkg/a.py"
    assert relative_posix("t\\pkg\\a.py", "t") == "pkg/a.py"


def test_relative_posix_handles_windows_paths_on_any_os():
    # relpath must see normalized separators: on Linux it treats
    # backslashes as ordinary characters and emits ../t/pkg/a.py garbage.
    assert relative_posix("E:\\t\\nibabel\\nibabel\\x.py",
                          "E:\\t\\nibabel") == "nibabel/x.py"
    assert relative_posix("E:/t/nibabel/nibabel/x.py",
                          "E:/t/nibabel") == "nibabel/x.py"


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


def _crossmod(tmp_path):
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text(
        "from .b import helper\n"
        "def caller():\n"
        "    return helper()\n",
        encoding="utf-8",
    )
    (pkg / "b.py").write_text(
        "class C:\n"
        "    def chelp(self):\n"
        "        return 1\n"
        "    def method(self):\n"
        "        return self.chelp()\n"
        "class D:\n"
        "    def chelp(self):\n"
        "        return 2\n"
        "def helper():\n"
        "    return 3\n",
        encoding="utf-8",
    )
    return tmp_path


def test_plain_from_import_links_call_to_qualified_def(tmp_path):
    g = build_graph_from_repo(_crossmod(tmp_path), "demo")
    d = g.edges["demo.a.caller", "demo.b.helper"]
    assert d["kind"] == "CALLS"


def test_typed_param_method_call_resolves(tmp_path):
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "svc.py").write_text(
        "class Client:\n"
        "    def run(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    (pkg / "agent.py").write_text(
        "from .svc import Client\n"
        "def act(client: Client):\n"
        "    return client.run()\n",
        encoding="utf-8",
    )
    g = build_graph_from_repo(tmp_path, "demo")
    d = g.edges["demo.agent.act", "demo.svc.Client.run"]
    assert d["kind"] == "CALLS"


def test_methods_qualified_with_class_no_collision(tmp_path):
    g = build_graph_from_repo(_crossmod(tmp_path), "demo")
    assert g.nodes["demo.b.C.chelp"]["kind"] == "function"
    assert g.nodes["demo.b.D.chelp"]["kind"] == "function"
    assert "demo.b.chelp" not in g.nodes
    d = g.edges["demo.b.C.method", "self.chelp"]
    assert d["kind"] == "CALLS"


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


def _endian_pkg(tmp_path):
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "endian.py").write_text(
        "import numpy as np\n"
        "from numpy import dtype\n"
        "def on_dtype(dt):\n"
        "    dt = np.dtype('i4')\n"
        "    return dt.newbyteorder('<')\n"
        "def on_param(data):\n"
        "    return data.newbyteorder('<')\n"
        "def on_bare_dtype(t):\n"
        "    t = dtype(t)\n"
        "    return t.newbyteorder('B')\n"
        "def on_attr(obj):\n"
        "    return obj.dtype.newbyteorder('<')\n"
        "def on_reassigned(dt):\n"
        "    dt = np.dtype('i4')\n"
        "    dt = make_array()\n"
        "    return dt.newbyteorder('<')\n",
        encoding="utf-8",
    )
    return tmp_path


def test_newbyteorder_classifies_dtype_vs_ndarray(tmp_path):
    g = build_graph_from_repo(_endian_pkg(tmp_path), "demo")
    targets = {}
    for u, v, d in g.edges(data=True):
        if d["kind"] == "USES_EXTERNAL" and "newbyteorder" in v:
            targets[u] = v
    assert targets["demo.endian.on_dtype"] == "numpy.dtype.newbyteorder"
    assert targets["demo.endian.on_param"] == "numpy.ndarray.newbyteorder"
    assert targets["demo.endian.on_bare_dtype"] == "numpy.dtype.newbyteorder"
    assert targets["demo.endian.on_attr"] == "numpy.dtype.newbyteorder"
    # reassignment kills dtype-ness: the surviving use is an array call
    assert targets["demo.endian.on_reassigned"] == \
        "numpy.ndarray.newbyteorder"
