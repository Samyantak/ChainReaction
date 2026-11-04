"""M5 fix loop tests: edits, diffs, guards, loop on synthetic pkgs."""

import json
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from chainreaction import fixloop
from chainreaction.fixloop import (
    Edit,
    apply_edits,
    build_round_prompt,
    call_fix_llm,
    correspondent_tests,
    file_window,
    find_usages,
    fix_prompt_hash,
    guard_checks,
    modules_for_files,
    parse_edits,
    recorded_llm_ms,
    run_loop,
    select_targeted,
    site_symbols,
    skipped_record,
    stubbed_probe_code,
    unified_diff_for,
    validate_edits,
    workdir_diff,
)

CALC = 'def f():\n    """Doc."""\n\n    return 1 / 0\n'


def _proj(tmp_path, package="pkg"):
    """Tiny package tree: pkg/mod.py + test file (LF endings)."""
    pkg = tmp_path / package
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from pkg.mod import VALUE\n",
                                     encoding="utf-8", newline="\n")
    (pkg / "mod.py").write_text("VALUE = 1\n", encoding="utf-8",
                                newline="\n")
    (tmp_path / "test_mod.py").write_text(
        "from pkg.mod import VALUE\n\n\n"
        "def test_v():\n    assert VALUE == 2\n",
        encoding="utf-8", newline="\n")
    return tmp_path


def test_parse_edits_valid_and_errors():
    edits, error = parse_edits({
        "edits": [{"path": "pkg/mod.py", "search": "a",
                   "replace": "b", "test_file": False}],
        "rationale": "r", "addresses": ["s"]})
    assert error is None and len(edits) == 1
    assert edits[0].test_file is False
    assert parse_edits([])[1] == "response is not a JSON object"
    assert parse_edits({})[1] == "response has no edits array"
    assert parse_edits({"edits": []})[1] == "response has no edits array"
    assert parse_edits({"edits": [{}]})[1].startswith("edit 0 needs")
    assert parse_edits(
        {"edits": [{"path": "a", "search": "x\x00"}]})[1] == \
        "edit 0 contains NUL bytes"


def test_validate_edits_accepts_package_source(tmp_path):
    root = _proj(tmp_path)
    edits = [Edit("pkg/mod.py", "VALUE = 1", "VALUE = 2")]
    assert validate_edits(edits, root, "pkg") == []


def test_validate_edits_rejects(tmp_path):
    root = _proj(tmp_path)
    (root / "pkg" / "dup.py").write_text("x = 1\nx = 1\n",
                                         encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (root / "other.py").write_text("y = 1\n", encoding="utf-8")
    cases = [
        ([Edit("pkg/missing.py", "a", "b")], "no such file"),
        ([Edit("../escape.py", "a", "b")], "escapes the tree"),
        ([Edit("pyproject.toml", "[project]", "x")],
         "not a Python source file"),
        ([Edit("other.py", "y = 1", "y = 2")], "outside the package"),
        ([Edit("test_mod.py", "VALUE == 2", "x")], "test files need"),
        ([Edit("pkg/mod.py", "absent", "x")],
         "search occurs 0 times, need exactly 1"),
        ([Edit("pkg/dup.py", "x = 1", "x = 2")],
         "search occurs 2 times, need exactly 1"),
    ]
    for edits, needle in cases:
        errors = validate_edits(edits, root, "pkg")
        assert any(needle in e for e in errors), (edits, errors)


def test_validate_edits_packaging_python_rejected(tmp_path):
    root = _proj(tmp_path)
    (root / "setup.py").write_text("from setuptools import setup\n",
                                   encoding="utf-8")
    errors = validate_edits([Edit("setup.py", "setup", "x")], root, "pkg")
    assert any("packaging/CI" in e for e in errors)


def test_validate_edits_allows_flagged_test_only(tmp_path):
    root = _proj(tmp_path)
    edit = Edit("test_mod.py", "VALUE == 2", "VALUE == 2",
                test_file=True)
    assert validate_edits([edit], root, "pkg",
                           test_only_ok=True) == []
    # Flagged but the round is not test-only: still rejected.
    assert validate_edits([edit], root, "pkg",
                           test_only_ok=False) != []


def test_apply_edits_splices_and_keeps_endings(tmp_path):
    root = _proj(tmp_path)
    crlf = root / "pkg" / "crlf.py"
    crlf.write_bytes(b"a = 1\r\nb = 2\r\n")
    touched = apply_edits(root, [Edit("pkg/mod.py", "VALUE = 1",
                                      "VALUE = 2"),
                                 Edit("pkg/crlf.py", "b = 2", "b = 3")])
    assert touched == ["pkg/crlf.py", "pkg/mod.py"]
    assert (root / "pkg" / "mod.py").read_text(encoding="utf-8") == \
        "VALUE = 2\n"
    assert (root / "pkg" / "crlf.py").read_bytes() == b"a = 1\r\nb = 3\r\n"


def test_apply_edits_rejects_moved_match(tmp_path):
    root = _proj(tmp_path)
    with pytest.raises(ValueError, match="exactly once"):
        apply_edits(root, [Edit("pkg/mod.py", "absent", "x")])


def test_unified_diff_deterministic_and_git_appliable(tmp_path):
    rel = "pkg/mod.py"
    old, new = "a = 1\nb = 2\n", "a = 1\nb = 3\n"
    first = unified_diff_for(rel, old, new)
    assert first == unified_diff_for(rel, old, new)
    assert first.startswith(f"--- a/{rel}\n+++ b/{rel}\n@@ ")
    assert unified_diff_for(rel, old, old) == ""
    # The patch applies to a pristine copy with git apply.
    target = tmp_path / "target"
    (target / "pkg").mkdir(parents=True)
    (target / rel).write_text(old, encoding="utf-8", newline="\n")
    check = subprocess.run(["git", "apply", "--check", "-"],
                           input=first.encode(), cwd=str(target),
                           capture_output=True, timeout=60)
    assert check.returncode == 0, check.stderr.decode()
    proc = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"],
                          input=first.encode(), cwd=str(target),
                          capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode()
    assert (target / rel).read_text(encoding="utf-8") == new


def test_unified_diff_marks_missing_trailing_newline(tmp_path):
    rel = "pkg/mod.py"
    diff = unified_diff_for(rel, "a = 1\n", "a = 2")
    assert "\\ No newline at end of file" in diff
    target = tmp_path / "t"
    (target / "pkg").mkdir(parents=True)
    (target / rel).write_text("a = 1\n", encoding="utf-8", newline="\n")
    proc = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"],
                          input=diff.encode(), cwd=str(target),
                          capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode()
    assert (target / rel).read_bytes() == b"a = 2"


def test_workdir_diff_sorted_and_skips_identical(tmp_path):
    src = tmp_path / "src"
    work = tmp_path / "work"
    for base in (src, work):
        (base / "pkg").mkdir(parents=True)
        (base / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (base / "pkg" / "b.py").write_text("y = 1\n", encoding="utf-8")
    (work / "pkg" / "b.py").write_text("y = 2\n", encoding="utf-8")
    (work / "pkg" / "a.py").write_text("x = 9\n", encoding="utf-8")
    diff = workdir_diff(src, work, ["pkg/b.py", "pkg/a.py",
                                   "pkg/missing.py"])
    assert diff.index("a/pkg/a.py") < diff.index("a/pkg/b.py")
    assert "missing.py" not in diff


def test_file_window_numbered_and_clamped(tmp_path):
    root = tmp_path
    (root / "m.py").write_text("l1\nl2\nl3\n", encoding="utf-8")
    assert file_window(root, "m.py", 2, radius=1) == \
        "1: l1\n2: l2\n3: l3"
    assert file_window(root, "m.py", 99, radius=40).startswith("1: l1")
    assert file_window(root, "nope.py", 1) == ""


def _graph():
    import networkx as nx

    g = nx.MultiDiGraph()
    g.add_edge("pkg.mod", "numpy.NaN", kind="USES_EXTERNAL",
               file="pkg/mod.py", line=10)
    g.add_edge("pkg.mod", "numpy.NaN", kind="USES_EXTERNAL",
               file="pkg/other.py", line=3)
    g.add_edge("pkg.mod", "os.path", kind="USES_EXTERNAL",
               file="pkg/mod.py", line=1)
    g.add_edge("pkg.mod", "pkg.sub", kind="IMPORTS", file="pkg/mod.py",
               line=1)
    return g


def test_find_usages_and_site_symbols():
    g = _graph()
    assert find_usages(g, "numpy.NaN") == ["pkg/mod.py:10",
                                           "pkg/other.py:3"]
    assert find_usages(None, "numpy.NaN") == []
    assert site_symbols(g, "pkg/mod.py", 10, {"numpy.NaN"}) == \
        {"numpy.NaN"}
    assert site_symbols(g, "pkg/mod.py", 1, {"numpy.NaN"}) == set()


def test_in_tree_frames_deepest_first(tmp_path):
    from chainreaction.fixloop import in_tree_frames
    from chainreaction.verifier import Frame

    frames = [Frame(str(tmp_path / "pkg" / "a.py"), 3, "f"),
              Frame("/other/site/x.py", 9, "g"),
              Frame(str(tmp_path / "pkg" / "b.py"), 7, "h")]
    assert in_tree_frames(frames, tmp_path, "pkg") == ["pkg/b.py:7",
                                                       "pkg/a.py:3"]
    assert in_tree_frames([], tmp_path, "pkg") == []


def test_parse_traceback_reads_pytest_headers():
    from chainreaction.verifier import parse_traceback

    out = ("_____\n"
           "tests/test_x.py:12: in test_a\n"
           "    assert 1 == 2\n"
           "E   AssertionError\n")
    frames = parse_traceback(out)
    assert [(f.file, f.line, f.func) for f in frames] == [
        ("tests/test_x.py", 12, "test_a")]
    mixed = ('  File "/a/b.py", line 3, in f\n'
             "==== pkg/c.py:9: in g\n")
    assert [(f.file, f.line) for f in parse_traceback(mixed)] == [
        ("/a/b.py", 3), ("pkg/c.py", 9)]
    # Summary and error lines never match.
    assert parse_traceback("FAILED a.py::t - msg\n"
                           "E   ValueError: x\n") == []


def test_in_tree_frames_resolves_relative_to_root(tmp_path):
    from chainreaction.fixloop import in_tree_frames
    from chainreaction.verifier import Frame

    (tmp_path / "tests").mkdir()
    frames = [Frame("tests/test_x.py", 12, "test_a"),
              Frame("/elsewhere/y.py", 1, "f")]
    assert in_tree_frames(frames, tmp_path, "pkg") == ["tests/test_x.py:12"]


def test_test_only_ok_rejects_unlocated_failures():
    loop = fixloop._Loop(SimpleNamespace(state={"graph": None,
                                                "symbols": set()}),
                         None)
    assert loop.test_only_ok({"site": None}, []) is False
    assert loop.test_only_ok({"site": "pkg/mod.py:3"}, []) is False
    assert loop.test_only_ok({"site": "tests/test_x.py:3"}, []) is True


def test_counts_keeps_error_to_failure_red():
    """A test failing now in a red-proof error file is still failing."""
    loop = fixloop._Loop(SimpleNamespace(state={}), None)
    loop.suite_failed = {"a.py::t1", "b.py::t2", "c.py::t3"}
    loop.suite_files = set()
    loop.suite_fresh = True
    loop.red_failed = set()
    loop.red_files = {"a.py"}
    loop.red_provenance = "measured (pristine work tree)"
    loop.baseline_failed = {"c.py::t3"}
    loop.baseline_files = set()
    counts = loop.counts()
    assert counts["still_failing"] == ["a.py::t1"]
    assert counts["newly_broken"] == ["b.py::t2"]
    assert counts["fixed"] == []
    # A collapsed red collection (directory entry) keeps everything
    # under it red, in both dimensions.
    loop.red_files = {"pkg"}
    loop.suite_failed = {"pkg/a.py::t1", "other.py::t2"}
    loop.suite_files = {"pkg/b.py", "other2.py"}
    counts = loop.counts()
    assert counts["still_failing"] == ["pkg/a.py::t1"]
    assert counts["newly_broken"] == ["other.py::t2"]
    assert counts["error_files"]["still_failing"] == ["pkg/b.py"]
    assert counts["error_files"]["newly_broken"] == ["other2.py"]
    # Baseline failures stay excluded even under a red error dir.
    loop.baseline_failed = {"pkg/a.py::t1", "c.py::t3"}
    counts = loop.counts()
    assert counts["still_failing"] == []
    assert counts["newly_broken"] == ["other.py::t2"]


def test_before_targeted_falls_back_to_full_on_small_suites():
    plan = SimpleNamespace(target_rel="t", package="pkg",
                           source_root=".", before_env="before")
    loop = fixloop._Loop(SimpleNamespace(state={"testmap": None}),
                         plan)
    seen = []

    def fake_run_suite(tests, env, timeout, label):
        seen.append((tests, env))
        return {"failed": set(), "error_files": set(),
                "counts": {"passed": 3}, "total": 3,
                "escalated": False}, "out"

    loop.run_suite = fake_run_suite
    rec = loop.run_before_targeted(["pkg/mod.py"], collected=10)
    assert rec["ran"] is True
    assert rec["method"] == "full-suite (no covering tests)"
    assert seen == [([], "before")]
    rec = loop.run_before_targeted(["pkg/mod.py"], collected=5000)
    assert rec == {"ran": False,
                   "reason": ("no file-correspondence tests for "
                              "edited files")}


def test_test_import_windows_recovers_source(tmp_path):
    from chainreaction.fixloop import test_import_windows

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("VALUE = 1\n",
                                             encoding="utf-8")
    (tmp_path / "test_mod.py").write_text(
        "from pkg.mod import VALUE\n\n\n"
        "def test_v():\n    assert VALUE == 2\n",
        encoding="utf-8")
    assert test_import_windows(tmp_path, "test_mod.py::test_v",
                               "pkg") == [("pkg/mod.py", 1)]
    assert test_import_windows(tmp_path, "missing.py::t", "pkg") == []


def test_test_import_windows_resolves_relative(tmp_path):
    from chainreaction.fixloop import test_import_windows

    (tmp_path / "pkg" / "tests").mkdir(parents=True)
    (tmp_path / "pkg" / "casting.py").write_text("x = 1\n",
                                                 encoding="utf-8")
    (tmp_path / "pkg" / "tests" / "test_c.py").write_text(
        "from ..casting import x\n", encoding="utf-8")
    assert test_import_windows(tmp_path, "pkg/tests/test_c.py::t",
                               "pkg") == [("pkg/casting.py", 1)]


def test_build_round_prompt_deterministic():
    kwargs = dict(round_no=1, max_rounds=5, fix_order=["a: b:1"],
                  failure_kind="probe", failure_site="pkg/mod.py:10",
                  error_line="NameError: x",
                  trace_excerpt="File \"m\", line 10", items=[],
                  windows={"pkg/mod.py": "10: x"}, usages={},
                  applied_summaries=[])
    first = build_round_prompt(**kwargs)
    assert first == build_round_prompt(**kwargs)
    assert "Round 1 of 5" in first
    assert "pkg/mod.py:10" in first
    assert first.endswith(
        "Propose search/replace edits fixing exactly this failure.\n")
    with_feedback = build_round_prompt(feedback="try again", **kwargs)
    assert "try again" in with_feedback


def test_guard_checks_catch_all_four_violations():
    assert guard_checks(["pkg/a.py"],
                        ["@pytest.mark.skip(reason='x')"], []) != []
    assert guard_checks(["pkg/a.py"], ["x = pytest.xfail(1)"], []) != []
    assert guard_checks(["t.py"], [], ["def test_gone():"]) != []
    assert guard_checks(["pkg/a.py"], ["except Exception: pass"],
                        []) == ["added except-pass silencer"]
    assert guard_checks(["pkg/a.py"], ["except:", "    pass"], []) != []
    assert guard_checks(["pkg/a.py"],
                        ["except (Exception, ValueError): pass"],
                        []) != []
    assert guard_checks(["pkg/a.py"], ["except Exception as e:",
                                       "    pass"], []) != []
    # Specific exceptions with a pass fallback are defensive, not
    # silencing (flask-login's PackageNotFoundError shim).
    assert guard_checks(["pkg/a.py"],
                        ["except PackageNotFoundError: pass"], []) == []
    assert guard_checks(["pkg/a.py"], ["except ValueError:",
                                       "    pass"], []) == []
    assert guard_checks(["pyproject.toml"], ["x"], [])[0].endswith(
        "dependency/packaging file edited")
    # Honest fixes stay clean.
    assert guard_checks(["pkg/a.py"], ["x = np.nan"], ["x = np.NaN"]) == \
        []
    # except with a real handler is not a silencer.
    assert guard_checks(["pkg/a.py"], ["except ValueError:",
                                       "    raise"], []) == []


def test_stub_probe_completes_meta_path_without_network(tmp_path):
    # The real sponsorfit package is unavailable here; prove the stub
    # mechanics (fake client shape, routing assert, socket block).
    harness = ("import sys, types\n"
               "cli = open('client.py').read()\n"
               "mod = types.ModuleType('client')\n"
               "exec(compile(cli, 'client.py', 'exec'), mod.__dict__)\n"
               "sys.modules['client'] = mod\n")
    client = ("import openai\n"
              "def run():\n"
              "    c = openai.OpenAI(base_url='https://api.meta.ai/v1')\n"
              "    r = c.chat.completions.create(model='m', messages=[])\n"
              "    return r.choices[0].message.content\n")
    (tmp_path / "client.py").write_text(client, encoding="utf-8")
    good = stubbed_probe_code(harness + "import client\nprint(client.run())\n")
    proc = subprocess.run([sys.executable, "-c", good], cwd=str(tmp_path),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "STUB_BASE_URL=https://api.meta.ai/v1" in proc.stdout
    assert "STUB_CALLER=" in proc.stdout
    assert "client.py" in proc.stdout
    assert "stub reply" in proc.stdout


def test_stub_caller_frames_maps_probe_site(tmp_path):
    from chainreaction.fixloop import stub_caller_frames

    (tmp_path / "pkg").mkdir()
    target = tmp_path / "pkg" / "llm.py"
    target.write_text("x = 1\n", encoding="utf-8")
    err = ("stub: Meta call path never routed (base_url=None "
           f"caller={target}:160)")
    assert stub_caller_frames(err, tmp_path, "pkg") == ["pkg/llm.py:160"]
    assert stub_caller_frames("no caller here", tmp_path, "pkg") == []
    assert stub_caller_frames("", tmp_path, "pkg") == []


def test_stub_probe_rejects_unrouted_and_live_sockets(tmp_path):
    bad = stubbed_probe_code("print('no client call')\n")
    proc = subprocess.run([sys.executable, "-c", bad], cwd=str(tmp_path),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0
    assert "never routed" in proc.stderr or "never routed" in proc.stdout
    sock = stubbed_probe_code(
        "import socket\nsocket.create_connection(('x', 1))\n")
    proc = subprocess.run([sys.executable, "-c", sock], cwd=str(tmp_path),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0
    assert "network blocked" in proc.stderr


def test_select_targeted_prefers_coverage(tmp_path):
    from chainreaction.testmap import TestMap

    testmap = TestMap("t", "pkg", "",
                      {"a.py::t1": ["pkg.mod"], "b.py::t2": ["pkg.o"]},
                      collected=10)
    method, tests = select_targeted(testmap, ["pkg/mod.py"], "t", "pkg",
                                    10, tmp_path)
    assert (method, tests) == ("coverage", ["a.py::t1"])
    method, tests = select_targeted(testmap, ["pkg/mod.py"], "t", "pkg",
                                    1, tmp_path)
    assert tests == [] and method.startswith("full-suite")
    method, tests = select_targeted(testmap, ["pkg/nowhere.py"], "t",
                                    "pkg", 10, tmp_path)
    assert tests == [] and "full suite" in method


def test_select_targeted_falls_back_to_correspondence(tmp_path):
    root = tmp_path
    (root / "pkg" / "tests").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (root / "pkg" / "tests" / "test_mod.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8")
    method, tests = select_targeted(None, ["pkg/mod.py"], ".", "pkg",
                                    0, root)
    assert (method, tests) == ("file-correspondence",
                               ["pkg/tests/test_mod.py"])
    method, tests = select_targeted(None, ["pkg/lonely.py"], ".", "pkg",
                                    0, root)
    assert (method, tests) == (
        "full-suite (no correspondent tests)", [])
    assert correspondent_tests(root, "pkg", []) == []


def test_modules_for_files_skips_unmappable(tmp_path):
    root = _proj(tmp_path)
    assert modules_for_files(["pkg/mod.py", "test_mod.py", "nope.py"],
                             root, "pkg") == {"pkg.mod"}


def test_call_fix_llm_times_out():
    class Slow:
        cancelled = False

        def llm(self, *a, **k):
            import time as _time
            _time.sleep(5)
            return "{}"

    started = __import__("time").monotonic()
    with pytest.raises(TimeoutError):
        call_fix_llm(Slow(), "sys", "user", timeout_s=1)
    assert __import__("time").monotonic() - started < 5


def test_fix_prompt_hash_stable_and_effort_scoped(tmp_path):
    from chainreaction import llm as llm_mod

    ctx = SimpleNamespace(manifest={"model": "m"})
    first = fix_prompt_hash(ctx, "sys", "user")
    assert first == fix_prompt_hash(ctx, "sys", "user")
    assert first == llm_mod.cache_key_for("m", "sys", "user",
                                          fixloop.FIX_RESPONSE_FORMAT,
                                          "high")
    assert first != llm_mod.cache_key_for("m", "sys", "user",
                                          fixloop.FIX_RESPONSE_FORMAT)


def test_recorded_llm_ms_consumes_with_cursor(tmp_path):
    rows = [{"prompt_hash": "h", "duration_ms": 100},
            {"prompt_hash": "other", "duration_ms": 5},
            {"prompt_hash": "h", "duration_ms": 50}]
    (tmp_path / "llm_calls.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    ctx = SimpleNamespace(dir=tmp_path)
    total, cursor = recorded_llm_ms(ctx, "h")
    assert (total, cursor) == (150, 2)
    assert recorded_llm_ms(ctx, "h", cursor) == (0, 2)
    assert recorded_llm_ms(ctx, "missing") == (0, 0)


def test_copy_source_keeps_git_skips_caches(tmp_path):
    src = tmp_path / "src"
    (src / ".git").mkdir(parents=True)
    (src / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (src / "pkg" / "__pycache__").mkdir(parents=True)
    (src / "pkg" / "__pycache__" / "m.pyc").write_text("x",
                                                       encoding="utf-8")
    (src / "pkg" / "m.py").write_text("x = 1\n", encoding="utf-8")
    work = fixloop.copy_source_to_fixwork(src, tmp_path / "work")
    # .git carries over (setuptools_scm needs it); caches never do.
    assert (work / ".git" / "HEAD").is_file()
    assert not (work / "pkg" / "__pycache__").exists()
    assert (work / "pkg" / "m.py").is_file()


def test_symbol_in_text_rejects_degenerate_leaves():
    match = fixloop._symbol_in_text
    norm = fixloop._normalize_error_text
    assert match("numpy.NaN",
                 norm("AttributeError: `np.NaN` was removed"))
    assert match("numpy.NaN", "uses NaN here")
    assert not match("numpy.0", "axis 0 out of bounds")
    assert not match("numpy.0j", "residual 0j expected")
    assert not match("x", "x marks it")


def test_prune_fixwork_keeps_fresh_and_recent(tmp_path, monkeypatch):
    import os
    import time

    base = tmp_path / "fixwork"
    for name in ("a", "b", "c", "d"):
        (base / name).mkdir(parents=True)
    old = time.time() - 7200
    os.utime(base / "a", (old, old))
    removed = fixloop.prune_fixwork(tmp_path, keep=1)
    assert removed == ["a"]
    assert sorted(p.name for p in base.iterdir()) == ["b", "c", "d"]


def test_skipped_record_shape():
    rec = skipped_record("skipped-no-key", "no MODEL_API_KEY")
    assert rec["status"] == rec["final_status"] == "skipped"
    assert rec["rounds"] == [] and rec["patch"] is None
    assert rec["guards"] == {"ok": True, "violations": []}


def _stage_ctx(**over):
    ctx = SimpleNamespace(mode="live", manifest={}, state={},
                          saved=[], events=[])
    ctx.save_artifact = lambda name, obj: ctx.saved.append((name, obj))
    ctx.emit = lambda type, data: ctx.events.append((type, data))
    ctx.set_manifest = lambda **kw: ctx.manifest.update(kw)
    for key, value in over.items():
        setattr(ctx, key, value)
    return ctx


def test_stage_fix_skip_gates(monkeypatch):
    from chainreaction import llm as llm_mod
    from chainreaction.runs import stages as stages_mod

    monkeypatch.setattr(
        "chainreaction.fixloop.run_loop",
        lambda ctx: (_ for _ in ()).throw(
            AssertionError("must not run")))
    monkeypatch.setattr(llm_mod, "has_key", lambda: False)
    ctx = _stage_ctx(state={"probes": []})
    stages_mod.stage_fix(ctx)
    assert ctx.manifest["fix_source"] == "skipped-no-key"
    assert ctx.state["fix"]["status"] == "skipped"

    monkeypatch.setattr(llm_mod, "has_key", lambda: True)
    for state, reason in (
            ({"untouched": True}, "skipped-untouched"),
            ({"dependency_conflict": {"p": 1}}, "skipped-conflict"),
            ({"probes": [("after-import",
                          SimpleNamespace(ok=True))],
              "scores": {}}, "skipped-clean"),
            ({"probes": [("after-import",
                          SimpleNamespace(ok=False))],
              "scores": {}}, "muse-spark")):
        ctx = _stage_ctx(state=dict(state))
        if reason == "muse-spark":
            monkeypatch.setattr(
                "chainreaction.fixloop.run_loop",
                lambda ctx: {"status": "green"})
        stages_mod.stage_fix(ctx)
        assert ctx.manifest["fix_source"] == reason, reason


def test_stage_fix_replay_follows_manifest(monkeypatch):
    from chainreaction.runs import stages as stages_mod

    monkeypatch.setattr(
        "chainreaction.fixloop.run_loop",
        lambda ctx: (_ for _ in ()).throw(
            AssertionError("must not run")))
    ctx = _stage_ctx(mode="replay",
                     manifest={"fix_source": "skipped-no-key"})
    stages_mod.stage_fix(ctx)
    assert ctx.state["fix"]["stop_reason"] == "skipped-no-key"
    monkeypatch.setattr("chainreaction.fixloop.run_loop",
                        lambda ctx: {"status": "green"})
    ctx = _stage_ctx(mode="replay",
                     manifest={"fix_source": "muse-spark"})
    stages_mod.stage_fix(ctx)
    assert ctx.state["fix"] == {"status": "green"}


def test_clear_pycache_removes_caches(tmp_path):
    nested = tmp_path / "pkg" / "__pycache__"
    nested.mkdir(parents=True)
    (nested / "m.pyc").write_text("x", encoding="utf-8")
    (tmp_path / "pkg" / "m.py").write_text("x = 1\n", encoding="utf-8")
    fixloop._clear_pycache(tmp_path)
    assert not nested.exists()
    assert (tmp_path / "pkg" / "m.py").is_file()


# Hand-verified nibabel fix (was show_fixloop.py P1): sctypes with a
# numpy2 fallback. Fixture only — never shown as model output.
P1_SEARCH = """if not hasattr(np, 'sctypes'):
    _sctypes = None
    warnings.warn(
        'np.sctypes was removed in NumPy 2.0; configuring a fallback')"""

P1_REPLACE = """if not hasattr(np, 'sctypes'):
    _sctypes = {
        'int': [np.int8, np.int16, np.int32, np.int64],
        'float': [np.float16, np.float32, np.float64],
        'complex': [np.complex64, np.complex128],
    }"""


def test_hand_fixture_nibabel_sctypes_fallback(tmp_path):
    (tmp_path / "nibabel").mkdir()
    casting = tmp_path / "nibabel" / "casting.py"
    casting.write_text(
        "import warnings\nimport numpy as np\n\n\n" + P1_SEARCH
        + "\n\nsctypes = getattr(np, 'sctypes', None) or _sctypes\n",
        encoding="utf-8", newline="\n")
    edit = Edit("nibabel/casting.py", P1_SEARCH, P1_REPLACE)
    assert validate_edits([edit], tmp_path, "nibabel") == []
    assert apply_edits(tmp_path, [edit]) == ["nibabel/casting.py"]
    text = casting.read_text(encoding="utf-8")
    assert "np.float64" in text and "warnings.warn" not in text
    assert guard_checks(["nibabel/casting.py"],
                        text.splitlines(), []) == []


# -- loop end-to-end on synthetic packages --

class _FakeCtx:
    """Minimal run context: canned LLM, real subprocesses, tmp store."""

    def __init__(self, tmp_path, *, llm_script, state, run_id=None,
                 preset="test-preset", fix_rounds=None):
        import time as _time

        self._time = _time
        self.mode = "live"
        self.dir = tmp_path / "run"
        self.dir.mkdir(exist_ok=True)
        self.manifest = {"run_id": run_id or uuid.uuid4().hex,
                         "model": "fake-model", "preset": preset,
                         "fix_rounds": fix_rounds}
        self.state = state
        self._llm_script = list(llm_script)
        self.llm_calls = []
        self.cmds = []
        self.events = []
        self.cancelled = False
        from chainreaction.runs.scrub import Scrubber
        self.scrubber = Scrubber.default()

    def llm(self, system, user, max_tokens=8000, response_format=None,
            reasoning_effort=None):
        assert reasoning_effort == "high"
        assert response_format == fixloop.FIX_RESPONSE_FORMAT
        self.llm_calls.append(user)
        head = self._llm_script.pop(0)
        return head(system, user) if callable(head) else head

    def run_cmd(self, cmd, cwd=".", env_name=None, timeout=300, tool=None,
                extra_env=None):
        self.cmds.append((list(cmd), str(cwd), env_name))
        env = dict(__import__("os").environ)
        env.update(extra_env or {})
        t0 = self._time.monotonic()
        try:
            proc = subprocess.run(
                cmd, cwd=str(cwd), capture_output=True, text=True,
                timeout=timeout, env=env)
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout if isinstance(exc.stdout, str) else ""
            err = exc.stderr if isinstance(exc.stderr, str) else ""
            return SimpleNamespace(
                returncode=None, stdout=out, stderr=err,
                duration_ms=int((self._time.monotonic() - t0) * 1000),
                timed_out=True, missing=None)
        return SimpleNamespace(
            returncode=proc.returncode,
            stdout=self.scrubber.text(proc.stdout),
            stderr=self.scrubber.text(proc.stderr),
            duration_ms=int((self._time.monotonic() - t0) * 1000),
            timed_out=False, missing=None)

    def env_python(self, env_name):
        return sys.executable

    def emit(self, type, data):
        self.events.append((type, data))

    def save_artifact(self, name, obj):
        from chainreaction.runs.scrub import canonical_json

        path = self.dir / "artifacts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(obj, str):
            path.write_text(obj, encoding="utf-8", newline="\n")
        else:
            path.write_text(canonical_json(obj) + "\n", encoding="utf-8")
        self.events.append(("artifact", {"name": name}))
        return path

    def check_cancelled(self):
        if self.cancelled:
            from chainreaction.runs.context import Cancelled
            raise Cancelled("cancelled in test")

    def set_manifest(self, **fields):
        self.manifest.update(fields)


def _loop_state(target="proj", package="pkg", **over):
    scen = SimpleNamespace(target=target, package=package,
                           verify_env="after", before_env="",
                           probe_code="", pr_files=[], pr_url="")
    state = {"scenario": scen,
             "changeset": SimpleNamespace(items=[]), "symbols": set(),
             "graph": None, "testmap": None, "scores": None}
    state.update(over)
    return state


def _loop_ctx(monkeypatch, tmp_path, *, llm_script, state, **kw):
    monkeypatch.setattr("chainreaction.runs.scrub.ROOT", tmp_path)
    return _FakeCtx(tmp_path, llm_script=llm_script, state=state, **kw)


def _reply(edits, rationale="fix it", addresses=None):
    return json.dumps({"edits": edits, "rationale": rationale,
                       "addresses": addresses or []})


def _broken_import_pkg(root):
    """Package whose import fails until the bad line is removed."""
    pkg = root / "proj" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "from pkg.mod import VALUE\nimport nosuchmodule_xyz\n",
        encoding="utf-8", newline="\n")
    (pkg / "mod.py").write_text("VALUE = 1\n", encoding="utf-8",
                                newline="\n")
    (root / "proj" / "test_mod.py").write_text(
        "from pkg.mod import VALUE\n\n\n"
        "def test_v():\n    assert VALUE == 2\n",
        encoding="utf-8", newline="\n")
    return root / "proj"


def test_run_loop_red_to_green(monkeypatch, tmp_path):
    src = _broken_import_pkg(tmp_path)

    def script(system, user):
        if "Round 1 of" in user:
            return _reply([{"path": "pkg/__init__.py",
                            "search": "import nosuchmodule_xyz\n",
                            "replace": "", "test_file": False}],
                          "drop the bad import")
        return _reply([{"path": "pkg/mod.py", "search": "VALUE = 1",
                        "replace": "VALUE = 2", "test_file": False}],
                      "set VALUE right")

    ctx = _loop_ctx(monkeypatch, tmp_path, llm_script=[script, script],
                    state=_loop_state())
    record = run_loop(ctx)
    assert record["status"] == "green"
    assert record["stop_reason"] == "green"
    assert len(record["rounds"]) == 2
    assert record["counts"]["newly_broken"] == []
    assert record["guards"] == {"ok": True, "violations": []}
    assert record["patch"] == "fixes/final.patch"
    patch = (ctx.dir / "artifacts" / "fixes" / "final.patch").read_text(
        encoding="utf-8")
    assert "pkg/__init__.py" in patch and "pkg/mod.py" in patch
    assert (ctx.dir / "artifacts" / "fixes" / "round-1.diff").is_file()
    # The source tree is untouched; the work copy carries the fix.
    assert "nosuchmodule" in (src / "pkg" / "__init__.py").read_text(
        encoding="utf-8")
    assert [t for t, _d in ctx.events].count("artifact") >= 3


def test_run_loop_marks_skip_adding_model_unverified(monkeypatch,
                                                     tmp_path):
    """Acceptance: a passing-via-skip patch is unverified, not green."""
    src = _broken_import_pkg(tmp_path)
    (src / "pkg" / "__init__.py").write_text(
        "from pkg.mod import VALUE\n", encoding="utf-8", newline="\n")

    def script(system, user):
        return _reply([{"path": "test_mod.py",
                        "search": "from pkg.mod import VALUE",
                        "replace": ("import pytest\n"
                                    "from pkg.mod import VALUE"),
                        "test_file": True},
                       {"path": "test_mod.py",
                        "search": "def test_v():",
                        "replace": ("@pytest.mark.skip(reason='x')\n"
                                    "def test_v():"),
                        "test_file": True}],
                      "skip the red test")

    ctx = _loop_ctx(monkeypatch, tmp_path, llm_script=[script],
                    state=_loop_state())
    record = run_loop(ctx)
    assert record["status"] == "unverified"
    assert record["guards"]["violations"] != []
    assert any("skip" in v for v in record["guards"]["violations"])


def test_run_loop_retries_ambiguous_search(monkeypatch, tmp_path):
    """Acceptance: a 0/2+-match search is rejected, then retried."""
    src = _broken_import_pkg(tmp_path)
    (src / "pkg" / "__init__.py").write_text(
        "from pkg.mod import VALUE\n", encoding="utf-8", newline="\n")
    (src / "pkg" / "mod.py").write_text("VALUE = 1\nVALUE = 1\n",
                                        encoding="utf-8", newline="\n")
    (src / "test_mod.py").write_text(
        "from pkg.mod import VALUE\n\n\n"
        "def test_v():\n    assert VALUE == 2\n",
        encoding="utf-8", newline="\n")
    calls = []

    def script(system, user):
        calls.append(user)
        if len(calls) == 1:
            return _reply([{"path": "pkg/mod.py", "search": "VALUE = 1",
                            "replace": "VALUE = 2", "test_file": False}],
                          "ambiguous attempt")
        return _reply([{"path": "pkg/mod.py",
                        "search": "VALUE = 1\nVALUE = 1\n",
                        "replace": "VALUE = 2\nVALUE = 2\n",
                        "test_file": False}],
                      "unique attempt")

    ctx = _loop_ctx(monkeypatch, tmp_path, llm_script=[script, script],
                    state=_loop_state())
    record = run_loop(ctx)
    assert record["status"] == "green"
    attempt = record["rounds"][0]["attempts"][0]
    assert any("occurs 2 times" in v for v in attempt["validation"])
    assert attempt["applied"] == ["pkg/mod.py"]
    assert len(ctx.llm_calls) == 2  # rejected, then retried


def test_run_loop_stops_after_no_progress_twice(monkeypatch, tmp_path):
    src = _broken_import_pkg(tmp_path)

    def script(system, user):
        return _reply([{"path": "pkg/mod.py", "search": "VALUE = 1",
                        "replace": "VALUE = 1  # noop",
                        "test_file": False}],
                      "touch nothing that matters")

    ctx = _loop_ctx(monkeypatch, tmp_path,
                    llm_script=[script] * 6,
                    state=_loop_state(), fix_rounds=5)
    record = run_loop(ctx)
    assert record["stop_reason"] == "no-progress-twice"
    assert record["status"] == "partial"  # edits exist, probe unmoved
    assert len(record["rounds"]) == 2
    assert all(r["progress"] is False for r in record["rounds"])


def test_run_loop_skipped_clean(monkeypatch, tmp_path):
    src = _broken_import_pkg(tmp_path)
    (src / "pkg" / "__init__.py").write_text(
        "from pkg.mod import VALUE\n", encoding="utf-8", newline="\n")
    (src / "pkg" / "mod.py").write_text("VALUE = 2\n", encoding="utf-8",
                                        newline="\n")
    ctx = _loop_ctx(monkeypatch, tmp_path, llm_script=[],
                    state=_loop_state())
    record = run_loop(ctx)
    assert record["status"] == "skipped"
    assert record["stop_reason"] == "skipped-clean"
    assert ctx.llm_calls == [] and record["rounds"] == []


def test_run_loop_retries_failed_model_call(monkeypatch, tmp_path):
    """A failed model call degrades the attempt, never the run."""
    src = _broken_import_pkg(tmp_path)
    (src / "pkg" / "__init__.py").write_text(
        "from pkg.mod import VALUE\n", encoding="utf-8", newline="\n")
    calls = []

    def script(system, user):
        calls.append(user)
        if len(calls) == 1:
            raise RuntimeError("empty response from model "
                               "(finish_reason=length)")
        return _reply([{"path": "pkg/mod.py", "search": "VALUE = 1",
                        "replace": "VALUE = 2", "test_file": False}],
                      "set VALUE right")

    ctx = _loop_ctx(monkeypatch, tmp_path, llm_script=[script, script],
                    state=_loop_state())
    record = run_loop(ctx)
    assert record["status"] == "green"
    attempt = record["rounds"][0]["attempts"][0]
    assert any("model_error:" in v for v in attempt["validation"])
    assert attempt["applied"] == ["pkg/mod.py"]


def test_run_loop_stops_on_persistent_model_errors(monkeypatch,
                                                   tmp_path):
    _broken_import_pkg(tmp_path)

    def script(system, user):
        raise RuntimeError("model unavailable")

    ctx = _loop_ctx(monkeypatch, tmp_path,
                    llm_script=[script] * 12, state=_loop_state())
    record = run_loop(ctx)
    assert record["stop_reason"] == "llm-error"
    assert record["status"] == "failed"
    assert len(record["rounds"]) == 2
