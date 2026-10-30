"""Deterministic propagation engine (no LLM).

Rules (architecture doc):
- A use of a broken symbol at module level, or in a def executed at import,
  fails the module HARD. All import-time importers fail HARD (transitively);
  in-function importers of a HARD module become AT_RISK.
- A use inside a def that is not executed at import marks the def AT_RISK;
  its callers become AT_RISK (transitively). A module executing an impacted
  def at import fails HARD.
- HARD wins over AT_RISK. Every impact keeps its full via-chain with
  file:line evidence.

Guards (try/except ImportError, version checks) are not detected yet.
"""

from dataclasses import dataclass, field

import networkx as nx


@dataclass
class Impact:
    status: str  # "HARD" or "AT_RISK"
    symbol: str  # broken external symbol at the root
    via: list[tuple[str, str, str, int]] = field(default_factory=list)
    # via hops: (from_node, to_node, file, line)


def propagate(g: nx.DiGraph, broken: set[str]) -> dict[str, Impact]:
    impacts: dict[str, Impact] = {}
    defs = _def_index(g)

    def mark(node: str, status: str, symbol: str, via) -> bool:
        cur = impacts.get(node)
        if cur is None:
            impacts[node] = Impact(status=status, symbol=symbol, via=list(via))
            return True
        if cur.status == "AT_RISK" and status == "HARD":
            impacts[node] = Impact(status=status, symbol=symbol, via=list(via))
            return True
        return False

    # 1. Seeds: direct uses of broken symbols.
    for u, v, d in g.edges(data=True):
        if d.get("kind") != "USES_EXTERNAL":
            continue
        sym = _matches(v, broken)
        if sym is None:
            continue
        seed_hop = [(u, v, d["file"], d["line"])]
        kind = g.nodes[u].get("kind") if u in g.nodes else None
        if kind == "module":
            mark(u, "HARD", sym, seed_hop)
        else:
            executed = [
                (m, dd)
                for m, _, dd in g.in_edges(u, data=True)
                if dd.get("kind") == "EXECUTES_AT_IMPORT"
            ]
            if executed:
                m, dd = executed[0]
                mark(m, "HARD", sym, [(m, u, dd["file"], dd["line"])] + seed_hop)
            mark(u, "AT_RISK", sym, seed_hop)

    # 2. HARD closure over importers (fixpoint).
    changed = True
    while changed:
        changed = False
        for u, v, d in g.edges(data=True):
            if d.get("kind") != "IMPORTS":
                continue
            imp = impacts.get(v)
            if imp is None or imp.status != "HARD":
                continue
            hop = [(u, v, d["file"], d["line"])]
            if d.get("import_time"):
                changed |= mark(u, "HARD", imp.symbol, hop + imp.via)
            elif u not in impacts:
                changed |= mark(u, "AT_RISK", imp.symbol, hop + imp.via)

    # 3. AT_RISK closure over callers + import-time executors (fixpoint).
    callers: dict[str, list[tuple[str, dict]]] = {}
    for u, v, d in g.edges(data=True):
        if d.get("kind") == "EXECUTES_AT_IMPORT":
            target: str | None = v  # always a qualified def node
        elif d.get("kind") == "CALLS":
            target = _resolve_call(g, u, v, defs)
        else:
            continue
        if target is None:
            continue
        callers.setdefault(target, []).append((u, d))

    changed = True
    while changed:
        changed = False
        for target, ups in callers.items():
            imp = impacts.get(target)
            if imp is None:
                continue
            for u, d in ups:
                hop = [(u, target, d["file"], d["line"])]
                if d.get("kind") == "EXECUTES_AT_IMPORT":
                    changed |= mark(u, "HARD", imp.symbol, hop + imp.via)
                elif u not in impacts:
                    changed |= mark(u, "AT_RISK", imp.symbol, hop + imp.via)

    return impacts


def predicted_chain(g: nx.DiGraph, hops: list[tuple[str, str, str, int]]
                    ) -> list[str]:
    """Project via-style hops onto module nodes, order kept, deduped."""
    chain: list[str] = []
    seen: set[str] = set()
    for u, v, _file, _line in hops:
        for node in (u, v):
            if (node not in seen and node in g.nodes
                    and g.nodes[node].get("kind") == "module"):
                seen.add(node)
                chain.append(node)
    return chain


def failure_path(g: nx.DiGraph, impacts: dict[str, Impact],
                 broken: set[str], package: str
                 ) -> list[tuple[str, str, str, int]]:
    """First-failure path from the package root, in execution order.

    Statuses from propagate() say WHAT breaks (order-independent), but a
    node's via records whichever cause won the closure race — usually the
    shortest path, not the one Python executes first. This reconstructs
    the runtime path: at each module it follows the earliest-line failing
    cause (failing import, module-level broken use, or call into an
    impacted def), mirroring top-down module execution. Returns via hops
    (from, to, file, line); [] when the package import is predicted clean.
    """
    if package not in impacts:
        return []
    defs = _def_index(g)
    hops: list[tuple[str, str, str, int]] = []
    visited: set[str] = set()
    node = package
    while node not in visited:
        visited.add(node)
        kind = g.nodes[node].get("kind") if node in g.nodes else None
        cands: list[tuple[int, tuple[str, str, str, int], str | None]] = []
        for _, v, d in g.out_edges(node, data=True):
            edge_kind = d.get("kind")
            if kind == "module":
                if edge_kind == "IMPORTS" and d.get("import_time"):
                    imp = impacts.get(v)
                    if imp is not None and imp.status == "HARD":
                        cands.append((d["line"], (node, v, d["file"], d["line"]), v))
                elif edge_kind == "USES_EXTERNAL" and _matches(v, broken):
                    cands.append((d["line"], (node, v, d["file"], d["line"]), None))
                elif edge_kind == "EXECUTES_AT_IMPORT" and v in impacts:
                    cands.append((d["line"], (node, v, d["file"], d["line"]), v))
            elif edge_kind == "USES_EXTERNAL" and _matches(v, broken):
                cands.append((d["line"], (node, v, d["file"], d["line"]), None))
            elif edge_kind == "CALLS":
                target = _resolve_call(g, node, v, defs) if "." not in v else v
                if target in impacts:
                    cands.append((d["line"], (node, v, d["file"], d["line"]), target))
            elif edge_kind == "IMPORTS":
                imp = impacts.get(v)
                # in-function import of a HARD module: descend into it
                if imp is not None and imp.status == "HARD":
                    cands.append((d["line"], (node, v, d["file"], d["line"]), v))
            elif edge_kind == "EXECUTES_AT_IMPORT" and v in impacts:
                cands.append((d["line"], (node, v, d["file"], d["line"]), v))
        if not cands:
            break
        _, hop, nxt = min(cands, key=lambda c: c[0])
        hops.append(hop)
        if nxt is None:
            break
        node = nxt
    return hops


def _matches(target: str, broken: set[str]) -> str | None:
    for sym in broken:
        if target == sym or target.startswith(sym + "."):
            return sym
    return None


def _owner_module(g: nx.DiGraph, node: str) -> str:
    """Nearest enclosing module of a node (walks dotted prefixes)."""
    parts = node.split(".")
    for i in range(len(parts), 0, -1):
        cand = ".".join(parts[:i])
        if cand in g.nodes and g.nodes[cand].get("kind") == "module":
            return cand
    return parts[0]


def _is_def(g: nx.DiGraph, node: str) -> bool:
    # NB: add_edge implicitly creates a node for the raw call name, so
    # `in g.nodes` is always true — only nodes with a recorded kind count.
    return g.nodes[node].get("kind") in ("function", "class") if node in g.nodes else False


def _def_index(g: nx.DiGraph) -> dict[str, dict[str, list[str]]]:
    index: dict[str, dict[str, list[str]]] = {}
    for node, data in g.nodes(data=True):
        if data.get("kind") in ("function", "class"):
            short = node.rpartition(".")[2]
            module = _owner_module(g, node)
            index.setdefault(module, {}).setdefault(short, []).append(node)
    return index


def _resolve_call(
    g: nx.DiGraph, caller: str, name: str, defs: dict[str, dict[str, list[str]]]
) -> str | None:
    if "." in name:
        head, _, short = name.partition(".")
        if head in ("self", "cls"):
            scope = caller.rpartition(".")[0]  # enclosing class, if any
            cand = f"{scope}.{short}" if scope else short
            return cand if _is_def(g, cand) else None
        return name if _is_def(g, name) else None
    module = _owner_module(g, caller)
    cands = defs.get(module, {}).get(name, [])
    # Bare calls see module globals, never methods — prefer the
    # module-level def; a lone same-named method still resolves (lenient).
    if f"{module}.{name}" in cands:
        return f"{module}.{name}"
    return cands[0] if len(cands) == 1 else None
