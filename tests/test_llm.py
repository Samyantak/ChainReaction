"""Live-LLM surfaces with the network mocked out (offline-safe).

chat() itself is covered by the no-key error path; interpret/propose_fix/
generate_narrative run against canned model replies, and run_auto_loop
drives a tiny real target tree through red to green.
"""

import json
import sys

import pytest

from chainreaction import explain, fixloop, interpret
from chainreaction.explain import Report
from chainreaction.fixloop import (
    Patch,
    SuiteResult,
    failed_tests,
    propose_fix,
    run_auto_loop,
)
from chainreaction.interpret import ChangeItem
from chainreaction.llm import chat, extract_json

GUIDE = (
    "NumPy 2.0 migration guide. np.sctypes was removed; use explicit "
    "dtype lists instead. The copy keyword behavior changed."
)

MODEL_ITEMS = {
    "items": [
        {"symbol": "numpy.sctypes",
         "kind": "removed_symbol",
         "quote": "np.sctypes was removed; use explicit dtype lists instead.",
         "replacement": "explicit dtype lists"},
        {"symbol": "numpy.fake",
         "kind": "removed_symbol",
         "quote": "paraphrased words that appear nowhere in the guide"},
    ]
}


def _no_key(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)


def test_extract_json_fenced_and_bare():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('prefix {"a": 2} suffix') == {"a": 2}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_chat_no_key(monkeypatch):
    _no_key(monkeypatch)
    with pytest.raises(RuntimeError, match="MODEL_API_KEY"):
        chat("sys", "hi")


def test_interpret_keeps_verified_drops_paraphrase(monkeypatch):
    seen = {}

    def fake_chat(system, user, **kw):
        seen["system"] = system
        seen["user"] = user
        return json.dumps(MODEL_ITEMS)

    monkeypatch.setattr("chainreaction.interpret.chat", fake_chat)
    cs = interpret.interpret("upgrade numpy", GUIDE, "http://guide")
    assert [i.symbol for i in cs.items] == ["numpy.sctypes"]
    assert cs.items[0].verified is True
    assert "upgrade numpy" in seen["user"]
    assert GUIDE in seen["user"]
    assert "exact" in seen["system"].lower()


def test_interpret_no_key(monkeypatch):
    _no_key(monkeypatch)
    with pytest.raises(RuntimeError, match="MODEL_API_KEY"):
        interpret.interpret("upgrade numpy", GUIDE)


def test_propose_fix_builds_patch(monkeypatch, tmp_path):
    (tmp_path / "mod.py").write_text("a = 1\nb = 2\nc = 3\n",
                                     encoding="utf-8")
    diff = ("--- a/mod.py\n+++ b/mod.py\n@@ -1,3 +1,3 @@\n"
            " a = 1\n-b = 2\n+b = 20\n c = 3\n")
    seen = {}

    def fake_chat(system, user, **kw):
        seen["user"] = user
        return json.dumps({"description": "bump b", "diff": diff})

    monkeypatch.setattr("chainreaction.fixloop.chat", fake_chat)
    patch = propose_fix("mod.b", "mod.py:2", tmp_path, patch_id="P-9")
    assert isinstance(patch, Patch)
    assert patch.id == "P-9"
    assert patch.diff == diff
    assert "2: b = 2" in seen["user"]


def test_propose_fix_rejects_bad_evidence_and_no_diff(monkeypatch, tmp_path):
    (tmp_path / "mod.py").write_text("a = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="file.py:line"):
        propose_fix("s", "noline", tmp_path)
    with pytest.raises(ValueError, match="file.py:line"):
        propose_fix("s", "missing.py:1", tmp_path)
    monkeypatch.setattr(
        "chainreaction.fixloop.chat",
        lambda *a, **k: json.dumps({"description": "x", "diff": "oops"}))
    with pytest.raises(ValueError, match="unified diff"):
        propose_fix("s", "mod.py:1", tmp_path)


def test_generate_narrative(monkeypatch):
    seen = {}

    def fake_chat(system, user, **kw):
        seen["system"] = system
        seen["user"] = user
        return "NARRATIVE"

    monkeypatch.setattr("chainreaction.explain.chat", fake_chat)
    report = Report(decision="d", target="t", package="nibabel",
                    numpy_version="2.0.2", change_symbols=["numpy.sctypes"],
                    hard=["nibabel.casting"], at_risk=[],
                    spine=["nibabel.casting"])
    assert explain.generate_narrative(report) == "NARRATIVE"
    assert "nibabel.casting" in seen["user"]
    assert "spine_match" in seen["user"]


def test_failed_tests_parses_summary():
    res = SuiteResult(returncode=1,
                      tail="FAILED a.py::t1 - assert\nFAILED b.py::t2 - boom\n"
                           "1 passed, 2 failed")
    assert failed_tests(res) == ["a.py::t1", "b.py::t2"]
    assert failed_tests(SuiteResult(returncode=0, tail="3 passed")) == []


def _buggy_target(tmp_path, value):
    (tmp_path / "buggy.py").write_text(f"VALUE = {value}\n",
                                       encoding="utf-8")
    (tmp_path / "test_buggy.py").write_text(
        "from buggy import VALUE\n\n\n"
        "def test_v():\n    assert VALUE == 2\n", encoding="utf-8")


def test_run_auto_loop_red_to_green(tmp_path):
    _buggy_target(tmp_path, 1)
    diff = ("--- a/buggy.py\n+++ b/buggy.py\n@@ -1 +1 @@\n"
            "-VALUE = 1\n+VALUE = 2\n")

    def propose(nodeid):
        assert nodeid == "test_buggy.py::test_v"
        return Patch("P-x", "set VALUE=2", diff)

    res = run_auto_loop(sys.executable, tmp_path, "test_buggy.py",
                        propose, timeout_per_run=180, max_rounds=2)
    assert res.green is True
    assert len(res.iterations) == 1
    assert res.iterations[0].applied == ["P-r1"]


def test_run_auto_loop_already_green(tmp_path):
    _buggy_target(tmp_path, 2)
    res = run_auto_loop(sys.executable, tmp_path, "test_buggy.py",
                        lambda nodeid: (_ for _ in ()).throw(
                            AssertionError("must not propose")),
                        timeout_per_run=180, max_rounds=2)
    assert res.green is True
    assert res.iterations == []


def test_clear_pycache_removes_caches(tmp_path):
    nested = tmp_path / "pkg" / "__pycache__"
    nested.mkdir(parents=True)
    (nested / "m.pyc").write_text("x", encoding="utf-8")
    (tmp_path / "pkg" / "m.py").write_text("x = 1\n", encoding="utf-8")
    fixloop._clear_pycache(tmp_path)
    assert not nested.exists()
    assert (tmp_path / "pkg" / "m.py").is_file()


def test_change_item_verified_flag_roundtrip():
    item = ChangeItem(symbol="s", kind="k", quote="q")
    assert item.verified is False
