"""Background worker pool + run lifecycle.

Two concurrent runs max (env builds are heavy). Each run executes the
stage list on a pool thread; cancel sets an event the gateways honor
(subprocess trees are killed). Terminal states: done | failed |
cancelled. Runs left active by a previous server process are swept to
failed at startup so history never spins forever.
"""

import shutil
import subprocess
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlparse

from chainreaction import llm as llm_mod
from chainreaction import scenarios
from chainreaction.runs import store as store_mod
from chainreaction.runs.context import (
    Cancelled,
    CommitMismatch,
    ReplayMiss,
    RunContext,
)
from chainreaction.runs.scrub import ROOT, utc_now_iso
from chainreaction.runs.stages import STAGES

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cr-run")
_REGISTRY: dict = {}
_REGISTRY_LOCK = threading.Lock()


@dataclass
class RunRequest:
    preset: str | None = None
    mode: str = "live"
    replay_from: str | None = None
    decision: str | None = None
    guide_url: str | None = None
    repo_url: str | None = None
    ref: str | None = None


@dataclass
class _Handle:
    run_id: str
    ctx: RunContext
    cancel_event: threading.Event
    future=None


def _code_sha() -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and sha else "unknown"


def _base_host() -> str:
    try:
        return urlparse(llm_mod.base_url()).hostname or ""
    except Exception:
        return ""


def create_run(req: RunRequest) -> str:
    """Validate a request, write the queued manifest, return the run id."""
    if req.mode not in ("live", "replay"):
        raise ValueError(f"invalid mode: {req.mode!r}")
    run_id = uuid.uuid4().hex
    if req.mode == "replay":
        if req.replay_from and req.preset:
            raise ValueError("pass replay_from or preset, not both")
        if req.replay_from:
            source_dir = store_mod.run_dir(req.replay_from)
            label = req.replay_from
        elif req.preset:
            source_dir = store_mod.preset_dir(req.preset)
            label = f"preset:{req.preset}"
        else:
            raise ValueError("replay needs replay_from or preset")
        manifest_path = source_dir / store_mod.MANIFEST
        if not manifest_path.is_file():
            raise ValueError(f"replay source not found: {label}")
        source = store_mod.read_json(manifest_path)
        if source.get("status") != "done":
            raise ValueError(
                f"cannot replay {label}: status is "
                f"{source.get('status')!r}, need 'done'")
        manifest = {
            "run_id": run_id,
            "preset": source.get("preset"),
            "repo_url": req.repo_url or source.get("repo_url", ""),
            "ref": req.ref or source.get("ref", ""),
            "resolved_commit": source.get("resolved_commit"),
            "decision": req.decision or source.get("decision", ""),
            "guide_url": req.guide_url or source.get("guide_url", ""),
            "mode": "replay",
            "replay_from": label,
            "model": source.get("model", ""),
            "base_url_host": source.get("base_url_host", ""),
            "changeset_source": source.get("changeset_source"),
            "narrative_source": source.get("narrative_source"),
            "chainreaction_git_sha": _code_sha(),
            "created_at": utc_now_iso(),
            "finished_at": None,
            "status": "queued",
            "current_stage": None,
            "error": None,
        }
    else:
        if not req.preset:
            raise ValueError("M1 runs need preset (generic repos arrive "
                             "in M2)")
        try:
            scen = scenarios.get(req.preset)
        except KeyError:
            raise ValueError(f"unknown preset: {req.preset!r}") from None
        manifest = {
            "run_id": run_id,
            "preset": req.preset,
            "repo_url": req.repo_url or scen.repo_url,
            "ref": req.ref or scen.ref,
            "resolved_commit": None,  # the setup stage resolves it
            "decision": req.decision or scen.decision,
            "guide_url": req.guide_url or scen.guide_url,
            "mode": "live",
            "replay_from": None,
            "model": llm_mod.model_name(),
            "base_url_host": _base_host(),
            "changeset_source": None,  # the interpret stage sets it
            "narrative_source": None,  # the report stage sets it
            "chainreaction_git_sha": _code_sha(),
            "created_at": utc_now_iso(),
            "finished_at": None,
            "status": "queued",
            "current_stage": None,
            "error": None,
        }
    store_mod.write_json(store_mod.run_dir(run_id) / store_mod.MANIFEST,
                         manifest)
    return run_id


def get_handle(run_id: str):
    with _REGISTRY_LOCK:
        return _REGISTRY.get(run_id)


def submit_run(run_id: str, stages=None) -> None:
    """Queue a created run on the pool (stages injectable for tests)."""
    manifest = store_mod.read_json(
        store_mod.run_dir(run_id) / store_mod.MANIFEST)
    cancel_event = threading.Event()
    source_dir = None
    if manifest.get("mode") == "replay":
        label = manifest.get("replay_from", "")
        if label.startswith("preset:"):
            source_dir = store_mod.preset_dir(label.split(":", 1)[1])
        else:
            source_dir = store_mod.run_dir(label)
    ctx = RunContext(run_id=run_id, manifest=manifest,
                     mode=manifest.get("mode", "live"),
                     source_dir=source_dir, cancel_event=cancel_event)
    handle = _Handle(run_id, ctx, cancel_event)
    with _REGISTRY_LOCK:
        _REGISTRY[run_id] = handle
    handle.future = _EXECUTOR.submit(_execute, run_id, stages or STAGES)


def _copy_recordings(ctx: RunContext) -> None:
    # Every run dir stays self-contained (replay-of-replay works) and
    # byte-preserving copies keep the provenance chain auditable.
    for name in (store_mod.LLM_CALLS, store_mod.CMDS, store_mod.FETCHES):
        src = ctx.source_dir / name
        if src.is_file():
            shutil.copy(src, ctx.dir / name)


def _warn_on_code_drift(ctx: RunContext) -> None:
    try:
        source = store_mod.read_json(
            ctx.source_dir / store_mod.MANIFEST)
    except (ValueError, OSError):
        return
    recorded = source.get("chainreaction_git_sha")
    current = ctx.manifest.get("chainreaction_git_sha")
    if recorded and current and recorded != current:
        ctx.emit("log", {"message":
                         f"warning: executing code {current[:12]} differs "
                         f"from the recording ({recorded[:12]}); replay "
                         "may diverge"})


def _execute(run_id: str, stages) -> None:
    handle = get_handle(run_id)
    ctx = handle.ctx
    try:
        if ctx.mode == "replay":
            _copy_recordings(ctx)
            _warn_on_code_drift(ctx)
        ctx.set_manifest(status="running")
        for name, fn in stages:
            ctx.check_cancelled()
            ctx.stage = name
            ctx.set_manifest(current_stage=name)
            ctx.emit("stage_start", {"stage": name})
            try:
                fn(ctx)
            except (ReplayMiss, CommitMismatch) as exc:
                ctx.emit("error", {"stage": name, "error":
                                   f"{type(exc).__name__}: {exc}"})
                raise
            except Cancelled:
                raise
            except Exception as exc:
                ctx.emit("error", {"stage": name, "error":
                                   f"{type(exc).__name__}: {exc}",
                                   "traceback": traceback.format_exc(
                                       limit=8)})
                raise
            ctx.emit("stage_end", {"stage": name})
        if ctx.mode == "replay":
            warnings = ctx.replay_warnings()
            for warning in warnings:
                ctx.emit("log", {"warning": warning})
            if warnings:
                ctx.set_manifest(replay_warnings=warnings)
        # The final log precedes the terminal status: a terminal
        # manifest guarantees the event file is complete (SSE relies on
        # this to drain exactly once before closing).
        ctx.emit("log", {"message": f"run done ({ctx.mode})"})
        ctx.set_manifest(status="done", finished_at=utc_now_iso(),
                         current_stage=None, error=None)
    except Cancelled as exc:
        ctx.emit("log", {"message": "run cancelled"})
        ctx.set_manifest(status="cancelled", finished_at=utc_now_iso(),
                         error=str(exc))
    except Exception as exc:  # noqa: BLE001 - terminal failure path
        ctx.emit("log", {"message": f"run failed: {exc}"})
        if ctx.manifest.get("status") not in store_mod.TERMINAL:
            ctx.set_manifest(status="failed", finished_at=utc_now_iso(),
                             error=f"{type(exc).__name__}: {exc}")


def cancel_run(run_id: str) -> str:
    """Request cancellation; returns the run's current status."""
    manifest_path = store_mod.run_dir(run_id) / store_mod.MANIFEST
    if not manifest_path.is_file():
        raise KeyError(run_id)
    manifest = store_mod.read_json(manifest_path)
    if manifest.get("status") in store_mod.TERMINAL:
        return manifest["status"]
    handle = get_handle(run_id)
    if handle is None:
        # Never submitted in this process (or registry dropped):
        # cancel directly so it never runs.
        manifest.update(status="cancelled", finished_at=utc_now_iso(),
                        error="cancelled before start")
        store_mod.write_json(manifest_path, manifest)
        return "cancelled"
    handle.cancel_event.set()
    return store_mod.read_json(manifest_path).get("status", "running")


def startup_sweep() -> list:
    """Fail runs left active by a previous server process."""
    swept = []
    for manifest in store_mod.list_runs():
        if manifest.get("status") not in store_mod.ACTIVE:
            continue
        run_id = manifest["run_id"]
        manifest.update(status="failed", finished_at=utc_now_iso(),
                        current_stage=None,
                        error="server restarted")
        directory = store_mod.run_dir(run_id)
        store_mod.write_json(directory / store_mod.MANIFEST, manifest)
        events = store_mod.read_jsonl(directory / store_mod.EVENTS)
        seq = max((e.get("seq", 0) for e in events), default=0) + 1
        store_mod.append_jsonl(directory / store_mod.EVENTS, {
            "seq": seq, "ts": utc_now_iso(), "run_id": run_id,
            "stage": manifest.get("current_stage") or "setup",
            "type": "log",
            "data": {"message": "server restarted; run marked failed"},
        })
        swept.append(run_id)
    return swept
