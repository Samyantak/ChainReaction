"""Graph builder skeleton tests (repo's own tests for the touched area)."""

from chainreaction.graph import build_graph_from_tree
import ast


SAMPLE = '''\
import numpy as np
from os import path

def use_sctypes():
    return np.sctypes["float"]

class Holder:
    def make(self):
        return use_sctypes()
'''


def test_imports_and_calls_recorded_with_evidence():
    g = build_graph_from_tree(ast.parse(SAMPLE), "sample.py")

    assert g.nodes["sample"]["kind"] == "module"
    assert g.nodes["numpy"]["kind"] == "external"

    imports = [
        (u, v, d)
        for u, v, d in g.edges(data=True)
        if d["kind"] == "IMPORTS"
    ]
    assert imports, "expected IMPORTS edges"
    for _, _, d in imports:
        assert d["file"] == "sample.py"
        assert d["line"] >= 1
        assert d["import_time"] is True  # top-level imports

    calls = [
        (u, v, d)
        for u, v, d in g.edges(data=True)
        if d["kind"] == "CALLS"
    ]
    assert any(v == "use_sctypes" for _, v, _ in calls)


def test_in_function_import_tagged_call_time():
    src = "def f():\n    import json\n    return json.dumps({})\n"
    g = build_graph_from_tree(ast.parse(src), "m.py")
    edges = [
        (u, v, d)
        for u, v, d in g.edges(data=True)
        if d["kind"] == "IMPORTS"
    ]
    assert len(edges) == 1
    assert edges[0][2]["import_time"] is False
    assert edges[0][0] == "m.f"
