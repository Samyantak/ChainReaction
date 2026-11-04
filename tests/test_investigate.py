"""Investigator (M4) tests: read-only tools, probes, loop, checker.

Read-only tool tests are hermetic: a tiny fixture package built in
tmp_path, graphed with the real repo builder, queried through the
same dispatch the agent loop uses.
"""

import json

import pytest

from types import SimpleNamespace

from chainreaction.generic import ProjectInfo
from chainreaction.graph.repo import build_graph_from_repo
from chainreaction.interpret import ChangeItem, ChangeSet
from chainreaction.investigate import (
    TOOLS,
    LoopBudgets,
    ToolEnv,
    run_investigation,
    run_tool,
    validate_snippet,
    verify_investigation,
)
from chainreaction.runs import stages as stages_mod
from chainreaction.runs import store
from chainreaction.runs.context import CmdResult, RunContext
from chainreaction.runs.scrub import Scrubber
from chainreaction.scenarios import Scenario
from chainreaction.testmap import SuiteResult, TestMap, break_lines
from chainreaction.verifier import ProbeResult

PKG_FILES = {
    "pkg/__init__.py": "from pkg.mod import helper\n",
    "pkg/mod.py": (
        "import numpy as np\n"
        "\n"
        "MISSING = np.NaN\n"
        "\n"
        "@np.sctypes\n"
        "def decorated():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def helper():\n"
        "    return np.sctypes\n"
        "\n"
        "\n"
        "def caller():\n"
        "    return helper()\n"
    ),
    "pkg/user.py": "from pkg.mod import helper\n\n\ndef use():\n    return helper()\n",
}


@pytest.fixture()
def pkg(tmp_path):
    root = tmp_path / "ws"
    for rel, text in PKG_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture()
def env(pkg):
    graph = build_graph_from_repo(pkg, "pkg")
    changeset = ChangeSet(
        decision="upgrade numpy to 2.0", source_url="",
        items=[ChangeItem(symbol="numpy.NaN", kind="removed",
                          quote="np.NaN removed", introduced_in="2.0"),
               ChangeItem(symbol="numpy.sctypes", kind="removed",
                          quote="np.sctypes removed",
                          introduced_in="2.0")])
    return ToolEnv(ctx=None, workspace=pkg, package="pkg", graph=graph,
                   changeset=changeset, testmap=None, before_env=None,
                   after_env="after")


def test_tool_schemas_cover_the_spec_names():
    names = [t["function"]["name"] for t in TOOLS]
    assert names == ["list_changes", "find_usages", "trace_importers",
                     "callers_of", "propagate", "tests_covering",
                     "read_file", "run_import_probe", "run_tests",
                     "run_snippet", "submit_findings"]


def test_list_changes_and_kind_filter(env):
    all_items = run_tool(env, "list_changes", {})
    assert all_items["ok"] is True
    assert [i["symbol"] for i in all_items["items"]] == ["numpy.NaN",
                                                         "numpy.sctypes"]
    assert all_items["items"][0]["release"] == "2.0"
    none = run_tool(env, "list_changes", {"kind": "renamed"})
    assert none["items"] == []
    assert none["truncated"] is False


def test_find_usages_marks_import_time_and_call_time(env):
    got = run_tool(env, "find_usages", {"symbol": "numpy.sctypes"})
    assert got["ok"] is True
    by_line = {s["line"]: s for s in got["sites"]}
    # Module-level decorator: runs at import.
    assert by_line[5]["import_time"] is True
    assert by_line[5]["def"] is None
    assert "sctypes" in by_line[5]["code"]
    # Function body: runs at call time.
    assert by_line[11]["import_time"] is False
    assert by_line[11]["def"] == "pkg.mod.helper"
    assert got["truncated"] is False
    assert by_line[5]["file"] == "pkg/mod.py"


def test_find_usages_unknown_symbol_is_empty_not_error(env):
    got = run_tool(env, "find_usages", {"symbol": "numpy.nope"})
    assert got["ok"] is True
    assert got["sites"] == []


def test_trace_importers_walks_to_package_root(env):
    got = run_tool(env, "trace_importers", {"module": "pkg.mod"})
    assert got["ok"] is True
    assert got["chains"][0][0]["module"] == "pkg.mod"
    assert got["chains"][0][-1]["module"] == "pkg"
    hop = got["chains"][0][1]
    assert hop["file"] == "pkg/__init__.py"
    assert hop["line"] == 1
    assert got["truncated"] is False


def test_callers_of_finds_call_paths(env):
    got = run_tool(env, "callers_of",
                   {"qualified_def": "pkg.mod.helper"})
    assert got["ok"] is True
    joined = json.dumps(got["paths"])
    assert "pkg.mod.caller" in joined
    assert "pkg.user.use" in joined
    use_hop = [h for p in got["paths"] for h in p
               if h["def"] == "pkg.user.use"][0]
    assert (use_hop["file"], use_hop["line"]) == ("pkg/user.py", 5)
    assert got["truncated"] is False


def test_propagate_uses_the_existing_engine(env):
    got = run_tool(env, "propagate", {"symbols": ["numpy.NaN"]})
    assert got["ok"] is True
    assert "pkg.mod" in got["hard"]
    assert "pkg" in got["hard"]  # importer of a HARD module
    assert got["worst_chain"][0] == "pkg"
    assert got["truncated"] is False


def test_propagate_reports_per_symbol_hits(env):
    got = run_tool(env, "propagate",
                   {"symbols": ["numpy.NaN", "numpy.nope"]})
    assert got["symbols"]["numpy.NaN"] == {"sites": 1, "import_time": 1}
    assert got["symbols"]["numpy.nope"] == {"sites": 0, "import_time": 0}
    assert "1/2 symbols used" in got["summary"]


def test_tests_covering_needs_a_coverage_map(env):
    got = run_tool(env, "tests_covering", {"modules": ["pkg.mod"]})
    assert got["ok"] is False
    assert "coverage" in got["error"]


def test_read_file_returns_numbered_lines(env):
    got = run_tool(env, "read_file",
                   {"path": "pkg/mod.py", "start": 3, "end": 3})
    assert got["ok"] is True
    assert got["lines"] == [[3, "MISSING = np.NaN"]]
    assert got["truncated"] is False


def test_read_file_rejects_wide_windows_and_escapes(env):
    wide = run_tool(env, "read_file",
                    {"path": "pkg/mod.py", "start": 1, "end": 500})
    assert wide["ok"] is False
    assert "200" in wide["error"]
    esc = run_tool(env, "read_file",
                   {"path": "../outside.py", "start": 1, "end": 2})
    assert esc["ok"] is False
    assert "workspace" in esc["error"]


# -- probe tools (fake ctx.run_cmd) --

class _FakeCtx:
    def __init__(self, result):
        self._result = result
        self.calls = []
        self.scrubber = Scrubber.default()
        self.state = {}

    def run_cmd(self, cmd, cwd=".", env_name=None, timeout=300,
                extra_env=None):
        self.calls.append({"cmd": cmd, "cwd": cwd, "env_name": env_name,
                           "timeout": timeout, "extra_env": extra_env})
        return self._result

    def env_python(self, env_name):
        return f"<env-python:{env_name}>"


@pytest.fixture()
def probe_env(env):
    stderr = ('Traceback (most recent call last):\n'
              f'  File "{env.workspace / "pkg" / "mod.py"}", line 3, '
              "in <module>\n"
              "    MISSING = np.NaN\n"
              "AttributeError: module 'numpy' has no attribute 'NaN'\n")
    ctx = _FakeCtx(CmdResult(1, "", stderr, 5, False, None, {}))
    env.ctx = ctx
    env.before_env = "ws/before"
    env.after_env = "ws/after"
    return env


def test_run_import_probe_reports_chain_and_error(probe_env):
    got = run_tool(probe_env, "run_import_probe",
                   {"module": "pkg.mod", "env": "after"})
    assert got["ok"] is False
    assert got["chain"] == ["pkg.mod"]
    assert got["error"] == {"file": "pkg/mod.py", "line": 3,
                            "func": "<module>",
                            "site": "pkg/mod.py:3"}
    assert "has no attribute" in got["error_line"]
    assert probe_env.ctx.calls[0]["env_name"] == "ws/after"
    assert "import pkg.mod" in probe_env.ctx.calls[0]["cmd"][-1]


def test_run_import_probe_ok_has_empty_chain(probe_env):
    probe_env.ctx._result = CmdResult(0, "", "", 4, False, None, {})
    got = run_tool(probe_env, "run_import_probe",
                   {"module": "pkg.mod", "env": "before"})
    assert got["ok"] is True
    assert got["chain"] == []
    assert probe_env.ctx.calls[0]["env_name"] == "ws/before"


def test_run_import_probe_rejects_bad_module_and_env(probe_env):
    bad = run_tool(probe_env, "run_import_probe",
                   {"module": "os; import shutil", "env": "after"})
    assert bad["ok"] is False
    assert "module" in bad["error"]
    bad_env = run_tool(probe_env, "run_import_probe",
                       {"module": "pkg.mod", "env": "sideways"})
    assert bad_env["ok"] is False
    assert probe_env.ctx.calls == []
    probe_env.before_env = None
    missing = run_tool(probe_env, "run_import_probe",
                       {"module": "pkg.mod", "env": "before"})
    assert missing["ok"] is False
    assert "before" in missing["error"]


def test_run_tests_reports_per_test_outcomes(probe_env):
    stdout = ("pkg/test_mod.py::test_a PASSED\n"
              "pkg/test_mod.py::test_b FAILED\n"
              "===== short test summary info =====\n"
              "FAILED pkg/test_mod.py::test_b - assert 1 == 2\n"
              "1 failed, 1 passed in 0.5s\n")
    probe_env.ctx._result = CmdResult(1, stdout, "", 9, False, None, {})
    ids = ["pkg/test_mod.py::test_a", "pkg/test_mod.py::test_b"]
    got = run_tool(probe_env, "run_tests",
                   {"test_ids": ids, "env": "after"})
    assert got["ok"] is True
    assert got["results"] == [
        {"id": "pkg/test_mod.py::test_a", "outcome": "pass"},
        {"id": "pkg/test_mod.py::test_b", "outcome": "fail"}]
    assert "assert 1 == 2" in got["tail"]


def test_run_tests_rejects_empty_and_oversize_lists(probe_env):
    assert run_tool(probe_env, "run_tests",
                    {"test_ids": [], "env": "after"})["ok"] is False
    big = [f"t{i}" for i in range(51)]
    too_many = run_tool(probe_env, "run_tests",
                        {"test_ids": big, "env": "after"})
    assert too_many["ok"] is False
    assert "50" in too_many["error"]
    assert probe_env.ctx.calls == []


def test_run_snippet_echoes_capped_output(probe_env):
    probe_env.ctx._result = CmdResult(0, "hello\n", "", 3, False, None,
                                      {})
    got = run_tool(probe_env, "run_snippet",
                   {"code": "print('hello')", "env": "after"})
    assert got["ok"] is True
    assert got["stdout"] == "hello\n"
    assert got["truncated"] is False


def test_validate_snippet_rejects_each_banned_pattern():
    assert "os" in (validate_snippet("import os") or "")
    assert validate_snippet("import subprocess") is not None
    assert validate_snippet("from os import path") is not None
    assert validate_snippet("import socket") is not None
    assert validate_snippet("import shutil") is not None
    assert validate_snippet("open('f', 'w')") is not None
    assert validate_snippet("open('f', mode='a')") is not None
    assert validate_snippet("open('f', 'r+')") is not None
    assert validate_snippet("__import__('os')") is not None
    assert validate_snippet("def broken(:") is not None
    assert validate_snippet("\n".join(f"x{i} = {i}" for i in range(41))
                            ) is not None


def test_validate_snippet_allows_safe_code():
    assert validate_snippet("import numpy as np\nprint(np.__version__)") \
        is None
    assert validate_snippet("open('f').read()") is None
    assert validate_snippet("open('f', 'r').read()") is None
    assert validate_snippet("\n".join(f"x{i} = {i}" for i in range(40))
                            ) is None


# -- agent loop (scripted model through a live RunContext) --

def _turn(content="", calls=None, tokens=10):
    from chainreaction import llm as llm_mod
    return llm_mod.ChatResult(content, "fake-model",
                              {"total_tokens": tokens}, "stop", False,
                              None, tool_calls=calls)


def _call(cid, name, args):
    return {"id": cid, "name": name, "arguments": args}


SUBMIT_ARGS = {
    "findings": [{"id": "F1", "claim": "pkg.mod breaks at import",
                  "kind": "breaks_import", "symbol": "numpy.NaN",
                  "sites": ["pkg/mod.py:3"], "tests": [],
                  "evidence": ["call_1"], "confidence": "likely"}],
    "triage": [{"symbol": "numpy.NaN", "relevant": True,
                "reason": "used at import time", "evidence": ["call_1"]}],
    "summary": "One import break."}


@pytest.fixture()
def live_ctx(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(tmp_path / "runs"))
    manifest = {"run_id": "m4", "preset": None, "mode": "live",
                "model": "fake-model", "status": "running"}
    store.write_json(store.run_dir("m4") / store.MANIFEST, manifest)
    return manifest


def _loop_env(env, ctx):
    env.ctx = ctx
    env.before_env = "ws/before"
    env.after_env = "ws/after"
    return env


def _run_loop(env, live_ctx, script, budgets=None):
    seen = []
    replies = list(script)

    def fake(system, user, max_tokens, response_format, tools,
             tool_choice):
        seen.append(user)
        assert tools is not None and tool_choice == "auto"
        return replies.pop(0)

    ctx = RunContext(run_id="m4", manifest=live_ctx, mode="live",
                     live_llm=fake)
    out = run_investigation(ctx, _loop_env(env, ctx),
                            decision="upgrade numpy to 2.0",
                            baseline={"failed": [], "passed": 1,
                                      "total": 1},
                            fix_hints=["pkg.mod"],
                            budgets=budgets)
    return out, seen, ctx


def test_loop_happy_path_emits_trace_and_events(env, live_ctx):
    script = [_turn("looking", [_call("call_1", "list_changes", {})]),
              _turn("done", [_call("call_2", "submit_findings",
                                   SUBMIT_ARGS)])]
    out, seen, ctx = _run_loop(env, live_ctx, script)
    assert out["status"] == "complete"
    assert out["summary"] == "One import break."
    assert [t["id"] for t in out["trace"]] == ["call_1", "call_2"]
    assert out["trace"][0]["result"]["ok"] is True
    assert out["usage"]["tool_calls"] == 2
    assert out["usage"]["tokens"] == 20
    rows = store.read_jsonl(ctx.dir / store.EVENTS)
    kinds = [r["type"] for r in rows]
    assert kinds.count("tool_call") == 2
    assert kinds.count("tool_result") == 2
    assert "artifact" in kinds


def test_loop_runs_parallel_calls_in_order(env, live_ctx):
    script = [_turn("two", [_call("call_1", "list_changes", {}),
                            _call("call_2", "find_usages",
                                  {"symbol": "numpy.NaN"})]),
              _turn("done", [_call("call_3", "submit_findings",
                                   SUBMIT_ARGS)])]
    out, _, _ = _run_loop(env, live_ctx, script)
    assert [t["id"] for t in out["trace"]] == ["call_1", "call_2",
                                               "call_3"]
    assert out["trace"][1]["result"]["sites"][0]["line"] == 3


def test_loop_nudges_once_then_gives_up(env, live_ctx):
    script = [_turn("just thinking"), _turn("still thinking")]
    out, seen, _ = _run_loop(env, live_ctx, script)
    assert out["status"] == "incomplete"
    assert len(seen) == 2  # second turn is the nudge
    assert "submit_findings" in seen[1]
    assert out["trace"] == []


def test_loop_budget_forces_submit_then_incomplete(env, live_ctx):
    script = [_turn("one", [_call("call_1", "list_changes", {})]),
              _turn("ignores budget",
                    [_call("call_2", "list_changes", {})])]
    budgets = LoopBudgets(max_tool_calls=1)
    out, seen, _ = _run_loop(env, live_ctx, script, budgets)
    assert out["status"] == "incomplete"
    assert [t["id"] for t in out["trace"]] == ["call_1"]
    assert "Budget exhausted" in seen[1]


def test_loop_rejects_bad_submit_and_continues(env, live_ctx):
    bad = {"findings": [], "triage": []}  # no summary
    script = [_turn("early", [_call("call_1", "submit_findings", bad)]),
              _turn("fixed", [_call("call_2", "submit_findings",
                                    SUBMIT_ARGS)])]
    out, seen, _ = _run_loop(env, live_ctx, script)
    assert out["status"] == "complete"
    assert out["trace"][0]["result"]["ok"] is False
    assert "summary" in seen[1]


def test_loop_caps_batch_at_eight_per_turn(env, live_ctx):
    batch = [_call(f"call_{i}", "list_changes", {})
             for i in range(1, 11)]
    script = [_turn("many", batch),
              _turn("done", [_call("call_11", "submit_findings",
                                   SUBMIT_ARGS)])]
    out, seen, _ = _run_loop(env, live_ctx, script)
    assert out["status"] == "complete"
    assert [t["id"] for t in out["trace"]] == \
        [f"call_{i}" for i in range(1, 9)] + ["call_9"]
    assert out["usage"]["tool_calls"] == 9
    assert "call_9" in seen[1] and "call_10" in seen[1]
    assert "re-issue" in seen[1]


def test_loop_honors_submit_beyond_cap(env, live_ctx):
    batch = [_call(f"call_{i}", "list_changes", {})
             for i in range(1, 9)]
    batch.append(_call("call_9", "submit_findings", SUBMIT_ARGS))
    out, _, _ = _run_loop(env, live_ctx, [(_turn("many", batch))])
    assert out["status"] == "complete"
    assert out["trace"][-1]["id"] == "call_9"


def test_loop_stops_batch_at_budget(env, live_ctx):
    batch = [_call(f"call_{i}", "list_changes", {})
             for i in range(1, 6)]
    script = [_turn("many", batch),
              _turn("done", [_call("call_6", "submit_findings",
                                   SUBMIT_ARGS)])]
    budgets = LoopBudgets(max_tool_calls=2)
    out, seen, _ = _run_loop(env, live_ctx, script, budgets)
    assert out["status"] == "complete"
    assert [t["id"] for t in out["trace"]] == ["call_1", "call_2",
                                               "call_3"]
    assert "Budget exhausted" in seen[1]


def test_loop_forced_turn_honors_submit_with_extras(env, live_ctx):
    script = [_turn("one", [_call("call_1", "list_changes", {})]),
              _turn("last", [_call("call_2", "list_changes", {}),
                             _call("call_3", "submit_findings",
                                   SUBMIT_ARGS)])]
    budgets = LoopBudgets(max_tool_calls=1)
    out, _, _ = _run_loop(env, live_ctx, script, budgets)
    assert out["status"] == "complete"
    assert [t["id"] for t in out["trace"]] == ["call_1", "call_3"]


def test_loop_assigns_short_ids_and_keeps_model_ids(env, live_ctx):
    script = [_turn("a", [_call("call-uuid-1", "list_changes", {})]),
              _turn("b", [_call("call-uuid-2", "submit_findings",
                                SUBMIT_ARGS)])]
    out, _, _ = _run_loop(env, live_ctx, script)
    assert [t["id"] for t in out["trace"]] == ["call_1", "call_2"]
    assert [t["model_id"] for t in out["trace"]] == ["call-uuid-1",
                                                     "call-uuid-2"]
    # Findings cite the short ids the model saw in history.
    assert out["findings"][0]["evidence"] == ["call_1"]


def test_loop_system_prompt_lists_probe_layers(env, live_ctx):
    systems = []
    replies = [_turn("done", [_call("x", "submit_findings", SUBMIT_ARGS)])]

    def fake(system, user, max_tokens, response_format, tools,
             tool_choice):
        systems.append(system)
        return replies.pop(0)

    ctx = RunContext(run_id="m4", manifest=live_ctx, mode="live",
                     live_llm=fake)
    layers = [{"name": "layer2 (peeled)", "kind": "peeled",
               "module": "pkg", "env": "after", "code": "import shim"}]
    run_investigation(ctx, _loop_env(env, ctx), decision="d",
                      layers=layers)
    assert "layer2 (peeled)" in systems[0]
    assert "import shim" in systems[0]
    assert "Peel import layers" in systems[0]


def test_loop_unknown_tool_fails_closed(env, live_ctx):
    script = [_turn("oops", [_call("call_1", "rm_rf", {})]),
              _turn("done", [_call("call_2", "submit_findings",
                                   SUBMIT_ARGS)])]
    out, _, _ = _run_loop(env, live_ctx, script)
    assert out["status"] == "complete"
    assert out["trace"][0]["result"]["ok"] is False
    assert "unknown tool" in out["trace"][0]["result"]["error"]


def _investigation(trace, findings, triage=None):
    return {"status": "complete", "findings": findings,
            "triage": triage or [], "summary": "s", "trace": trace,
            "usage": {}}


def _trace_entry(cid, name, result):
    return {"id": cid, "name": name, "args": {}, "result": result}


USAGES_RESULT = {"ok": True, "summary": "2 sites", "truncated": False,
                 "sites": [{"file": "pkg/mod.py", "line": 3,
                            "site": "pkg/mod.py:3", "def": None,
                            "import_time": True, "code": "MISSING = np.NaN"}]}

FAILED_PROBE = {"ok": False, "summary": "fails", "truncated": False,
                "chain": ["pkg.mod"],
                "error": {"file": "pkg/mod.py", "line": 3,
                          "func": "<module>", "site": "pkg/mod.py:3"},
                "error_line": "AttributeError: NaN", "timed_out": False}

PASSED_PROBE = {"ok": True, "summary": "ok", "truncated": False,
                "chain": [], "error": None, "error_line": "",
                "timed_out": False}

TESTS_RESULT = {"ok": True, "summary": "2 tests", "truncated": False,
                "test_ids": ["pkg/test_mod.py::test_a"],
                "count": 1}

RUN_TESTS_FAIL = {"ok": True, "summary": "1 failed", "truncated": False,
                  "results": [{"id": "pkg/test_mod.py::test_a",
                               "outcome": "fail"}],
                  "tail": "assert", "timed_out": False}


def test_checker_accepts_grounded_likely_finding():
    inv = _investigation(
        [_trace_entry("call_1", "find_usages", USAGES_RESULT)],
        [{"id": "F1", "claim": "pkg.mod uses numpy.NaN at import",
          "kind": "breaks_import", "symbol": "numpy.NaN",
          "sites": ["pkg/mod.py:3"], "tests": [], "evidence": ["call_1"],
          "confidence": "likely"}])
    out = verify_investigation(inv)
    assert out["findings"][0]["verified"] is True
    assert out["findings"][0]["verify_reason"] == ""


def test_checker_rejects_unknown_evidence():
    inv = _investigation(
        [_trace_entry("call_1", "find_usages", USAGES_RESULT)],
        [{"id": "F1", "claim": "c", "kind": "breaks_import",
          "symbol": "s", "sites": [], "tests": [],
          "evidence": ["call_9"], "confidence": "likely"}])
    out = verify_investigation(inv)
    assert out["findings"][0]["verified"] is False
    assert "call_9" in out["findings"][0]["verify_reason"]


def test_checker_rejects_site_outside_cited_results():
    inv = _investigation(
        [_trace_entry("call_1", "find_usages", USAGES_RESULT)],
        [{"id": "F1", "claim": "c", "kind": "breaks_import",
          "symbol": "s", "sites": ["pkg/other.py:99"], "tests": [],
          "evidence": ["call_1"], "confidence": "likely"}])
    out = verify_investigation(inv)
    assert out["findings"][0]["verified"] is False
    assert "pkg/other.py:99" in out["findings"][0]["verify_reason"]


def test_checker_rejects_confirmed_without_matching_probe():
    inv = _investigation(
        [_trace_entry("call_1", "find_usages", USAGES_RESULT)],
        [{"id": "F1", "claim": "c", "kind": "breaks_import",
          "symbol": "s", "sites": ["pkg/mod.py:3"], "tests": [],
          "evidence": ["call_1"], "confidence": "confirmed"}])
    out = verify_investigation(inv)
    assert out["findings"][0]["verified"] is False
    assert "probe" in out["findings"][0]["verify_reason"]


def test_checker_accepts_confirmed_import_break():
    inv = _investigation(
        [_trace_entry("call_1", "find_usages", USAGES_RESULT),
         _trace_entry("call_2", "run_import_probe", FAILED_PROBE)],
        [{"id": "F1", "claim": "pkg.mod breaks at import",
          "kind": "breaks_import", "symbol": "numpy.NaN",
          "sites": ["pkg/mod.py:3"], "tests": [],
          "evidence": ["call_1", "call_2"], "confidence": "confirmed"}])
    out = verify_investigation(inv)
    assert out["findings"][0]["verified"] is True


def test_checker_accepts_peeled_snippet_for_import_break():
    snippet = {"ok": False, "summary": "failed", "truncated": False,
               "stdout": "", "stderr": "ImportError", "chain": ["pkg"],
               "timed_out": False}
    inv = _investigation(
        [_trace_entry("call_1", "run_snippet", snippet)],
        [{"id": "F1", "claim": "peeled import still fails", "kind":
          "breaks_import", "symbol": "s", "sites": [], "tests": [],
          "evidence": ["call_1"], "confidence": "confirmed"}])
    out = verify_investigation(inv)
    assert out["findings"][0]["verified"] is True
    # A failing snippet with no module chain (typo'd code) proves nothing.
    snippet["chain"] = []
    out = verify_investigation(inv)
    assert out["findings"][0]["verified"] is False


def test_checker_accepts_confirmed_call_break_and_tests():
    inv = _investigation(
        [_trace_entry("call_1", "tests_covering", TESTS_RESULT),
         _trace_entry("call_2", "run_tests", RUN_TESTS_FAIL)],
        [{"id": "F1", "claim": "test_a fails at call time",
          "kind": "breaks_call", "symbol": "numpy.sctypes",
          "sites": [], "tests": ["pkg/test_mod.py::test_a"],
          "evidence": ["call_1", "call_2"], "confidence": "confirmed"}])
    out = verify_investigation(inv)
    assert out["findings"][0]["verified"] is True


def test_checker_rejects_test_outside_covering_results():
    inv = _investigation(
        [_trace_entry("call_1", "tests_covering", TESTS_RESULT)],
        [{"id": "F1", "claim": "c", "kind": "breaks_call",
          "symbol": "s", "sites": [],
          "tests": ["pkg/test_mod.py::test_ghost"],
          "evidence": ["call_1"], "confidence": "likely"}])
    out = verify_investigation(inv)
    assert out["findings"][0]["verified"] is False
    assert "test_ghost" in out["findings"][0]["verify_reason"]


def test_checker_marks_bad_triage_unverified():
    inv = _investigation(
        [_trace_entry("call_1", "list_changes", {"ok": True})],
        [],
        [{"symbol": "numpy.NaN", "relevant": False,
          "reason": "unused", "evidence": ["call_7"]}])
    out = verify_investigation(inv)
    assert out["triage"][0]["verified"] is False


def test_loop_replay_reproduces_the_trace(env, live_ctx, tmp_path):
    script = [_turn("looking", [_call("call_1", "list_changes", {})]),
              _turn("using", [_call("call_2", "find_usages",
                                    {"symbol": "numpy.sctypes"})]),
              _turn("done", [_call("call_3", "submit_findings",
                                   SUBMIT_ARGS)])]
    live_out, _, live = _run_loop(env, live_ctx, script)
    manifest = {"run_id": "m4r", "preset": None, "mode": "replay",
                "model": "fake-model", "status": "running"}
    store.write_json(store.run_dir("m4r") / store.MANIFEST, manifest)
    replay = RunContext(run_id="m4r", manifest=manifest, mode="replay",
                        source_dir=live.dir)
    again = run_investigation(replay, _loop_env(env, replay),
                              decision="upgrade numpy to 2.0",
                              baseline={"failed": [], "passed": 1,
                                        "total": 1},
                              fix_hints=["pkg.mod"])
    assert again["status"] == "complete"
    assert again["trace"] == live_out["trace"]
    assert again["findings"] == live_out["findings"]
    assert replay.replay_warnings() == []


# -- investigate stage wiring --

def _stage_ctx(monkeypatch, tmp_path, run_id, preset, script=None,
               has_key=True):
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(tmp_path / "runs"))
    manifest = {"run_id": run_id, "preset": preset, "mode": "live",
                "model": "fake-model", "status": "running"}
    store.write_json(store.run_dir(run_id) / store.MANIFEST, manifest)
    replies = list(script or [])

    def fake(system, user, max_tokens, response_format, tools,
             tool_choice):
        return replies.pop(0)

    monkeypatch.setattr("chainreaction.runs.stages.llm_mod.has_key",
                        lambda: has_key)
    return RunContext(run_id=run_id, manifest=manifest, mode="live",
                      live_llm=fake)


def _stage_state(ctx, pkg, *, package="pkg", decision_pkg="numpy",
                 has_suite=False, preset=True):
    scen = Scenario(id="t", decision="upgrade numpy to 2.0",
                    target=str(pkg), package=package, guide_url="",
                    verify_env="ws/after", before_env="ws/before")
    ctx.state["scenario"] = scen
    ctx.state["symbols"] = {"numpy.NaN"}
    ctx.state["config"] = []
    ctx.state["deprecated"] = set()
    ctx.state["changed"] = set()
    ctx.state["changeset"] = ChangeSet(
        decision="upgrade numpy to 2.0", source_url="", items=[])
    if not preset:
        ctx.state["decision_parsed"] = SimpleNamespace(package=decision_pkg)
        ctx.state["workspace"] = "workspaces/t"
        ctx.state["project"] = ProjectInfo(package=package,
                                           has_suite=has_suite)


SUBMIT_GROUNDED = {
    "findings": [{"id": "F1", "claim": "pkg.mod uses numpy.NaN at import",
                  "kind": "breaks_import", "symbol": "numpy.NaN",
                  "sites": ["pkg/mod.py:3"], "tests": [],
                  "evidence": ["call_1"], "confidence": "likely"}],
    "triage": [{"symbol": "numpy.NaN", "relevant": True,
                "reason": "import-time use", "evidence": ["call_1"]}],
    "summary": "One import-time use."}

STAGE_SCRIPT = [_turn("u", [_call("call_1", "find_usages",
                                  {"symbol": "numpy.NaN"})]),
                _turn("d", [_call("call_2", "submit_findings",
                                   SUBMIT_GROUNDED)])]


def test_stage_investigate_preset_runs_loop_and_verifies(
        monkeypatch, tmp_path, pkg):
    ctx = _stage_ctx(monkeypatch, tmp_path, "m4s", "test-preset",
                     STAGE_SCRIPT)
    _stage_state(ctx, pkg)
    stages_mod.stage_investigate(ctx)
    inv = store.read_json(ctx.dir / "artifacts" / "investigation.json")
    assert inv["status"] == "complete"
    assert inv["findings"][0]["verified"] is True
    assert ctx.state["investigation"]["status"] == "complete"
    assert "pkg.mod" in ctx.state["graph"].nodes
    rows = store.read_jsonl(ctx.dir / store.EVENTS)
    assert "tool_call" in [r["type"] for r in rows]


def test_stage_investigate_skips_without_key(monkeypatch, tmp_path, pkg):
    ctx = _stage_ctx(monkeypatch, tmp_path, "m4k", "test-preset",
                     has_key=False)
    _stage_state(ctx, pkg)
    stages_mod.stage_investigate(ctx)
    inv = store.read_json(ctx.dir / "artifacts" / "investigation.json")
    assert inv["status"] == "skipped"
    assert "MODEL_API_KEY" in inv["reason"]
    assert not (ctx.dir / store.LLM_CALLS).exists()


def test_stage_investigate_skips_untouched(monkeypatch, tmp_path, pkg):
    ctx = _stage_ctx(monkeypatch, tmp_path, "m4u", None, STAGE_SCRIPT)
    _stage_state(ctx, pkg, decision_pkg="notimported", preset=False)
    stages_mod.stage_investigate(ctx)
    inv = store.read_json(ctx.dir / "artifacts" / "investigation.json")
    assert inv["status"] == "skipped"
    assert inv["reason"] == "untouched"
    assert ctx.state["untouched"] is True
    assert "graph" not in ctx.state


def test_stage_investigate_generic_no_suite_runs_loop(
        monkeypatch, tmp_path, pkg):
    ctx = _stage_ctx(monkeypatch, tmp_path, "m4g", None, STAGE_SCRIPT)
    _stage_state(ctx, pkg, decision_pkg="numpy", preset=False)
    stages_mod.stage_investigate(ctx)
    assert ctx.state["testmap_ready"] is True
    assert ctx.state["testmap"] is None
    inv = store.read_json(ctx.dir / "artifacts" / "investigation.json")
    assert inv["status"] == "complete"


def test_stage_verify_reuses_investigate_before_suite(
        monkeypatch, tmp_path, pkg):
    ctx = _stage_ctx(monkeypatch, tmp_path, "m4v", None)
    _stage_state(ctx, pkg, decision_pkg="numpy", has_suite=True,
                 preset=False)
    monkeypatch.setattr(ctx, "env_python",
                        lambda e: "definitely-not-an-interpreter")
    calls = []
    real_run_cmd = ctx.run_cmd

    def spy(cmd, cwd=".", env_name=None, timeout=300, **kw):
        calls.append({"cmd": cmd, "env_name": env_name})
        return real_run_cmd(cmd, cwd=cwd, env_name=env_name,
                            timeout=timeout, **kw)

    monkeypatch.setattr(ctx, "run_cmd", spy)
    ctx.state["testmap_ready"] = True
    ctx.state["before_suite"] = SuiteResult(counts={"passed": 1},
                                            failed=set(),
                                            error_files=set())
    ctx.state["before_output"] = "1 passed in 0.1s\n"
    ctx.state["testmap"] = None
    ctx.state["envs"] = {}
    stages_mod.stage_verify(ctx)
    assert (ctx.dir / "artifacts" / "verify" / "before-suite.log").is_file()
    before_pytests = [c for c in calls
                      if (c["env_name"] or "").endswith("/before")
                      and "pytest" in c["cmd"]]
    assert before_pytests == []
    assert any("pytest" in c["cmd"] for c in calls)  # after suite ran


def test_stage_verify_runs_before_suite_without_marker(
        monkeypatch, tmp_path, pkg):
    ctx = _stage_ctx(monkeypatch, tmp_path, "m4n", None)
    _stage_state(ctx, pkg, decision_pkg="numpy", has_suite=True,
                 preset=False)
    monkeypatch.setattr(ctx, "env_python",
                        lambda e: "definitely-not-an-interpreter")
    calls = []
    real_run_cmd = ctx.run_cmd

    def spy(cmd, cwd=".", env_name=None, timeout=300, **kw):
        calls.append({"cmd": cmd, "env_name": env_name})
        return real_run_cmd(cmd, cwd=cwd, env_name=env_name,
                            timeout=timeout, **kw)

    monkeypatch.setattr(ctx, "run_cmd", spy)
    ctx.state["envs"] = {}
    stages_mod.stage_verify(ctx)
    before_pytests = [c for c in calls
                      if (c["env_name"] or "").endswith("/before")
                      and "pytest" in c["cmd"]]
    assert len(before_pytests) >= 1


def test_stage_predict_reuses_investigate_graph(monkeypatch, tmp_path,
                                                pkg):
    ctx = _stage_ctx(monkeypatch, tmp_path, "m4p", "test-preset",
                     STAGE_SCRIPT)
    _stage_state(ctx, pkg)
    stages_mod.stage_investigate(ctx)
    graph = ctx.state["graph"]
    stages_mod.stage_predict(ctx)
    assert ctx.state["graph"] is graph
    pred = store.read_json(ctx.dir / "artifacts" / "prediction.json")
    assert "pkg.mod" in pred["hard"]


# -- M4g: adjusted prediction + ablation --

def _complete_investigation(triage=None, findings=None):
    return {"status": "complete", "findings": findings or [],
            "triage": triage or [], "summary": "s", "trace": [],
            "usage": {}}


def _triage(symbol, relevant, verified=True):
    return {"symbol": symbol, "relevant": relevant, "reason": "r",
            "evidence": ["call_1"], "verified": verified,
            "verify_reason": "" if verified else "nope"}


def test_predict_applies_verified_triage(monkeypatch, tmp_path, pkg):
    ctx = _stage_ctx(monkeypatch, tmp_path, "m4t", "test-preset")
    _stage_state(ctx, pkg)
    ctx.state["investigation"] = _complete_investigation(
        triage=[_triage("numpy.NaN", False)])
    stages_mod.stage_predict(ctx)
    pred = store.read_json(ctx.dir / "artifacts" / "prediction.json")
    static = store.read_json(
        ctx.dir / "artifacts" / "prediction_static.json")
    assert pred["hard"] == []
    assert "pkg.mod" in static["hard"]
    assert "pkg.mod" in ctx.state["static"]["hard"]
    assert pred["change_symbols"] == ["numpy.NaN"]


def test_predict_ignores_unverified_triage(monkeypatch, tmp_path, pkg):
    ctx = _stage_ctx(monkeypatch, tmp_path, "m4u2", "test-preset")
    _stage_state(ctx, pkg)
    ctx.state["investigation"] = _complete_investigation(
        triage=[_triage("numpy.NaN", False, verified=False)])
    stages_mod.stage_predict(ctx)
    pred = store.read_json(ctx.dir / "artifacts" / "prediction.json")
    static = store.read_json(
        ctx.dir / "artifacts" / "prediction_static.json")
    assert "pkg.mod" in pred["hard"]
    assert pred["hard"] == static["hard"]


def test_verify_adds_agent_tests_and_behavior(monkeypatch, tmp_path,
                                              pkg):
    ctx = _stage_ctx(monkeypatch, tmp_path, "m4a", None)
    _stage_state(ctx, pkg, decision_pkg="numpy", has_suite=True,
                 preset=False)
    monkeypatch.setattr(ctx, "env_python",
                        lambda e: "definitely-not-an-interpreter")
    site = sorted(break_lines(build_graph_from_repo(str(pkg), "pkg"),
                              {"numpy.NaN"}, str(pkg)))[0]
    ctx.state["investigation"] = _complete_investigation(
        findings=[
            {"id": "F1", "claim": "c", "kind": "breaks_call",
             "symbol": "numpy.NaN", "sites": [], "tests": ["agent-t"],
             "evidence": ["call_1"], "confidence": "confirmed",
             "verified": True, "verify_reason": ""},
            {"id": "F2", "claim": "c", "kind": "behavior_change",
             "symbol": "numpy.NaN", "sites": [site], "tests": [],
             "evidence": ["call_2"], "confidence": "confirmed",
             "verified": True, "verify_reason": ""}])
    stages_mod.stage_predict(ctx)
    ctx.state["testmap_ready"] = True
    ctx.state["before_suite"] = SuiteResult(counts={"passed": 1},
                                            failed=set(),
                                            error_files=set())
    ctx.state["before_output"] = "1 passed\n"
    ctx.state["testmap"] = TestMap(
        target="workspaces/t", package="pkg", built_with="ws/before",
        tests={"t1": ["pkg.mod"]}, collected=1,
        line_hits={"t1": [site]})
    ctx.state["envs"] = {}
    stages_mod.stage_verify(ctx)
    scores = store.read_json(ctx.dir / "artifacts" / "scores.json")
    assert scores["static_predicted_failing"] == ["t1"]
    assert scores["agent_added_tests"] == ["agent-t"]
    assert scores["predicted_failing"] == ["agent-t", "t1"]
    assert scores["behavior"]["predicted"] == ["t1"]
    assert scores["behavior"]["confirmed"] == 1
    assert scores["static_precision"] is not None


def test_report_carries_ablation_and_investigation(monkeypatch, tmp_path,
                                                   pkg):
    ctx = _stage_ctx(monkeypatch, tmp_path, "m4r2", "test-preset",
                     has_key=False)
    _stage_state(ctx, pkg)
    ctx.state["scenario"].run_ruff = False
    ctx.state["investigation"] = _complete_investigation(
        triage=[_triage("numpy.NaN", True)])
    stages_mod.stage_predict(ctx)
    ctx.state["probes"] = [("layer1", ProbeResult(
        ok=False, numpy_version="2.0.2", frames=[], modules=["pkg.mod"],
        stderr="boom"))]
    stages_mod.stage_report(ctx)
    report = store.read_json(ctx.dir / "artifacts" / "report.json")
    assert "pkg.mod" in report["ablation"]["static"]["hard"]
    assert report["ablation"]["agent"]["hard"] == \
        report["ablation"]["static"]["hard"]
    assert report["ablation"]["test"]["static_precision"] is None
    assert report["investigation"]["status"] == "complete"
    assert "did not improve" in report["ablation"]["note"] \
        or "No test suite" in report["ablation"]["note"]
    md = (ctx.dir / "artifacts" / "report.md").read_text(encoding="utf-8")
    assert "## Investigator" in md
