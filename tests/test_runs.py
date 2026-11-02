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
    assert "<home>" in rec["stderr"] or "<root>" in rec["stderr"]


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
    assert "<env:numpy2>/lib/site-packages/numpy/x.py" in clean
    assert '{"json": "a\\"b"}' in clean  # escapes outside paths survive


def test_interpret_falls_back_without_key(tmp_runs, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    manifest = _live_manifest("u1")
    ctx = RunContext(run_id="u1", manifest=manifest, mode="live")
    ctx.state["scenario"] = scenarios.get("nibabel-numpy2")
    stages_mod.stage_interpret(ctx)
    assert ctx.manifest["changeset_source"] == "hand-recorded"
    assert (ctx.dir / "artifacts" / "changeset.json").is_file()
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
                       response_format=None):
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


def test_preset_replay_matches_committed(monkeypatch, tmp_path):
    monkeypatch.delenv("CHAINREACTION_RUNS_DIR", raising=False)
    preset, source = _real_preset()
    target = ROOT / "targets" / "nibabel"
    if not (target / ".git").is_dir():
        pytest.skip("nibabel target not fetched")
    try:
        proc = subprocess.run(["git", "-C", str(target), "rev-parse",
                               "HEAD"], capture_output=True, text=True,
                              timeout=30)
    except OSError:
        pytest.skip("git unavailable")
    if proc.stdout.strip() != (source.get("resolved_commit") or ""):
        pytest.skip("nibabel checkout differs from the recorded commit")
    # Acceptance #2 shape: no key, model + guide endpoints unreachable.
    def _no_network(*a, **k):
        raise AssertionError("replay must not touch the network")

    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr(llm_mod, "client",
                        lambda: _no_network())
    monkeypatch.setattr("chainreaction.interpret.fetch_page", _no_network)
    monkeypatch.setattr("chainreaction.runs.context.fetch_page", _no_network)
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(tmp_path / "runs"))
    dest = store.preset_dir("nibabel-numpy2")
    dest.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copytree(preset, dest)
    replay = worker.create_run(
        worker.RunRequest(preset="nibabel-numpy2", mode="replay"))
    worker.submit_run(replay)
    manifest = _wait_done(replay, timeout=900)
    assert manifest["status"] == "done", manifest.get("error")
    live_report = (dest / "artifacts" / "report.json").read_bytes()
    replay_report = (store.run_dir(replay) / "artifacts"
                     / "report.json").read_bytes()
    assert live_report == replay_report
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
