"""Run store tests: replay honesty, cancel, SSE resume (fast fake stages).

The full nibabel proof lives in runs/presets/ (test_preset_replay and
test_preset_scan); these tests cover the machinery with hermetic runs.
"""

import json
import subprocess
import sys
import time

import pytest

from chainreaction import llm as llm_mod
from chainreaction import scenarios
from chainreaction.runs import stages as stages_mod
from chainreaction.runs import store, worker
from chainreaction.runs.context import ReplayMiss, RunContext
from chainreaction.runs.scrub import ROOT


@pytest.fixture()
def tmp_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(tmp_path / "runs"))
    return tmp_path / "runs"


def _wait_done(run_id, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        manifest = store.read_json(store.run_dir(run_id) / store.MANIFEST)
        if manifest.get("status") in store.TERMINAL:
            return manifest
        assert time.monotonic() < deadline, f"run {run_id} did not finish"
        time.sleep(0.05)


def _sse_ids(text):
    return [int(line.split("id:")[1].strip()) for line in text.splitlines()
            if line.startswith("id:")]


def _live_manifest(run_id, **fields):
    manifest = {"run_id": run_id, "preset": "nibabel-numpy2",
                "mode": "live", "model": llm_mod.model_name(),
                "status": "running"}
    manifest.update(fields)
    store.write_json(store.run_dir(run_id) / store.MANIFEST, manifest)
    return manifest


# -- gateway record/replay (unit) --

def test_llm_records_and_replays_in_order(tmp_runs):
    def fake(system, user, max_tokens, response_format):
        return llm_mod.ChatResult(f"reply:{user}", "fake-model",
                                  {"total_tokens": 3}, "stop", False, None)

    live = RunContext(run_id="u1", manifest=_live_manifest("u1"),
                      mode="live", live_llm=fake)
    assert live.llm("sys", "a") == "reply:a"
    assert live.llm("sys", "a") == "reply:a"  # same prompt, 2nd occurrence
    recs = store.read_jsonl(live.dir / store.LLM_CALLS)
    assert len(recs) == 2
    assert recs[0]["prompt_hash"] == recs[1]["prompt_hash"]
    assert recs[0]["usage"] == {"total_tokens": 3}
    assert recs[0]["cached"] is False

    replay = RunContext(
        run_id="u2", manifest=_live_manifest("u2", mode="replay",
                                             model="fake-model"),
        mode="replay", source_dir=live.dir)
    assert replay.llm("sys", "a") == "reply:a"
    assert replay.llm("sys", "a") == "reply:a"
    assert replay.replay_warnings() == []


def test_llm_records_cache_hits_as_cached(tmp_runs):
    def fake(system, user, max_tokens, response_format):
        return llm_mod.ChatResult("old", "fake-model", None, None, True,
                                  "2026-01-01T00:00:00Z")

    live = RunContext(run_id="u1", manifest=_live_manifest("u1"),
                      mode="live", live_llm=fake)
    assert live.llm("sys", "a") == "old"
    recs = store.read_jsonl(live.dir / store.LLM_CALLS)
    assert recs[0]["cached"] is True
    assert recs[0]["cache_mtime"] == "2026-01-01T00:00:00Z"


def test_llm_length_retry_records_both_attempts(tmp_runs):
    seen = []

    def fake(system, user, max_tokens, response_format):
        seen.append(max_tokens)
        if len(seen) == 1:
            return llm_mod.ChatResult("truncated", "fake-model", None,
                                      "length", False, None)
        return llm_mod.ChatResult("complete", "fake-model", None,
                                  "stop", False, None)

    live = RunContext(run_id="u1", manifest=_live_manifest("u1"),
                      mode="live", live_llm=fake)
    assert live.llm("sys", "a", max_tokens=100) == "complete"
    assert seen == [100, 150]
    recs = store.read_jsonl(live.dir / store.LLM_CALLS)
    assert len(recs) == 2
    assert recs[0]["finish_reason"] == "length"
    assert recs[1]["finish_reason"] == "stop"
    assert recs[0]["params"]["max_tokens"] == 100
    assert recs[1]["params"]["max_tokens"] == 150

    replay = RunContext(
        run_id="u2", manifest=_live_manifest("u2", mode="replay",
                                             model="fake-model"),
        mode="replay", source_dir=live.dir)
    assert replay.llm("sys", "a", max_tokens=100) == "complete"
    assert replay.replay_warnings() == []


def test_llm_failed_call_records_tombstone_and_replay_reraises(tmp_runs):
    calls = []

    def fake(system, user, max_tokens, response_format):
        calls.append(user)
        if len(calls) == 1:
            raise RuntimeError(
                "empty response from model (finish_reason=length)")
        return llm_mod.ChatResult("recovered", llm_mod.model_name(),
                                  None, "stop", False, None)

    live = RunContext(run_id="u1", manifest=_live_manifest("u1"),
                      mode="live", live_llm=fake)
    with pytest.raises(RuntimeError, match="finish_reason=length"):
        live.llm("sys", "a")
    assert live.llm("sys", "b") == "recovered"
    recs = store.read_jsonl(live.dir / store.LLM_CALLS)
    assert len(recs) == 2
    assert recs[0]["response"] is None
    assert recs[0]["error_class"] == "RuntimeError"
    assert recs[0]["error"] == \
        "empty response from model (finish_reason=length)"

    replay = RunContext(
        run_id="u2", manifest=_live_manifest("u2", mode="replay"),
        mode="replay", source_dir=live.dir)
    with pytest.raises(RuntimeError, match="finish_reason=length"):
        replay.llm("sys", "a")
    assert replay.llm("sys", "b") == "recovered"
    assert replay.replay_warnings() == []


def test_fetch_records_and_replays(tmp_runs):
    def fake(url, timeout):
        return 200, b"<html><body>guide words</body></html>"

    live = RunContext(run_id="u1", manifest=_live_manifest("u1"),
                      mode="live", live_fetch=fake)
    assert live.fetch("https://example.com/g") == "guide words"
    recs = store.read_jsonl(live.dir / store.FETCHES)
    assert recs[0]["status"] == 200
    assert len(recs[0]["content_sha256"]) == 64

    replay = RunContext(run_id="u2", manifest=_live_manifest("u2"),
                        mode="replay", source_dir=live.dir)
    assert replay.fetch("https://example.com/g") == "guide words"


def test_replay_miss_names_hash_and_command(tmp_runs):
    def fake(system, user, max_tokens, response_format):
        return llm_mod.ChatResult("r", "fake-model", None, "stop",
                                  False, None)

    live = RunContext(run_id="u1", manifest=_live_manifest("u1"),
                      mode="live", live_llm=fake)
    live.llm("sys", "a")
    live.run_cmd([sys.executable, "-c", "print('x')"], cwd=".",
                 env_name=None)
    prompt_hash = store.read_jsonl(live.dir / store.LLM_CALLS)[0][
        "prompt_hash"]
    # Delete the model recording: replay must fail naming the hash.
    (live.dir / store.LLM_CALLS).write_text("", encoding="utf-8")
    replay = RunContext(
        run_id="u2", manifest=_live_manifest("u2", mode="replay",
                                             model="fake-model"),
        mode="replay", source_dir=live.dir)
    with pytest.raises(ReplayMiss, match=prompt_hash):
        replay.llm("sys", "a")
    # The command recording survived: replay still serves it.
    res = replay.run_cmd([sys.executable, "-c", "print('x')"], cwd=".",
                         env_name=None)
    assert res.stdout == "x\n" and res.returncode == 0
    # An unrecorded command names the command.
    with pytest.raises(ReplayMiss, match="print"):
        replay.run_cmd([sys.executable, "-c", "print('other')"], cwd=".",
                       env_name=None)


def test_replay_cmd_matches_across_tb_flavor(tmp_runs):
    live = RunContext(run_id="u1", manifest=_live_manifest("u1"),
                      mode="live")
    live.run_cmd([sys.executable, "-c", "print('x')", "--tb=no"],
                 cwd=".", env_name=None)
    recs = store.read_jsonl(live.dir / store.CMDS)
    assert "--tb=no" in recs[0]["cmd"]  # stored argv stays truthful
    replay = RunContext(
        run_id="u2", manifest=_live_manifest("u2", mode="replay"),
        mode="replay", source_dir=live.dir)
    # Current builders emit --tb=short; --tb=no records still match.
    res = replay.run_cmd(
        [sys.executable, "-c", "print('x')", "--tb=short"],
        cwd=".", env_name=None)
    assert res.stdout == "x\n" and res.returncode == 0
    assert replay.replay_warnings() == []


def test_suite_builders_emit_short_tracebacks():
    from types import SimpleNamespace

    seen = []

    def fake_run(cmd, cwd, extra_env=None):
        seen.append(cmd)
        return SimpleNamespace(stdout="2 passed in 0.1s\n", stderr="",
                               timed_out=False)

    _, _, escalated = stages_mod._run_after_suite(
        fake_run, "py", "t", "workspaces/k", None, lambda *a: None)
    assert escalated is False
    stages_mod._run_plain_before(fake_run, "py", "t", "workspaces/k")
    assert len(seen) == 2
    assert [c for c in seen if "--tb=short" in c] == seen


def test_m3_interpret_keeps_guide_url_a_user_input(tmp_runs):
    """The discovered URL must not land in manifest guide_url.

    manifest guide_url is discovery INPUT (the user's --guide). When
    the M3 stage writes the discovered URL back into it, replay takes
    the guide shortcut instead of live's search path, the notes
    differ, and the first interpret prompt misses its recording.
    """
    import json
    from types import SimpleNamespace

    pad = ("padding prose to exceed the minimum usable length "
           "threshold for discovery sources. " * 6)
    page31 = "https://github.com/org/pkg/blob/main/CHANGES-3.1.md"
    page30 = "https://github.com/org/pkg/blob/main/CHANGES-3.0.md"
    pypi = "https://pypi.org/pypi/pkg/json"
    fetch_map = {
        pypi: (200, json.dumps({
            "info": {"home_page": "https://github.com/org/pkg",
                     "project_urls": {"Changelog": page30}},
            "releases": {"3.0.0": [], "3.0.1": [], "3.1.0": []},
        }).encode()),
        page31: (200, (f"# Changes\n\n## 3.1.0\n\n"
                       f"- `contextfunction` replaced by `pass_context`. "
                       f"{pad}\n").encode()),
        page30: (200, (f"# Changes\n\n## 3.0.1\n\n"
                       f"- `oldthing` was removed, use `newthing` instead. "
                       f"{pad}\n").encode()),
    }

    def live_fetch(url, timeout):
        return fetch_map.get(url, (404, b""))

    def live_search(query):
        return llm_mod.WebSearchResult(
            "reply", [llm_mod.WebCitation(page31, "C", "s")],
            1, "fake-model", None, False, None)

    def live_llm(system, user, max_tokens, response_format):
        return llm_mod.ChatResult('{"items": []}', llm_mod.model_name(),
                                  None, "stop", False, None)

    def make_ctx(run_id, manifest, mode, source_dir=None):
        return RunContext(run_id=run_id, manifest=manifest, mode=mode,
                          live_fetch=live_fetch, live_search=live_search,
                          live_llm=live_llm, source_dir=source_dir)

    scen = SimpleNamespace(package="pkg")
    parsed = SimpleNamespace(package="pkg", from_version="3.0.0",
                             to_version="3.1.0")
    decision = "upgrade pkg to 3.1"
    live = make_ctx("u1", _live_manifest("u1"), "live")
    stages_mod._stage_interpret_m3(live, scen, parsed, decision)
    assert live.manifest.get("guide_url") in (None, "")
    live_symbols = {i.symbol for i in live.state["changeset"].items}
    assert live_symbols  # deterministic rows complete the empty model pass
    assert live.state["interpret_stats"]["releases"] == ["3.0.1", "3.1.0"]

    # Replay from the persisted manifest (what the preset carries).
    persisted = store.read_json(store.run_dir("u1") / store.MANIFEST)
    persisted.update(run_id="u2", mode="replay", status="running")
    store.write_json(store.run_dir("u2") / store.MANIFEST, persisted)
    replay = make_ctx("u2", persisted, "replay",
                      source_dir=store.run_dir("u1"))
    stages_mod._stage_interpret_m3(replay, scen, parsed, decision)
    assert {i.symbol for i in replay.state["changeset"].items} == \
        live_symbols
    assert replay.replay_warnings() == []


def test_probe_adapter_parses_modules_through_scrub(tmp_runs, tmp_path):
    from chainreaction.verifier import run_snippet_probe

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "svc.py").write_text(
        "def boom():\n    raise ValueError('nope')\n", encoding="utf-8")
    ctx = RunContext(run_id="u1", manifest=_live_manifest("u1"),
                     mode="live")
    run = stages_mod._env_adapter(ctx, "fake-env", timeout=60)
    res = run_snippet_probe(sys.executable, str(tmp_path),
                            "from pkg.svc import boom\nboom()\n",
                            package="pkg", run=run)
    # Traceback paths are recorded scrubbed, but module mapping must
    # still work (it parses the unscrubbed text).
    assert res.modules == ["pkg.svc"]
    rec = store.read_jsonl(ctx.dir / store.CMDS)[0]
    assert "<tmp>" in rec["stderr"] or "<home>" in rec["stderr"] \
        or "<root>" in rec["stderr"]


def test_env_adapter_adds_pythonpath_when_not_editable(tmp_runs):
    from types import SimpleNamespace

    from chainreaction.generic import ProjectInfo

    captured = {}

    def run_cmd(cmd, cwd=".", env_name=None, timeout=900, tool=None,
                extra_env=None):
        captured.update(extra_env or {})
        return SimpleNamespace(returncode=0, stdout="", stderr="",
                               missing=None, timed_out=False)

    ctx = RunContext(run_id="u1", manifest=_live_manifest("u1"), mode="live")
    ctx.run_cmd = run_cmd
    ctx.state["project"] = ProjectInfo(
        package="demo", editable=False, pythonpath="src")
    run = stages_mod._env_adapter(ctx, "fake-env", timeout=60)
    run(["echo", "hi"], cwd=".", extra_env={"COLUMNS": "200"})
    assert captured == {"COLUMNS": "200", "PYTHONPATH": "src"}
    captured.clear()
    ctx.state["project"] = ProjectInfo(package="demo")
    run(["echo", "hi"], cwd=".")
    assert captured == {}


def test_fetch_returns_table_rows(tmp_runs):
    html = (b"<html><body><h2>Main namespace</h2>"
            b"<p>members that have been removed:</p>"
            b"<table><tbody><tr><td><p>sctypes</p></td>"
            b"<td><p>Access dtypes explicitly instead.</p></td></tr>"
            b"</tbody></table></body></html>")
    live = RunContext(run_id="u1", manifest=_live_manifest("u1"),
                      mode="live",
                      live_fetch=lambda url, timeout: (200, html))
    out = live.fetch("https://example.com/g")
    assert "sctypes" in out
    rows = out.removal_table["removed"]
    assert [r["symbol"] for r in rows] == ["numpy.sctypes"]
    assert all(r["quote"] in out for r in rows)
    rec = store.read_jsonl(live.dir / store.FETCHES)[0]
    assert rec["removal_table"]["removed"][0]["symbol"] == "numpy.sctypes"
    replay = RunContext(run_id="u2", manifest=_live_manifest("u2"),
                        mode="replay", source_dir=live.dir)
    assert replay.fetch("https://example.com/g").removal_table == \
        out.removal_table


def test_replay_warns_on_unused_recordings(tmp_runs):
    def fake(system, user, max_tokens, response_format):
        return llm_mod.ChatResult("r", "fake-model", None, "stop",
                                  False, None)

    live = RunContext(run_id="u1", manifest=_live_manifest("u1"),
                      mode="live", live_llm=fake)
    live.llm("sys", "a")
    live.llm("sys", "b")  # replay will never ask for this one
    replay = RunContext(
        run_id="u2", manifest=_live_manifest("u2", mode="replay",
                                             model="fake-model"),
        mode="replay", source_dir=live.dir)
    replay.llm("sys", "a")
    warnings = replay.replay_warnings()
    assert len(warnings) == 1 and "1 unused llm" in warnings[0]


def test_run_cmd_echo_and_missing_tool(tmp_runs):
    live = RunContext(run_id="u1", manifest=_live_manifest("u1"))
    res = live.run_cmd([sys.executable, "-c", "print('hi')"], cwd=".",
                       env_name=None)
    assert (res.returncode, res.stdout) == (0, "hi\n")
    assert res.versions == {}
    missing = live.run_cmd(["definitely-missing-tool-xyz", "--version"],
                           cwd=".", env_name=None)
    assert missing.missing == "definitely-missing-tool-xyz"
    assert missing.returncode is None
    replay = RunContext(run_id="u2", manifest=_live_manifest("u2"),
                        mode="replay", source_dir=live.dir)
    res2 = replay.run_cmd([sys.executable, "-c", "print('hi')"], cwd=".",
                          env_name=None)
    assert (res2.returncode, res2.stdout) == (0, "hi\n")


def test_tool_cmd_records_logical_name_cross_os(tmp_runs, monkeypatch):
    import chainreaction.runs.context as ctx_mod

    monkeypatch.setattr(ctx_mod, "resolve_tool", lambda name: sys.executable)
    live = RunContext(run_id="u1", manifest=_live_manifest("u1"))
    res = live.run_cmd(["C:\\venv\\Scripts\\ruff.exe", "-c", "print('x')"],
                       cwd=".", env_name=None, tool="ruff")
    assert (res.returncode, res.stdout) == (0, "x\n")
    rec = store.read_jsonl(live.dir / store.CMDS)[0]
    assert rec["cmd"][0] == "<tool:ruff>"
    assert rec["missing"] is None

    # Replay on another OS resolves nothing: a different argv[0] (or no
    # executable at all) still serves the recording by logical name.
    def _boom(name):
        raise AssertionError("replay must not resolve tools")

    monkeypatch.setattr(ctx_mod, "resolve_tool", _boom)
    replay = RunContext(run_id="u2", manifest=_live_manifest("u2"),
                        mode="replay", source_dir=live.dir)
    res2 = replay.run_cmd(["/home/u/.local/bin/ruff", "-c", "print('x')"],
                          cwd=".", env_name=None, tool="ruff")
    assert (res2.returncode, res2.stdout) == (0, "x\n")


def test_tool_cmd_missing_live_stays_missing_on_replay(tmp_runs, monkeypatch):
    import chainreaction.runs.context as ctx_mod

    monkeypatch.setattr(ctx_mod, "resolve_tool", lambda name: None)
    live = RunContext(run_id="u1", manifest=_live_manifest("u1"))
    res = live.run_cmd(["ruff", "--version"], cwd=".", env_name=None,
                       tool="ruff")
    assert res.missing == "<tool:ruff>"
    assert res.returncode is None
    replay = RunContext(run_id="u2", manifest=_live_manifest("u2"),
                        mode="replay", source_dir=live.dir)
    res2 = replay.run_cmd(["ruff", "--version"], cwd=".", env_name=None,
                          tool="ruff")
    assert res2.missing == "<tool:ruff>"


def test_run_cmd_timeout_kills_and_records(tmp_runs):
    live = RunContext(run_id="u1", manifest=_live_manifest("u1"))
    res = live.run_cmd(
        [sys.executable, "-c", "import time; time.sleep(30)"], cwd=".",
        env_name=None, timeout=1)
    assert res.timed_out is True
    assert res.returncode != 0
    assert live.live_children == set()


def test_scrubber_replaces_paths_and_key(tmp_path, monkeypatch):
    from chainreaction.runs.scrub import Scrubber

    monkeypatch.setenv("MODEL_API_KEY", "sk-test-secret")
    scrub = Scrubber.default()
    dirty = (f'File "{ROOT}\\targets\\nibabel\\nibabel\\casting.py", line 1\r\n'
             f'File "{ROOT}/envs/numpy2/lib/site-packages/numpy/x.py"\r\n'
             "key=sk-test-secret\r\n"
             '{"json": "a\\"b"}\n')
    clean = scrub.text(dirty)
    assert "\r" not in clean
    assert "sk-test-secret" not in clean
    assert "<root>/targets/nibabel/nibabel/casting.py" in clean
    # Lib/site-packages collapses: Windows and Linux recordings agree.
    assert "<env:numpy2>/site-packages/numpy/x.py" in clean
    assert '{"json": "a\\"b"}' in clean  # escapes outside paths survive


def test_scrubber_normalizes_foreign_env_and_tmp_paths():
    import os
    import tempfile

    from chainreaction.runs.scrub import Scrubber

    scrub = Scrubber.default()
    linux = "/other/checkout/envs/numpy2/lib/python3.11/site-packages/n.py"
    assert scrub.text(f'File "{linux}", line 1') == \
        'File "<env:numpy2>/site-packages/n.py", line 1'
    mac = "/Users/u/work/envs/sponsorfit/lib/python3.12/site-packages/m.py"
    assert scrub.text(mac) == "<env:sponsorfit>/site-packages/m.py"
    win = ("E:\\work\\envs\\numpy2\\Lib\\site-packages\\numpy\\__init__.py")
    assert scrub.text(win) == "<env:numpy2>/site-packages/numpy/__init__.py"
    tmp = os.path.join(tempfile.gettempdir(), "cr-work", "w.py")
    assert scrub.text(tmp) == "<tmp>/cr-work/w.py"
    # Bare relative references are documentation, not paths: untouched.
    assert scrub.text("see envs/numpy2 for details") == \
        "see envs/numpy2 for details"
    twice = scrub.text(scrub.text(linux))
    assert twice == scrub.text(linux)  # idempotent


def test_untext_restores_forward_slash_json_safe_paths():
    from chainreaction.runs.scrub import Scrubber

    scrub = Scrubber.default(ws_pin="E:\\fake\\ws")
    # Native backslashes would corrupt JSON escapes in coverage dumps.
    assert scrub.untext('{"<ws>/src/pkg/a.py"}') == '{"E:/fake/ws/src/pkg/a.py"}'
    assert scrub.untext('{"a": 1}') == '{"a": 1}'
    import json

    json.loads(scrub.untext('{"<ws>/x.py": {"1": ["t"]}}'))


def test_untext_prefers_pinned_workspace(tmp_path, monkeypatch):
    import chainreaction.runs.scrub as scrub_mod
    from chainreaction.runs.scrub import Scrubber

    (tmp_path / "workspaces" / "a__x@111").mkdir(parents=True)
    (tmp_path / "workspaces" / ".staging-deadbeef").mkdir(parents=True)
    monkeypatch.setattr(scrub_mod, "ROOT", tmp_path)
    scrub = Scrubber.default(ws_pin=str(tmp_path / "workspaces" / "b__y@222"))
    assert scrub.untext("<ws>/f.py") == \
        str(tmp_path / "workspaces" / "b__y@222").replace("\\", "/") + "/f.py"
    # Staging dirs never contribute pairs.
    assert not [n for n, _ in Scrubber.default().replacements
                if ".staging-" in n]


def test_interpret_falls_back_without_key(tmp_runs, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    manifest = _live_manifest("u1")
    ctx = RunContext(run_id="u1", manifest=manifest, mode="live")
    ctx.state["scenario"] = scenarios.get("nibabel-numpy2")
    stages_mod.stage_interpret(ctx)
    assert ctx.manifest["changeset_source"] == "hand-recorded"
    assert (ctx.dir / "artifacts" / "changeset.json").is_file()
    saved = json.loads(
        (ctx.dir / "artifacts" / "changeset.json").read_text(
            encoding="utf-8"))
    assert saved["interpret_recall"] is None
    assert not (ctx.dir / store.LLM_CALLS).is_file()
    assert not (ctx.dir / store.FETCHES).is_file()
    # The shared scenario stubs are untouched (verified stays False).
    assert all(not i.verified
               for i in scenarios.get("nibabel-numpy2").stub_items)


# -- worker lifecycle (fake stages, real pool) --

def _mini_stages():
    def s_one(ctx):
        ctx.emit("log", {"message": "one"})
        ctx.save_artifact("llm.txt", ctx.llm("sys", "prompt-a"))

    def s_two(ctx):
        text = ctx.fetch("https://example.com/guide")
        res = ctx.run_cmd([sys.executable, "-c", "print('two')"], cwd=".",
                          env_name=None)
        ctx.save_artifact("report.json", {"text": text, "out": res.stdout})

    return [("one", s_one), ("two", s_two)]


def _patch_fakes(monkeypatch):
    calls = {"llm": 0, "fetch": 0}

    def fake_chat_full(system, user, max_tokens=8000,
                       response_format=None, reasoning_effort=None):
        calls["llm"] += 1
        return llm_mod.ChatResult(f"reply:{user}", llm_mod.model_name(),
                                  {"total_tokens": 3}, "stop", False, None)

    def fake_fetch_page(url, timeout=60):
        calls["fetch"] += 1
        return 200, b"<p>guide words</p>"

    monkeypatch.setattr(llm_mod, "chat_full", fake_chat_full)
    monkeypatch.setattr("chainreaction.runs.context.fetch_page",
                        fake_fetch_page)
    return calls


def test_worker_live_then_replay_is_identical(tmp_runs, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)  # fakes need no key
    calls = _patch_fakes(monkeypatch)
    stages = _mini_stages()
    live = worker.create_run(
        worker.RunRequest(preset="nibabel-numpy2", mode="live"))
    worker.submit_run(live, stages=stages)
    assert _wait_done(live)["status"] == "done"
    assert calls == {"llm": 1, "fetch": 1}

    replay = worker.create_run(worker.RunRequest(replay_from=live,
                                                 mode="replay"))
    worker.submit_run(replay, stages=stages)
    manifest = _wait_done(replay)
    assert manifest["status"] == "done"
    assert manifest["mode"] == "replay"
    assert manifest["replay_from"] == live
    # Replay served every gateway from the recording: no new live calls.
    assert calls == {"llm": 1, "fetch": 1}
    for name in ("llm.txt", "report.json"):
        live_bytes = (store.run_dir(live) / "artifacts" / name).read_bytes()
        replay_bytes = (store.run_dir(replay) / "artifacts"
                        / name).read_bytes()
        assert live_bytes == replay_bytes
    live_events = store.read_jsonl(store.run_dir(live) / store.EVENTS)
    replay_events = store.read_jsonl(store.run_dir(replay) / store.EVENTS)
    assert [e["seq"] for e in live_events] == list(
        range(1, len(live_events) + 1))
    assert len(replay_events) == len(live_events)


def test_worker_replay_miss_fails_loudly(tmp_runs, monkeypatch):
    _patch_fakes(monkeypatch)
    stages = _mini_stages()
    live = worker.create_run(
        worker.RunRequest(preset="nibabel-numpy2", mode="live"))
    worker.submit_run(live, stages=stages)
    assert _wait_done(live)["status"] == "done"
    prompt_hash = store.read_jsonl(
        store.run_dir(live) / store.LLM_CALLS)[0]["prompt_hash"]
    (store.run_dir(live) / store.LLM_CALLS).write_text("", encoding="utf-8")

    replay = worker.create_run(worker.RunRequest(replay_from=live,
                                                 mode="replay"))
    worker.submit_run(replay, stages=stages)
    assert _wait_done(replay)["status"] == "failed"
    errors = [e for e in store.read_jsonl(
        store.run_dir(replay) / store.EVENTS) if e["type"] == "error"]
    assert errors and "ReplayMiss" in errors[0]["data"]["error"]
    assert prompt_hash in errors[0]["data"]["error"]


def test_cancel_during_cmd_stops_fast_without_orphans(tmp_runs):
    def s_sleep(ctx):
        ctx.run_cmd([sys.executable, "-c",
                     "import time; time.sleep(60)"], cwd=".",
                    env_name=None, timeout=300)

    run_id = worker.create_run(
        worker.RunRequest(preset="nibabel-numpy2", mode="live"))
    worker.submit_run(run_id, stages=[("verify", s_sleep)])
    handle = worker.get_handle(run_id)
    deadline = time.monotonic() + 15
    while not handle.ctx.live_children:
        assert time.monotonic() < deadline, "subprocess never started"
        time.sleep(0.05)
    proc = next(iter(handle.ctx.live_children))
    t0 = time.monotonic()
    worker.cancel_run(run_id)
    manifest = _wait_done(run_id, timeout=30)
    assert time.monotonic() - t0 < 5
    assert manifest["status"] == "cancelled"
    assert handle.ctx.live_children == set()
    assert proc.poll() is not None  # reaped: no leftover process


def test_write_json_retries_transient_replace_lock(tmp_runs,
                                                     monkeypatch):
    import os

    calls = []
    real_replace = os.replace

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError("[WinError 5] Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    path = tmp_runs / "m" / "manifest.json"
    store.write_json(path, {"status": "done"})
    assert store.read_json(path) == {"status": "done"}
    assert len(calls) == 3
    assert not path.with_name(path.name + ".tmp").exists()


def test_startup_sweep_fails_stale_runs(tmp_runs):
    _live_manifest("stale", status="running", current_stage="x")
    store.append_jsonl(store.run_dir("stale") / store.EVENTS,
                       {"seq": 1, "type": "log", "data": {}})
    assert worker.startup_sweep() == ["stale"]
    swept = store.read_json(store.run_dir("stale") / store.MANIFEST)
    assert swept["status"] == "failed"
    assert swept["error"] == "server restarted"
    assert worker.startup_sweep() == []  # idempotent


def test_create_run_validation(tmp_runs):
    with pytest.raises(ValueError, match="preset"):
        worker.create_run(worker.RunRequest(mode="live"))
    with pytest.raises(ValueError, match="unknown preset"):
        worker.create_run(worker.RunRequest(preset="nope", mode="live"))
    with pytest.raises(ValueError, match="replay_from or preset"):
        worker.create_run(worker.RunRequest(mode="replay"))
    with pytest.raises(ValueError, match="not found"):
        worker.create_run(
            worker.RunRequest(replay_from="missing-run-id", mode="replay"))
    live = worker.create_run(
        worker.RunRequest(preset="nibabel-numpy2", mode="live"))
    with pytest.raises(ValueError, match="need 'done'"):
        worker.create_run(worker.RunRequest(replay_from=live, mode="replay"))
    manifest = store.read_json(store.run_dir(live) / store.MANIFEST)
    assert manifest["status"] == "queued"  # untouched by failed replays
    assert manifest["chainreaction_git_sha"]
    assert "MODEL_API_KEY" not in json.dumps(manifest)


# -- HTTP + SSE --

def _client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from chainreaction.web import app

    return TestClient(app)


def test_api_validation(tmp_runs, monkeypatch):
    client = _client()
    assert client.post("/api/runs", json={}).status_code == 400
    assert client.post("/api/runs",
                       json={"preset": "nope"}).status_code == 400
    assert client.post("/api/runs", json={"mode": "replay",
                                          "preset": "nope"}).status_code == 400
    assert client.get("/api/runs/does-not-exist").status_code == 404
    assert client.post("/api/runs/does-not-exist/cancel").status_code == 404
    assert client.get("/api/runs/does-not-exist/events").status_code == 404
    live = worker.create_run(
        worker.RunRequest(preset="nibabel-numpy2", mode="live"))
    assert client.get(f"/api/runs/{live}/artifacts/nope.txt").status_code \
        == 404
    assert client.get(
        f"/api/runs/{live}/artifacts/%2E%2E/manifest.json").status_code \
        in (400, 404)


def test_api_run_and_artifact_roundtrip(tmp_runs, monkeypatch):
    _patch_fakes(monkeypatch)
    client = _client()
    live = worker.create_run(
        worker.RunRequest(preset="nibabel-numpy2", mode="live"))
    worker.submit_run(live, stages=_mini_stages())
    assert _wait_done(live)["status"] == "done"
    body = client.get(f"/api/runs/{live}").json()
    assert body["status"] == "done"
    assert body["manifest"]["preset"] == "nibabel-numpy2"
    assert body["report"]["out"] == "two\n"
    artifact = client.get(f"/api/runs/{live}/artifacts/report.json")
    assert artifact.status_code == 200
    assert artifact.json()["out"] == "two\n"
    assert any(r["id"] == live for r in client.get("/api/runs").json())


def test_sse_resume_from_seq_and_header(tmp_runs, monkeypatch):
    _patch_fakes(monkeypatch)
    client = _client()
    live = worker.create_run(
        worker.RunRequest(preset="nibabel-numpy2", mode="live"))
    worker.submit_run(live, stages=_mini_stages())
    assert _wait_done(live)["status"] == "done"
    full = client.get(f"/api/runs/{live}/events")
    assert full.headers["content-type"].startswith("text/event-stream")
    ids = _sse_ids(full.text)
    assert ids == sorted(ids) and len(ids) > 4
    mid = ids[len(ids) // 2]
    resumed = _sse_ids(client.get(
        f"/api/runs/{live}/events?from={mid}").text)
    assert resumed == [i for i in ids if i > mid]
    via_header = _sse_ids(client.get(
        f"/api/runs/{live}/events",
        headers={"Last-Event-ID": str(mid)}).text)
    assert via_header == resumed


def test_sse_follows_live_run_to_completion(tmp_runs):
    def s_slow(ctx):
        for i in range(3):
            ctx.emit("log", {"message": f"tick-{i}"})
            time.sleep(0.4)

    client = _client()
    live = worker.create_run(
        worker.RunRequest(preset="nibabel-numpy2", mode="live"))
    worker.submit_run(live, stages=[("slow", s_slow)])
    body = client.get(f"/api/runs/{live}/events").text
    assert "tick-2" in body and "run done" in body


# -- committed preset: the honest-replay proof (acceptance #2, #6) --

def _real_preset():
    preset = store.presets_dir() / "nibabel-numpy2"
    manifest = preset / store.MANIFEST
    if not manifest.is_file():
        pytest.skip("preset not recorded yet")
    return preset, store.read_json(manifest)


def test_preset_scan_finds_no_leaks(monkeypatch):
    monkeypatch.delenv("CHAINREACTION_RUNS_DIR", raising=False)
    preset, _ = _real_preset()
    assert store.scan_for_leaks(preset) == []


def test_record_readers_accept_gzip_twins(tmp_path):
    plain = tmp_path / "cmds.jsonl"
    plain.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
    assert store.read_jsonl(plain) == [{"a": 1}, {"b": 2}]
    gzipped = store.gzip_file(plain)
    assert gzipped.name == "cmds.jsonl.gz" and not plain.exists()
    assert store.read_jsonl(plain) == [{"a": 1}, {"b": 2}]
    assert store.read_jsonl(tmp_path / "missing.jsonl") == []
    doc = tmp_path / "doc.json"
    doc.write_text('{"x": 1}', encoding="utf-8")
    store.gzip_file(doc)
    assert store.read_json(doc) == {"x": 1}
    # Deterministic bytes: same content compresses identically.
    twin = tmp_path / "twin.jsonl"
    twin.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
    assert store.gzip_file(twin).read_bytes() == gzipped.read_bytes()


def test_compress_preset_gzips_only_large_candidates(tmp_path):
    preset = tmp_path / "preset"
    (preset / "artifacts").mkdir(parents=True)
    (preset / "cmds.jsonl").write_text("x" * 200, encoding="utf-8")
    (preset / "fetches.jsonl").write_text("tiny", encoding="utf-8")
    (preset / "artifacts" / "testmap.json").write_text(
        "y" * 200, encoding="utf-8")
    (preset / "artifacts" / "report.json").write_text(
        "z" * 200, encoding="utf-8")
    done = store.compress_preset(preset, threshold=100)
    assert sorted(done) == ["artifacts/testmap.json.gz", "cmds.jsonl.gz"]
    assert (preset / "fetches.jsonl").is_file()  # under threshold
    assert (preset / "artifacts" / "report.json").is_file()  # not listed
    assert store.compress_preset(preset, threshold=100) == []  # idempotent


def test_scan_for_leaks_sees_through_gzip(tmp_path):
    from chainreaction.runs.scrub import ROOT as scrub_root

    clean = tmp_path / "cmds.jsonl"
    clean.write_bytes(b'{"ok": true}\n')
    store.gzip_file(clean)
    assert store.scan_for_leaks(tmp_path) == []
    dirty = tmp_path / "fetches.jsonl"
    dirty.write_bytes(f"oops {scrub_root}\n".encode("utf-8"))
    store.gzip_file(dirty)
    assert any("absolute path" in i for i in store.scan_for_leaks(tmp_path))
    (tmp_path / "bogus.jsonl.gz").write_bytes(b"not gzip")
    assert any("not valid gzip" in i
               for i in store.scan_for_leaks(tmp_path))


def test_copy_recordings_prefers_gzip_twin(tmp_path):
    from types import SimpleNamespace

    src = tmp_path / "src"
    src.mkdir()
    (src / "cmds.jsonl.gz").write_bytes(b"gz-bytes")
    (src / "fetches.jsonl").write_text("plain", encoding="utf-8")
    (src / "searches.jsonl").write_text("{}", encoding="utf-8")
    dst = tmp_path / "dst"
    dst.mkdir()
    worker._copy_recordings(SimpleNamespace(source_dir=src, dir=dst))
    assert (dst / "cmds.jsonl.gz").read_bytes() == b"gz-bytes"
    assert not (dst / "cmds.jsonl").exists()
    assert (dst / "fetches.jsonl").is_file()
    assert (dst / "searches.jsonl").is_file()


def _all_preset_ids():
    presets = ROOT / "runs" / "presets"
    if not presets.is_dir():
        return []
    return sorted(p.name for p in presets.iterdir()
                  if p.is_dir() and (p / store.MANIFEST).is_file())


@pytest.mark.parametrize("preset_id", _all_preset_ids())
def test_preset_replay_matches_committed(monkeypatch, tmp_path, preset_id):
    monkeypatch.delenv("CHAINREACTION_RUNS_DIR", raising=False)
    preset = ROOT / "runs" / "presets" / preset_id
    source = store.read_json(preset / store.MANIFEST)
    ws_rel = source.get("workspace")
    if ws_rel:
        code_dir = ROOT / ws_rel
    else:
        try:
            scen = scenarios.get(preset_id)
        except KeyError:
            pytest.skip(f"unknown preset {preset_id}")
        code_dir = ROOT / scen.target
    if not (code_dir / ".git").is_dir():
        pytest.skip(f"{preset_id} checkout not fetched")
    try:
        proc = subprocess.run(["git", "-C", str(code_dir), "rev-parse",
                               "HEAD"], capture_output=True, text=True,
                              timeout=30)
    except OSError:
        pytest.skip("git unavailable")
    if proc.stdout.strip() != (source.get("resolved_commit") or ""):
        pytest.skip(f"{preset_id} checkout differs from the recorded commit")
    # Acceptance #2 shape: no key, model + guide endpoints unreachable.
    def _no_network(*a, **k):
        raise AssertionError("replay must not touch the network")

    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr(llm_mod, "client",
                        lambda: _no_network())
    monkeypatch.setattr("chainreaction.interpret.fetch_page", _no_network)
    monkeypatch.setattr("chainreaction.runs.context.fetch_page", _no_network)
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(tmp_path / "runs"))
    dest = store.preset_dir(preset_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copytree(preset, dest)
    replay = worker.create_run(
        worker.RunRequest(preset=preset_id, mode="replay"))
    worker.submit_run(replay)
    manifest = _wait_done(replay, timeout=900)
    assert manifest["status"] == "done", manifest.get("error")
    live_report = (dest / "artifacts" / "report.json").read_bytes()
    replay_report = (store.run_dir(replay) / "artifacts"
                     / "report.json").read_bytes()
    assert live_report == replay_report
    if preset_id == "nibabel-numpy2":
        # Non-vacuous layers: the recorded break maps to the documented
        # layer-1 chain (an empty `actual` with recall 1.0 is the tell).
        report = json.loads(replay_report)
        assert report["layers"][0]["actual"] == [
            "nibabel",
            "nibabel.analyze",
            "nibabel.arrayproxy",
            "nibabel.volumeutils",
            "nibabel.casting",
        ]
        assert report["spine_match"] is True


# -- generic verify: after suite runs with the conftest --

def _after_runner(outputs):
    from types import SimpleNamespace

    calls = []

    def run(cmd, cwd, extra_env=None):
        calls.append(cmd)
        return SimpleNamespace(stdout=outputs[len(calls) - 1],
                               timed_out=False)

    return run, calls


def test_after_suite_keeps_conftest_when_collection_succeeds():
    run, calls = _after_runner(["1 failed, 4 passed in 0.2s\n"
                                "FAILED t.py::x - assert\n"])
    after, _, escalated = stages_mod._run_after_suite(
        run, "py", "/t", "ws", None, lambda *a: None)
    assert escalated is False
    assert len(calls) == 1
    assert "--noconftest" not in calls[0]
    assert "." in calls[0]
    assert after.total == 5


def test_after_suite_escalates_to_noconftest_on_collapse():
    run, calls = _after_runner(["INTERNALERROR: boom\n",
                                "2 failed, 3 passed in 0.2s\n"
                                "FAILED t.py::x - assert\n"
                                "FAILED t.py::y - assert\n"])
    after, _, escalated = stages_mod._run_after_suite(
        run, "py", "/t", "ws", None, lambda *a: None)
    assert escalated is True
    assert len(calls) == 2
    assert "--noconftest" not in calls[0]
    assert "--noconftest" in calls[1]
    assert after.total == 5


def test_after_suite_warns_when_escalation_collects_nothing():
    run, _ = _after_runner(["INTERNALERROR: boom\n", "ERROR: bad opt\n"])
    messages = []
    after, _, escalated = stages_mod._run_after_suite(
        run, "py", "/t", "ws", None,
        lambda *a: messages.append(a[1]["message"]))
    assert escalated is True
    assert after.total == 0
    assert any("even without the conftest" in m for m in messages)


def test_after_suite_timeout_raises_without_escalation():
    from types import SimpleNamespace

    calls = []

    def run(cmd, cwd, extra_env=None):
        calls.append(cmd)
        return SimpleNamespace(stdout="", timed_out=True)

    with pytest.raises(RuntimeError, match="timed out"):
        stages_mod._run_after_suite(run, "py", "/t", "ws", None,
                                    lambda *a: None)
    assert len(calls) == 1


def test_recording_before_pytests_drives_replay_decision():
    from types import SimpleNamespace

    cov = {"env_name": "k/before",
           "cmd": ["<env-python>", "-m", "pytest", ".", "--cov=p"]}
    plain = {"env_name": "k/before",
             "cmd": ["<env-python>", "-m", "pytest", ".", "-q"]}
    after = {"env_name": "k/after",
             "cmd": ["<env-python>", "-m", "pytest", ".", "-q"]}
    ctx = SimpleNamespace(_cmd_recs=[cov])
    assert stages_mod._recording_before_pytests(ctx) == (True, False)
    ctx = SimpleNamespace(_cmd_recs=[plain])
    assert stages_mod._recording_before_pytests(ctx) == (False, True)
    ctx = SimpleNamespace(_cmd_recs=[cov, plain])
    assert stages_mod._recording_before_pytests(ctx) == (True, True)
    ctx = SimpleNamespace(_cmd_recs=[after])
    assert stages_mod._recording_before_pytests(ctx) == (False, False)


def test_replay_manifest_carries_generic_provenance(tmp_runs):
    _live_manifest(
        "src1", status="done",
        repo_url="https://github.com/x/y", ref="abc",
        resolved_commit="abc", commit_date="2024-01-01",
        workspace="workspaces/x__y@abc", package="y",
        allow_untrusted=True, decision="upgrade d to 2",
        guide_url="https://example.com/g", guide_method="model",
        guide_proposal_fetched=True, interpret_flow="m3",
        env_provenance={"before": "built", "after": "built"})
    replay = worker.create_run(
        worker.RunRequest(replay_from="src1", mode="replay"))
    manifest = store.read_json(store.run_dir(replay) / store.MANIFEST)
    assert manifest["env_provenance"] == {"before": "built",
                                          "after": "built"}
    assert manifest["guide_method"] == "model"
    assert manifest["guide_proposal_fetched"] is True
    assert manifest["workspace"] == "workspaces/x__y@abc"
    assert manifest["interpret_flow"] == "m3"
    _live_manifest("src2", status="done")
    legacy = worker.create_run(
        worker.RunRequest(replay_from="src2", mode="replay"))
    assert store.read_json(
        store.run_dir(legacy) / store.MANIFEST)["interpret_flow"] == "legacy"
