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
            # NB: add_edge implicitly creates a node for the raw name, so
            # `v in g.nodes` is always true — resolve bare names explicitly.
            target = _resolve_call(g, u, v, defs) if "." not in v else None
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


def _matches(target: str, broken: set[str]) -> str | None:
    for sym in broken:
        if target == sym or target.startswith(sym + "."):
            return sym
    return None


def _def_index(g: nx.DiGraph) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for node, data in g.nodes(data=True):
        if data.get("kind") in ("function", "class"):
            module, _, name = node.rpartition(".")
            index.setdefault(module, set()).add(name)
    return index


def _resolve_call(
    g: nx.DiGraph, caller: str, name: str, defs: dict[str, set[str]]
) -> str | None:
    if "." in name:
        return name if name in g.nodes else None
    kind = g.nodes[caller].get("kind") if caller in g.nodes else None
    module = caller if kind == "module" else caller.rpartition(".")[0]
    if name in defs.get(module, ()):
        return f"{module}.{name}"
    return None
