"""Fix loop tests on a synthetic package (fast, no nibabel)."""

import sys

import pytest

from chainreaction.fixloop import (
    Patch,
    PatchApplyError,
    propose_fix,
    run_fix_loop,
    run_suite,
)

CALC = 'def f():\n    """Doc."""\n\n    return 1 / 0\n'
TEST = "from calc import f\n\ndef test_f():\n    assert f() == 1\n"
# NB: the blank context line below is intentionally bare (no space) to
# exercise _normalize_diff; do not "fix" it.
DIFF = """\
--- a/calc.py
+++ b/calc.py
@@ -1,4 +1,4 @@
 def f():
     \"\"\"Doc.\"\"\"

-    return 1 / 0
+    return 1
"""


def _proj(tmp_path):
    # LF endings, mirroring real tarball-extracted targets on any OS.
    (tmp_path / "calc.py").write_text(CALC, encoding="utf-8", newline="\n")
    (tmp_path / "test_calc.py").write_text(TEST, encoding="utf-8", newline="\n")
    return tmp_path


def test_loop_goes_red_to_green(tmp_path):
    target = _proj(tmp_path)
    res = run_fix_loop(sys.executable, target,
                       [Patch("p1", "fix division", DIFF)], ".",
                       timeout_per_run=120)
    assert res.green is True
    assert len(res.iterations) == 1
    assert res.iterations[0].result.passed == 1
    # snapshot untouched: the bug is still in the original tree
    assert (target / "calc.py").read_text(encoding="utf-8") == CALC


def test_suite_result_counts_failures(tmp_path):
    target = _proj(tmp_path)
    res = run_suite(sys.executable, target, ".", timeout=120)
    assert res.returncode != 0
    assert res.failed == 1 and res.passed == 0
    assert "failed" in res.summary


def test_bad_patch_raises_with_id(tmp_path):
    target = _proj(tmp_path)
    with pytest.raises(PatchApplyError, match="p-bad"):
        run_fix_loop(sys.executable, target,
                       [Patch("p-bad", "nope", "not a diff\n")], ".",
                       timeout_per_run=60)


def test_empty_patch_list_stays_red(tmp_path):
    res = run_fix_loop(sys.executable, _proj(tmp_path), [], ".")
    assert res.green is False and res.iterations == []


def test_propose_fix_stub_names_the_blocker():
    with pytest.raises(NotImplementedError, match="MODEL_API_KEY"):
        propose_fix("numpy.sctypes", "casting.py:717")


def test_old_workdirs_pruned_keeping_latest(tmp_path):
    from pathlib import Path

    target = _proj(tmp_path)
    first = run_fix_loop(sys.executable, target,
                         [Patch("p1", "fix division", DIFF)], ".",
                         timeout_per_run=120)
    second = run_fix_loop(sys.executable, target,
                          [Patch("p1", "fix division", DIFF)], ".",
                          timeout_per_run=120)
    assert Path(first.workdir).exists() is False
    assert Path(second.workdir).exists() is True
