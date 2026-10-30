"""Predicted impact of removing numpy.sctypes on the nibabel snapshot."""

from chainreaction.graph.repo import build_graph_from_repo
from chainreaction.propagate import propagate


def main() -> int:
    g = build_graph_from_repo("targets/nibabel", "nibabel")
    impacts = propagate(g, {"numpy.sctypes"})

    hard = sorted(n for n, i in impacts.items() if i.status == "HARD")
    risky = sorted(n for n, i in impacts.items() if i.status == "AT_RISK")
    print(f"HARD={len(hard)} AT_RISK={len(risky)}")
    print("HARD modules:")
    for n in hard:
        if g.nodes[n].get("kind") == "module":
            print(f"  {n}")

    expected_hard = {
        "nibabel",
        "nibabel.analyze",
        "nibabel.arrayproxy",
        "nibabel.volumeutils",
        "nibabel.casting",
        "nibabel.nifti1",
        "nibabel.arraywriters",
        "nibabel.pointset",
    }
    missing = expected_hard - set(hard)
    print("via-chain for nibabel.analyze:")
    for hop in impacts["nibabel.analyze"].via:
        print(f"  {hop[0]} -> {hop[1]}  ({hop[2]}:{hop[3]})")

    if missing:
        print(f"MISSING HARD: {sorted(missing)}")
        return 1
    print("PROPAGATION_MATCH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
