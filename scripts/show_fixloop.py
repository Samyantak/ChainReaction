"""Fix-loop demo: hand-written unified diffs -> suite per iteration.

The LLM (propose_fix) generates these diffs once MODEL_API_KEY exists;
the fixtures below prove the loop mechanics on the real nibabel break.
Default verifies against the FULL suite (--tests selects a subset).
"""

import sys

from chainreaction.fixloop import Patch, run_fix_loop
from chainreaction.verifier import target_python

P1_CASTING_IMPORT = """\
--- a/nibabel/casting.py
+++ b/nibabel/casting.py
@@ -712,7 +712,10 @@ def ok_floats():
     \"\"\"Return floating point types sorted by precision

     Remove longdouble if it has no higher precision than float64
     \"\"\"
-    # copy float list so we don't change the numpy global
-    floats = np.sctypes['float'][:]
+    # NumPy 2.0 removed np.sctypes; list float types explicitly, keeping
+    # longdouble only where it adds precision (as sctypes did per-platform).
+    floats = [np.float16, np.float32, np.float64]
+    if np.finfo(np.longdouble).nmant > np.finfo(np.float64).nmant:
+        floats.append(np.longdouble)
     if best_float() != np.longdouble and np.longdouble in floats:
"""

P2_QUATERNIONS_IMPORT = """\
--- a/nibabel/quaternions.py
+++ b/nibabel/quaternions.py
@@ -30,4 +30,4 @@ import numpy as np
 import numpy as np

-MAX_FLOAT = np.maximum_sctype(float)
+MAX_FLOAT = np.longdouble
 FLOAT_EPS = np.finfo(float).eps
"""

# Offsets assume P1 already applied (+3 lines in casting.py).
P3_CASTING_RUNTIME = """\
--- a/nibabel/casting.py
+++ b/nibabel/casting.py
@@ -753,8 +753,8 @@ def able_int_type(values):
     mn = min(values)
     mx = max(values)
     if mn >= 0:
-        for ityp in np.sctypes['uint']:
+        for ityp in (np.uint8, np.uint16, np.uint32, np.uint64):
             if mx <= np.iinfo(ityp).max:
                 return ityp
-    for ityp in np.sctypes['int']:
+    for ityp in (np.int8, np.int16, np.int32, np.int64):
         info = np.iinfo(ityp)
"""

P4_TEST_CASTING = """\
--- a/nibabel/tests/test_casting.py
+++ b/nibabel/tests/test_casting.py
@@ -21,6 +21,11 @@ from ..casting import (
     ulp,
 )
 from ..testing import suppress_warnings


+_SCTYPES_FLOAT = [np.float16, np.float32, np.float64, np.longdouble]
+_SCTYPES_INT = [np.int8, np.int16, np.int32, np.int64]
+_SCTYPES_UINT = [np.uint8, np.uint16, np.uint32, np.uint64]
+
+
 def test_shared_range():
@@ -27,3 +32,3 @@ def test_shared_range():
-    for ft in np.sctypes['float']:
-        for it in np.sctypes['int'] + np.sctypes['uint']:
+    for ft in _SCTYPES_FLOAT:
+        for it in _SCTYPES_INT + _SCTYPES_UINT:
             # Test that going a bit above or below the calculated min and max
@@ -55,5 +60,5 @@ def test_shared_range():
             else:
                 assert np.all(bit_bigger <= casted_mx)
-            if it in np.sctypes['uint']:
+            if it in _SCTYPES_UINT:
                 assert mn == 0
                 continue
@@ -92,4 +97,4 @@ def test_casting():
 def test_casting():
-    for ft in np.sctypes['float']:
-        for it in np.sctypes['int'] + np.sctypes['uint']:
+    for ft in _SCTYPES_FLOAT:
+        for it in _SCTYPES_INT + _SCTYPES_UINT:
             ii = np.iinfo(it)
@@ -142,4 +147,4 @@ def test_int_abs():
 def test_int_abs():
-    for itype in np.sctypes['int']:
+    for itype in _SCTYPES_INT:
         info = np.iinfo(itype)
         in_arr = np.array([info.min, info.max], dtype=itype)
@@ -189,5 +194,5 @@ def test_able_casting():
 def test_able_casting():
     # Check the able_int_type function guesses numpy out type
-    types = np.sctypes['int'] + np.sctypes['uint']
+    types = _SCTYPES_INT + _SCTYPES_UINT
     for in_type in types:
         in_info = np.iinfo(in_type)
"""

P5_INT_ABS_COPY = """\
--- a/nibabel/casting.py
+++ b/nibabel/casting.py
@@ -599,7 +599,7 @@ def int_abs(arr):
     >>> int_abs(np.array([-128, 127], dtype=np.float32))
     array([128., 127.], dtype=float32)
     \"\"\"
-    arr = np.array(arr, copy=False)
+    arr = np.asarray(arr)
     dt = arr.dtype
     if dt.kind == 'u':
         return arr
"""

PATCHES = [
    Patch("P1", "casting.ok_floats: explicit float list (import blocker)",
          P1_CASTING_IMPORT),
    Patch("P2", "quaternions.MAX_FLOAT: np.longdouble (import blocker)",
          P2_QUATERNIONS_IMPORT),
    Patch("P3", "casting.able_int_type: explicit int lists (runtime)",
          P3_CASTING_RUNTIME),
    Patch("P4", "test_casting.py: local sctypes tables", P4_TEST_CASTING),
    Patch("P5", "casting.int_abs: np.asarray (copy-keyword change)",
          P5_INT_ABS_COPY),
]


def main() -> int:
    tests = next((a.split("=", 1)[1] for a in sys.argv
                  if a.startswith("--tests=")), "nibabel/tests")
    timeout = int(next((a.split("=", 1)[1] for a in sys.argv
                        if a.startswith("--timeout=")), "900"))
    python = next((a.split("=", 1)[1] for a in sys.argv
                   if a.startswith("--python=")), target_python("numpy2"))
    print(f"target=targets/nibabel tests={tests} timeout={timeout}s")
    print(f"python={python}")
    res = run_fix_loop(python, "targets/nibabel", PATCHES, tests,
                       timeout_per_run=timeout)
    for it in res.iterations:
        print(f"iter{it.index} applied={it.applied} green={it.green}")
        print(f"  {it.result.summary or '(no summary)'}")
    print(f"workdir={res.workdir} (snapshot untouched)")
    print("FIXLOOP_GREEN" if res.green else "FIXLOOP_PROGRESS (see above)")
    return 0 if res.green else 1


if __name__ == "__main__":
    raise SystemExit(main())
