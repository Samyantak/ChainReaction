"""Live verify: real import probe under the installed numpy vs predictions.

Compares the actual traceback chain against the propagation prediction,
and reports grep + ruff baselines. `--peel` shims np.sctypes to reveal
the next failure layer.
"""

import sys

from chainreaction.baselines import grep_hits, ruff_npy201
from chainreaction.graph.repo import build_graph_from_repo
from chainreaction.propagate import propagate
from chainreaction.scorer import chain_match, precision_recall
from chainreaction.verifier import run_import_probe

TARGET = "targets/nibabel"
PACKAGE = "nibabel"
EXPECTED_SPINE = [
    "nibabel",
    "nibabel.analyze",
    "nibabel.arrayproxy",
    "nibabel.volumeutils",
    "nibabel.casting",
]

# Layer peeling: restore the np.sctypes table NumPy 2.0 removed.
PEEL_PREAMBLE = """\
import numpy as np
if not hasattr(np, "sctypes"):
    np.sctypes = {
        "float": [np.float16, np.float32, np.float64, np.longdouble],
        "int": [np.int8, np.int16, np.int32, np.int64],
        "uint": [np.uint8, np.uint16, np.uint32, np.uint64],
        "complex": [np.complex64, np.complex128, np.clongdouble],
        "bool": [np.bool_],
        "object": [np.object_],
    }
"""


def main() -> int:
    peel = "--peel" in sys.argv
    print(f"target={TARGET} package={PACKAGE} peel={peel}")

    g = build_graph_from_repo(TARGET, PACKAGE)
    impacts = propagate(g, {"numpy.sctypes"})
    hard = {n for n, i in impacts.items() if i.status == "HARD"}

    res = run_import_probe(sys.executable, TARGET, PACKAGE,
                           preamble=PEEL_PREAMBLE if peel else "")
    print(f"probe ok={res.ok} numpy={res.numpy_version}")
    if res.numpy_version is None or res.numpy_version == "MISSING":
        print("numpy is not installed for this interpreter; "
              "run: pip install \"numpy>=2\"")
        return 2
    major = int(res.numpy_version.split(".")[0])
    if major < 2 and not peel:
        print(f"WARNING: numpy {res.numpy_version} < 2; "
              "the NumPy 2.0 break will not reproduce")

    print("actual chain:")
    for m in res.modules:
        print(f"  {m}  {'HARD (predicted)' if m in hard else 'NOT PREDICTED'}")

    score = chain_match(EXPECTED_SPINE, res.modules)
    print(f"spine match={score.match} missing={score.missing} extra={score.extra}")
    pr = precision_recall(
        {n for n in hard if g.nodes[n].get("kind") == "module"},
        set(res.modules),
    )
    print(f"module-level precision={pr.precision:.2f} recall={pr.recall:.2f} "
          f"(predicted {len(pr.predicted)}, actual {len(pr.actual)})")

    if not res.ok:
        print("--- error tail ---")
        print("\n".join(res.stderr.splitlines()[-4:]))

    hits = grep_hits(f"{TARGET}/nibabel", [r"np\.sctypes"])
    nontest = [h for h in hits if "/tests/" not in h.file and "\\tests\\" not in h.file]
    print(f"grep baseline: {len(hits)} np.sctypes hits ({len(nontest)} non-test)")

    ruff = ruff_npy201(f"{TARGET}/nibabel")
    if ruff is None:
        print("ruff baseline: ruff not installed (pip install ruff)")
    else:
        print(f"ruff baseline: {len(ruff)} NPY201 hits")

    if peel:
        print("PEEL DONE: ok=" + str(res.ok))
        return 0
    ok = score.match and pr.recall == 1.0
    print("VERIFY_MATCH" if ok else "VERIFY_DIFFERS (see above)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
