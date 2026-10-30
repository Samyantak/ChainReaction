"""Verifier tests: canned tracebacks + live stdlib probes (no numpy needed)."""

import sys
from pathlib import Path

from chainreaction.verifier import (
    frames_to_modules,
    parse_traceback,
    run_import_probe,
)

CANNED = """\
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "E:/t/nibabel/__init__.py", line 52, in <module>
    from . import analyze
  File "E:/t/nibabel/analyze.py", line 88, in <module>
    from . import arrayproxy
  File "E:/t/nibabel/arrayproxy.py", line 39, in <module>
    from .volumeutils import shape_zoom_affine
  File "E:/t/nibabel/volumeutils.py", line 23, in <module>
    from .casting import type_info, ok_floats
  File "E:/t/nibabel/casting.py", line 723, in <module>
    OK_FLOATS = ok_floats()
  File "E:/t/nibabel/casting.py", line 717, in ok_floats
    floats = np.sctypes['float'][:]
AttributeError: `np.sctypes` was removed in the NumPy 2.0 release. ...
"""


def test_parse_traceback_frames_in_order():
    frames = parse_traceback(CANNED)
    assert [f.func for f in frames][:2] == ["<module>", "<module>"]
    assert frames[-1].file.endswith("casting.py")
    assert frames[-1].line == 717
    assert frames[-1].func == "ok_floats"
    assert len(frames) == 7


def test_frames_to_modules_dedupes_and_skips_foreign(tmp_path):
    root = tmp_path / "t"
    (root / "nibabel").mkdir(parents=True)
    for name in ["__init__.py", "analyze.py", "arrayproxy.py",
                 "volumeutils.py", "casting.py"]:
        (root / "nibabel" / name).write_text("x = 1\n", encoding="utf-8")
    real = CANNED.replace("E:/t", str(root))
    modules = frames_to_modules(parse_traceback(real), root)
    assert modules == [
        "nibabel",
        "nibabel.analyze",
        "nibabel.arrayproxy",
        "nibabel.volumeutils",
        "nibabel.casting",
    ]


def test_live_probe_success_on_stdlib(tmp_path):
    res = run_import_probe(sys.executable, tmp_path, "json")
    assert res.ok is True
    assert res.modules == []  # stdlib frames live outside the target root


def test_live_probe_failure_captured(tmp_path):
    res = run_import_probe(sys.executable, tmp_path, "definitely_missing_xyz")
    assert res.ok is False
    assert "ModuleNotFoundError" in res.stderr
