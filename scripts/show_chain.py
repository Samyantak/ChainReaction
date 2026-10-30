"""Reproduce the nibabel import chain from the graph alone (demo script).

Expected (arch doc): nibabel/__init__ -> ... -> analyze -> arrayproxy
-> volumeutils -> casting, where casting executes ok_floats() at import
(line 723) which reads np.sctypes at line 717.
"""

import networkx as nx

from chainreaction.graph.repo import build_graph_from_repo


def main() -> int:
    g = build_graph_from_repo("targets/nibabel", "nibabel")
    print(f"nodes={g.number_of_nodes()} edges={g.number_of_edges()}")

    import_time = nx.DiGraph(
        (u, v, d)
        for u, v, d in g.edges(data=True)
        if d["kind"] == "IMPORTS" and d.get("import_time")
    )
    try:
        path = nx.shortest_path(import_time, "nibabel", "nibabel.casting")
    except nx.NetworkXNoPath:
        print("NO import-time path nibabel -> nibabel.casting")
        return 1
    print("shortest import-time path to nibabel.casting:")
    for u, v in zip(path, path[1:]):
        d = g.edges[u, v]
        print(f"  {u} -> {v}  ({d['file']}:{d['line']})")

    expected = [
        ("nibabel", "nibabel.analyze"),
        ("nibabel.analyze", "nibabel.arrayproxy"),
        ("nibabel.arrayproxy", "nibabel.volumeutils"),
        ("nibabel.volumeutils", "nibabel.casting"),
    ]
    print("expected traceback hops (all must be import-time IMPORTS):")
    hops_ok = True
    for u, v in expected:
        d = g.edges.get((u, v))
        ok = d is not None and d["kind"] == "IMPORTS" and d.get("import_time")
        hops_ok &= ok
        loc = f"{d['file']}:{d['line']}" if d else "MISSING"
        print(f"  {'OK ' if ok else 'MISS'} {u} -> {v}  ({loc})")

    print("all import-time importers of nibabel.casting:")
    for u, v, d in g.in_edges("nibabel.casting", data=True):
        if d["kind"] == "IMPORTS" and d.get("import_time"):
            print(f"  {u}  ({d['file']}:{d['line']})")

    print("EXECUTES_AT_IMPORT from nibabel.casting:")
    for _, v, d in g.out_edges("nibabel.casting", data=True):
        if d["kind"] == "EXECUTES_AT_IMPORT":
            print(f"  {v}  ({d['file']}:{d['line']})")

    print("USES_EXTERNAL numpy.sctypes:")
    for u, v, d in g.edges(data=True):
        if d["kind"] == "USES_EXTERNAL" and v == "numpy.sctypes":
            print(f"  {u}  ({d['file']}:{d['line']})")

    sctypes_ok = any(
        d["kind"] == "USES_EXTERNAL" and v == "numpy.sctypes"
        and u == "nibabel.casting.ok_floats" and d["line"] == 717
        for u, v, d in g.edges(data=True)
    )
    trig_ok = any(
        d["kind"] == "EXECUTES_AT_IMPORT" and v == "nibabel.casting.ok_floats"
        for _, v, d in g.out_edges("nibabel.casting", data=True)
    )
    ok = hops_ok and sctypes_ok and trig_ok
    print("CHAIN_MATCH" if ok else "CHAIN_DIFFERS (see above)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
