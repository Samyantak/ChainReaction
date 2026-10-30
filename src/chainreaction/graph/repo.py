"""Repo-level graph builder: walks a package, links intra-package importers.

Edges: IMPORTS (import-time vs in-function), EXECUTES_AT_IMPORT (module-level
calls of local defs), USES_EXTERNAL (aliased external attribute use, e.g.
np.sctypes). All carry file:line evidence. No LLM involved.
"""

import ast
from pathlib import Path

import networkx as nx


def build_graph_from_repo(root: str | Path, package: str) -> nx.DiGraph:
    root = Path(root)
    pkg_dir = root / package
    g = nx.DiGraph()
    files = sorted(pkg_dir.rglob("*.py"))
    for path in files:
        module = _module_name(pkg_dir, package, path)
        g.add_node(module, kind="module", file=str(path), line=1)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        is_package = path.name == "__init__.py"
        _FileVisitor(g, module, package, str(path), is_package).visit(tree)
    _link_parent_inits(g, package)
    return g


def _link_parent_inits(g: nx.DiGraph, package: str) -> None:
    """Link each module to its ancestor packages (outermost first).

    Importing a submodule executes every ancestor __init__ first, so a
    module under a HARD package fails at import even when its own code
    is clean. failure_path ignores these edges (it walks the import
    chain, not the package tree); the HARD closure follows them.
    """
    for node, data in list(g.nodes(data=True)):
        if data.get("kind") != "module":
            continue
        if node != package and not node.startswith(package + "."):
            continue
        parts = node.split(".")
        for i in range(1, len(parts)):
            anc = ".".join(parts[:i])
            if anc in g.nodes and g.nodes[anc].get("kind") == "module":
                # A real IMPORTS edge already covers this pair (with a
                # better line); in-function-only ancestor imports stay a
                # known gap.
                if not g.has_edge(node, anc):
                    g.add_edge(node, anc, kind="PARENT_INIT",
                               file=data["file"], line=1, import_time=True)


def _module_name(pkg_dir: Path, package: str, path: Path) -> str:
    rel = path.relative_to(pkg_dir).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return package if not parts else f"{package}." + ".".join(parts)


class _FileVisitor(ast.NodeVisitor):
    def __init__(self, g: nx.DiGraph, module: str, package: str,
                 filename: str, is_package: bool = False) -> None:
        self.g = g
        self.module = module
        self.package = package
        self.filename = filename
        self.is_package = is_package
        self.scope: list[str] = [module]
        self.in_function = False
        self.aliases: dict[str, str] = {}  # local name -> dotted external root
        self.local_defs: set[str] = set()

    def _current(self) -> str:
        return self.scope[-1]

    def _edge(self, target: str, kind: str, lineno: int, **attrs) -> None:
        u = self._current()
        if self.g.has_edge(u, target):
            data = self.g[u][target]
            if data.get("kind") == kind:
                # Repeat occurrence: keep the FIRST (earliest-executed)
                # evidence, but OR the import_time flag — one module-level
                # occurrence means importing u executes this edge.
                if lineno < data["line"]:
                    data["line"] = lineno
                    data["file"] = self.filename
                    if "names" in attrs:
                        data["names"] = attrs["names"]
                if "import_time" in attrs:
                    data["import_time"] = (bool(data.get("import_time"))
                                           or bool(attrs["import_time"]))
                return
        self.g.add_edge(
            u, target, kind=kind,
            file=self.filename, line=lineno, **attrs,
        )

    # -- imports --

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            dotted = alias.name
            local = alias.asname or dotted.split(".")[0]
            target = self._resolve_absolute(dotted)
            if target is None:
                self.aliases[local] = dotted
                target = dotted.split(".")[0]
                if not self.g.has_node(target):
                    self.g.add_node(target, kind="external",
                                    file=self.filename, line=node.lineno)
            else:
                if alias.asname:
                    self.aliases[local] = target
            self._edge(target, "IMPORTS", node.lineno,
                       import_time=not self.in_function, names=[dotted])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        names = [a.name for a in node.names]
        if node.level and not node.module:
            # 'from . import foo' -> submodule foo of the parent package
            pkg = self._parent_package()
            for a in node.names:
                if a.name == "*":
                    continue
                self.aliases[a.asname or a.name] = f"{pkg}.{a.name}"
                self._edge(f"{pkg}.{a.name}", "IMPORTS", node.lineno,
                           import_time=not self.in_function, names=[a.name])
            self.generic_visit(node)
            return
        if node.level:
            base = self._resolve_relative(node.level, node.module or "")
        else:
            base = node.module or ""
        if base.startswith(self.package):
            target = base
            for a in node.names:
                # every binding, not just `as`: `from .b import f` must
                # connect later `f()` calls to base.f
                self.aliases[a.asname or a.name] = f"{base}.{a.name}"
        elif base:
            top = base.split(".")[0]
            if not self.g.has_node(top):
                self.g.add_node(top, kind="external",
                                file=self.filename, line=node.lineno)
            target = top
            for a in node.names:
                local = a.asname or a.name
                if local != "*":
                    self.aliases[local] = f"{base}.{a.name}"
        else:  # pragma: no cover - 'from' with empty module is a syntax error
            self.generic_visit(node)
            return
        self._edge(target, "IMPORTS", node.lineno,
                   import_time=not self.in_function, names=names)
        self.generic_visit(node)

    def _resolve_absolute(self, dotted: str) -> str | None:
        if dotted == self.package or dotted.startswith(self.package + "."):
            return dotted
        return None

    def _resolve_relative(self, level: int, module: str) -> str:
        pkg = self._parent_package()
        for _ in range(level - 1):
            pkg = pkg.rpartition(".")[0] or self.package
        return f"{pkg}.{module}" if module else pkg

    def _parent_package(self) -> str:
        if self.is_package or self.module == self.package:
            return self.module
        return self.module.rpartition(".")[0]

    # -- defs --

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter_def(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter_def(node, "function")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter_def(node, "class")

    def _enter_def(self, node, kind: str) -> None:
        qual = f"{self.scope[-1]}.{node.name}"
        if len(self.scope) == 1:
            self.local_defs.add(node.name)
        self.g.add_node(qual, kind=kind, file=self.filename, line=node.lineno)
        self.scope.append(qual)
        prev, self.in_function = self.in_function, True
        self.generic_visit(node)
        self.in_function = prev
        self.scope.pop()

    # -- calls and external uses --

    def _resolved(self, name: str) -> str | None:
        """Expand the root through import aliases; None when unbound."""
        root, _, rest = name.partition(".")
        if root not in self.aliases:
            return None
        base = self.aliases[root]
        return f"{base}.{rest}" if rest else base

    def _is_internal(self, dotted: str) -> bool:
        return dotted == self.package or dotted.startswith(self.package + ".")

    def visit_Call(self, node: ast.Call) -> None:
        name = self._dotted(node.func)
        if name:
            root = name.split(".")[0]
            if not self.in_function and root in self.local_defs:
                self._edge(f"{self.module}.{root}", "EXECUTES_AT_IMPORT",
                           node.lineno)
                self.generic_visit(node)
                return
            resolved = self._resolved(name)
            if resolved is None:
                self._edge(name, "CALLS", node.lineno)
            elif self._is_internal(resolved):
                self._edge(resolved, "CALLS", node.lineno)
            else:
                self._edge(resolved, "USES_EXTERNAL", node.lineno)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # attribute *reads* like np.sctypes outside a Call (e.g. subscript value)
        dotted = self._dotted(node)
        if dotted:
            root, _, rest = dotted.partition(".")
            resolved = self._resolved(dotted) if rest else None
            # External reads are symbol uses; internal reads need no edge
            # (module failure already travels over IMPORTS).
            if (resolved is not None and root in self.aliases
                    and not self._is_internal(resolved)):
                self._edge(resolved, "USES_EXTERNAL", node.lineno)
        self.generic_visit(node)

    @staticmethod
    def _dotted(func: ast.AST) -> str | None:
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
