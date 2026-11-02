"""Deterministic propagation engine (no LLM).

Rules (architecture doc):
- A use of a broken symbol at module level, or in a def executed at import,
  fails the module HARD. All import-time importers fail HARD (transitively);
  in-function importers of a HARD module become AT_RISK.
- A use inside a def that is not executed at import marks the def AT_RISK;
  its callers become AT_RISK (transitively). A module executing an impacted
  def at import fails HARD.
- A config break the decision probe reaches through calls fails its module
  HARD, the same way an import-time removal does: the probe will hit it.
  Config the probe cannot reach stays AT_RISK.
- HARD wins over AT_RISK. Every impact keeps its full via-chain with
  file:line evidence.

Guards (try/except ImportError, version checks) are not detected yet.
"""

import ast
from dataclasses import dataclass, field

import networkx as nx

from chainreaction.interpret import ChangeItem


@dataclass
class Impact:
    status: str  # "HARD" or "AT_RISK"
    symbol: str  # broken external symbol at the root
    via: list[tuple[str, str, str, int]] = field(default_factory=list)
    # via hops: (from_node, to_node, file, line)


def resolve_symbol(g: nx.DiGraph, package: str, symbol: str) -> str:
    """Resolve a change symbol to a graph node (exact or nearest enclosing).

    Intra-package symbols name definitions (closed world), so an
    unresolvable one is a typo â€” fail fast instead of silently matching
    nothing. External symbols never reach this function.
    """
    if symbol != package and not symbol.startswith(package + "."):
        raise ValueError(f"change symbol outside package {package}: {symbol!r}")
    kinds = ("module", "function", "class")
    if symbol in g.nodes and g.nodes[symbol].get("kind") in kinds:
        return symbol
    # One strip only: module.NAME and module.Class.NAME are legitimate
    # non-def names (Provider, PRICING); deeper misses are typos.
    parent = symbol.rpartition(".")[0] if "." in symbol else ""
    if parent and parent in g.nodes and g.nodes[parent].get("kind") in kinds:
        return parent
    raise ValueError(f"unresolvable change symbol: {symbol!r}")


def propagate(g: nx.DiGraph, broken: set[str],
              config: list[ChangeItem] | tuple = (),
              package: str = "",
              entries: tuple[str, ...] = ()) -> dict[str, Impact]:
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

    # 0. Config seeds: definitions that cannot serve the decision.
    # Unreached config is AT_RISK; entries and reached breaks are marked
    # in step 2.5, after the import closure — a call-time vendor break
    # must not fail importers the way an import-time removal does.
    if config and not package:
        raise ValueError("config items need the package name to resolve")
    reach = _vendor_reach(g, entries) if entries else {}
    for item in config:
        node = resolve_symbol(g, package, item.symbol)
        if node not in reach:
            mark(node, "AT_RISK", item.symbol, [])

    # 1. Seeds: direct uses of broken symbols.
    for u, v, d in g.edges(data=True):
        if d.get("kind") != "USES_EXTERNAL":
            continue
        sym = matches_symbol(v, broken)
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
            if d.get("kind") not in ("IMPORTS", "PARENT_INIT"):
                continue
            imp = impacts.get(v)
            if imp is None or imp.status != "HARD":
                continue
            hop = [(u, v, d["file"], d["line"])]
            if d.get("import_time"):
                changed |= mark(u, "HARD", imp.symbol, hop + imp.via)
            elif u not in impacts:
                changed |= mark(u, "AT_RISK", imp.symbol, hop + imp.via)

    # 2.5. Vendor seeds: the probe reaches these breaks through calls,
    # so their modules fail HARD (the probe will hit them), like an
    # import-time removal — but without failing their importers, since
    # importing a module with a call-time break still works. Entries
    # ride along as AT_RISK: the probe path runs through them. Via
    # lists the forward probe path from the entry; the ChangeItem code
    # quote evidences the site itself.
    if entries:
        for item in config:
            node = resolve_symbol(g, package, item.symbol)
            if node in reach:
                module = (node if g.nodes[node].get("kind") == "module"
                          else _owner_module(g, node))
                mark(module, "HARD", item.symbol, reach[node][2])
                if node != module:
                    mark(node, "AT_RISK", item.symbol, [])
        for entry in entries:
            if entry in g.nodes and _is_def(g, entry):
                mark(entry, "AT_RISK", entry, [])

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


def _genuinely_fails(g: nx.DiGraph, impacts: dict[str, Impact],
                     broken: set[str], node: str, executing: set[str],
                     _seen: frozenset = frozenset()) -> bool:
    """True when importing node fails given `executing` are in sys.modules.

    Structural, race-free: a HARD marking keeps one recorded via, which
    may show only the ancestor-init cause even when a genuine import
    cause exists (closure-order artifact). Re-evaluate live causes
    instead — a module-level broken use, an executed impacted def, a
    genuinely-failing import, or a genuinely-failing ancestor init —
    with already-executing modules contributing nothing.
    """
    imp = impacts.get(node)
    if imp is None or imp.status != "HARD":
        return False
    if node in _seen:
        return False  # import cycle: this cause alone proves nothing
    seen = _seen | {node}
    for _, w, d in g.out_edges(node, data=True):
        kind = d.get("kind")
        if kind == "USES_EXTERNAL" and matches_symbol(w, broken):
            return True
        if kind == "EXECUTES_AT_IMPORT" and w in impacts:
            return True
        if kind in ("IMPORTS", "PARENT_INIT"):
            if kind == "IMPORTS" and not d.get("import_time"):
                continue
            wimp = impacts.get(w)
            if (wimp is not None and wimp.status == "HARD"
                    and w not in executing
                    and _genuinely_fails(g, impacts, broken, w,
                                         executing, seen)):
                return True
    return False


def failure_path(g: nx.DiGraph, impacts: dict[str, Impact],
                 broken: set[str], package: str
                 ) -> list[tuple[str, str, str, int]]:
    """First-failure path from the package root, in execution order.

    Statuses from propagate() say WHAT breaks (order-independent), but a
    node's via records whichever cause won the closure race â€” usually the
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
                    if (imp is not None and imp.status == "HARD"
                            and _genuinely_fails(g, impacts, broken, v,
                                                 visited)):
                        cands.append((d["line"], (node, v, d["file"], d["line"]), v))
                elif edge_kind == "USES_EXTERNAL" and matches_symbol(v, broken):
                    cands.append((d["line"], (node, v, d["file"], d["line"]), None))
                elif edge_kind == "EXECUTES_AT_IMPORT" and v in impacts:
                    cands.append((d["line"], (node, v, d["file"], d["line"]), v))
            elif edge_kind == "USES_EXTERNAL" and matches_symbol(v, broken):
                cands.append((d["line"], (node, v, d["file"], d["line"]), None))
            elif edge_kind == "CALLS":
                target = _resolve_call(g, node, v, defs) if "." not in v else v
                if target in impacts:
                    cands.append((d["line"], (node, v, d["file"], d["line"]), target))
            elif edge_kind == "IMPORTS":
                imp = impacts.get(v)
                # in-function import of a HARD module: descend into it
                if (imp is not None and imp.status == "HARD"
                        and _genuinely_fails(g, impacts, broken, v,
                                             visited)):
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


def matches_symbol(target: str, broken: set[str]) -> str | None:
    """Broken symbol matched by an edge target (exact or parent prefix)."""
    # Sorted: set order varies across processes (PYTHONHASHSEED), and the
    # winner must be identical on live and replay runs.
    for sym in sorted(broken):
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
    # `in g.nodes` is always true â€” only nodes with a recorded kind count.
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
    # Bare calls see module globals, never methods â€” prefer the
    # module-level def; a lone same-named method still resolves (lenient).
    if f"{module}.{name}" in cands:
        return f"{module}.{name}"
    return cands[0] if len(cands) == 1 else None


def _call_dotted(func: ast.AST) -> str | None:
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


def probe_entries(probe_code: str, package: str, g: nx.DiGraph) -> list[str]:
    """Package defs the decision probe calls (its entry points).

    Tracks package imports, locals constructed from package classes
    (`client = MeteredLLMClient(...)`), and calls through them
    (`client.call(...)`), plus direct calls of imported defs. Names
    missing from the graph are skipped; [] means the probe's entries
    are unknown and vendor prediction degrades to AT_RISK.
    """
    if not probe_code:
        return []
    try:
        tree = ast.parse(probe_code)
    except SyntaxError:
        return []
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == package or a.name.startswith(package + "."):
                    imported[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level or not (node.module or "").startswith(package):
                continue
            for a in node.names:
                if a.name != "*":
                    imported[a.asname or a.name] = (
                        f"{node.module}.{a.name}")
    constructed: dict[str, str] = {}
    for node in ast.walk(tree):
        targets = []
        value = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        if not isinstance(value, ast.Call):
            continue
        cls = _call_dotted(value.func)
        if cls in imported:
            for target in targets:
                if isinstance(target, ast.Name):
                    constructed[target.id] = imported[cls]
    entries = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _call_dotted(node.func)
        if not dotted:
            continue
        cand = None
        if "." in dotted:
            head, _, rest = dotted.partition(".")
            if head in constructed and "." not in rest:
                cand = f"{constructed[head]}.{rest}"
            elif head in imported:
                cand = f"{imported[head]}.{rest}"
        elif dotted in imported:
            cand = imported[dotted]
        if (cand is not None and cand in g.nodes
                and g.nodes[cand].get("kind") in ("function", "class")):
            entries.add(cand)
    return sorted(entries)


def _calls_succ(g: nx.DiGraph, defs) -> dict[str, list]:
    """Resolved CALLS adjacency over defs: u -> [(target, raw, file, line)]."""
    succ: dict[str, list] = {}
    for u, raw, d in g.edges(data=True):
        if d.get("kind") != "CALLS" or not _is_def(g, u):
            continue
        target = _resolve_call(g, u, raw, defs)
        if target is None or not _is_def(g, target):
            continue
        succ.setdefault(u, []).append((target, raw, d["file"], d["line"]))
    for hops in succ.values():
        hops.sort(key=lambda e: (e[3], e[0]))
    return succ


def _vendor_reach(g: nx.DiGraph, entries) -> dict:
    """Nodes the probe reaches: node -> (depth, first_line, path hops).

    Depth 0 is an entry and its enclosing scopes (class, module); deeper
    nodes follow resolved CALLS edges. Modules also inherit reach from
    their reached defs. first_line is the CALLS edge line leaving the
    entry (0 at depth 0); path lists the forward hops from the entry.
    """
    defs = _def_index(g)
    succ = _calls_succ(g, defs)
    reach: dict = {}
    for entry in sorted(entries):
        if entry not in g.nodes or not _is_def(g, entry):
            continue
        node = entry
        while node and node in g.nodes and g.nodes[node].get("kind") in (
                "function", "class", "module"):
            reach.setdefault(node, (0, 0, []))
            node = node.rpartition(".")[0] if "." in node else ""
    queue = sorted(e for e in entries if e in reach)
    while queue:
        nxt = []
        for u in queue:
            depth, first, path = reach[u]
            for target, raw, path_, line in succ.get(u, []):
                if target in reach:
                    continue
                hop = (u, raw, path_, line)
                reach[target] = (depth + 1, first or line, path + [hop])
                nxt.append(target)
        queue = sorted(nxt)
    for node, data in g.nodes(data=True):
        if data.get("kind") != "module" or node in reach:
            continue
        cands = [(reach[d][0], reach[d][1], reach[d][2])
                 for d in defs_all_in(g, node) if d in reach]
        if cands:
            reach[node] = min(cands)
    return reach


def defs_all_in(g: nx.DiGraph, module: str) -> list[str]:
    """Every function/class defined under a module."""
    prefix = module + "."
    return [n for n, d in g.nodes(data=True)
            if d.get("kind") in ("function", "class")
            and (n == module or n.startswith(prefix))]


_UNREACHED = 10 ** 9


def order_config_by_entry(g: nx.DiGraph, entries, items: list,
                          package: str) -> list:
    """Config items in probe-hit order (entry scopes first, then by CALLS).

    Sort key is (CALLS depth from the entry, first edge line out of the
    entry, symbol); unreached items sort last by (code line, symbol).
    The order is the vendor fix order: fixing the first item and
    re-probing reveals the next, like import-layer peeling.
    """
    reach = _vendor_reach(g, entries) if entries else {}
    keyed = []
    for item in items:
        node = resolve_symbol(g, package, item.symbol)
        if node in reach:
            depth, first, _ = reach[node]
            line = first if depth else (getattr(item, "code_line", 0) or 0)
            keyed.append(((depth, line, item.symbol), item))
        else:
            keyed.append(((_UNREACHED, getattr(item, "code_line", 0) or 0,
                           item.symbol), item))
    keyed.sort(key=lambda pair: pair[0])
    return [item for _, item in keyed]


def vendor_chain(g: nx.DiGraph, package: str, entries,
                 ordered: list) -> list[tuple[str, str, str, int]]:
    """First-failure call path: probe entry to the rejecting site.

    One hop (entry def, break module, code file, code line) grounded in
    the first ordered code-verified config item; [] when entries are
    unknown or no item carries code grounding. The spine projects this
    onto modules, matching the probe's actual modules on success.
    """
    funcs = sorted(e for e in entries
                   if e in g.nodes and g.nodes[e].get("kind") == "function")
    cands = funcs or sorted(entries)
    if not cands:
        return []
    primary = cands[0]
    for item in ordered:
        if (getattr(item, "source", "") != "code"
                or not getattr(item, "verified", False)
                or not getattr(item, "code_file", "")
                or not getattr(item, "code_line", 0)):
            continue
        node = resolve_symbol(g, package, item.symbol)
        module = (node if g.nodes[node].get("kind") == "module"
                  else _owner_module(g, node))
        return [(primary, module, item.code_file, item.code_line)]
    return []
