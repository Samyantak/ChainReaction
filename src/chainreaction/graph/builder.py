"""AST graph builder skeleton.

Nodes: module, function/class, external symbol.
Edges: IMPORTS (tagged import-time vs in-function), CALLS (same-file calls).
Every node/edge stores file:line evidence. No LLM involved.
"""

import ast
from pathlib import Path

import networkx as nx


def build_graph_from_file(path: str | Path) -> nx.DiGraph:
    path = Path(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return build_graph_from_tree(tree, str(path))


def build_graph_from_tree(tree: ast.Module, filename: str) -> nx.DiGraph:
    g = nx.DiGraph()
    module = Path(filename).stem
    g.add_node(module, kind="module", file=filename, line=1)

    visitor = _Visitor(g, module, filename)
    visitor.visit(tree)
    return g


class _Visitor(ast.NodeVisitor):
    def __init__(self, g: nx.DiGraph, module: str, filename: str) -> None:
        self.g = g
        self.module = module
        self.filename = filename
        self.scope: list[str] = [module]  # module or function/class stack
        self.in_function = False

    def _current(self) -> str:
        return self.scope[-1]

    def _add_edge(self, target: str, kind: str, lineno: int, **attrs) -> None:
        self.g.add_edge(
            self._current(),
            target,
            kind=kind,
            file=self.filename,
            line=lineno,
            import_time=not self.in_function,
            **attrs,
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if not self.g.has_node(top):
                self.g.add_node(
                    top, kind="external", file=self.filename, line=node.lineno
                )
            self._add_edge(top, "IMPORTS", node.lineno, names=[alias.name])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = (node.module or "").split(".")[0]
        if base:
            if not self.g.has_node(base):
                self.g.add_node(
                    base, kind="external", file=self.filename, line=node.lineno
                )
            names = [a.name for a in node.names]
            self._add_edge(base, "IMPORTS", node.lineno, names=names)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter_def(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter_def(node, "function")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter_def(node, "class")

    def _enter_def(self, node, kind: str) -> None:
        qual = f"{self.module}.{node.name}"
        self.g.add_node(
            qual, kind=kind, file=self.filename, line=node.lineno
        )
        self.scope.append(qual)
        prev, self.in_function = self.in_function, True
        self.generic_visit(node)
        self.in_function = prev
        self.scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = self._call_name(node.func)
        if name:
            self._add_edge(name, "CALLS", node.lineno)
        self.generic_visit(node)

    @staticmethod
    def _call_name(func: ast.AST) -> str | None:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            parts = []
            cur = func
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
                return ".".join(reversed(parts))
        return None
