"""Verifier tests: canned tracebacks + live stdlib probes (no numpy needed)."""

import sys
from pathlib import Path

import pytest

from chainreaction.verifier import (
    frames_to_modules,
    parse_traceback,
    run_import_probe,
    run_snippet_probe,
    target_python,
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


def test_snippet_probe_maps_traceback_to_modules(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "svc.py").write_text(
        "def boom():\n    raise ValueError('nope')\n", encoding="utf-8")
    res = run_snippet_probe(sys.executable, tmp_path,
                            "from pkg.svc import boom\nboom()\n")
    assert res.ok is False
    assert res.modules == ["pkg.svc"]
    assert "ValueError" in res.stderr


def test_target_python_missing_env_errors():
    with pytest.raises(FileNotFoundError, match="make_target_envs"):
        target_python("nope")


def test_before_after_contrast_on_nibabel():
    before = run_import_probe(target_python("numpy1"), "targets/nibabel",
                              "nibabel")
    assert before.ok is True
    assert before.numpy_version == "1.26.4"
    assert before.modules == []
    after = run_import_probe(target_python("numpy2"), "targets/nibabel",
                             "nibabel")
    assert after.ok is False
    assert after.numpy_version == "2.0.2"
    assert after.modules == [
        "nibabel",
        "nibabel.analyze",
        "nibabel.arrayproxy",
        "nibabel.volumeutils",
        "nibabel.casting",
    ]
