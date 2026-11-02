"""RunContext: the only gateway stages use for events, model, subprocesses.

Live mode executes for real and records every call. Replay mode
re-executes the same stages but serves every gateway call from the
source run's recordings — no network, no key — matched in order by
(prompt_hash/cmd/fetch key, occurrence index). Any call without a
matching recording raises ReplayMiss: replay would rather fail loudly
than silently diverge.
"""

import hashlib
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from chainreaction import llm as llm_mod
from chainreaction.interpret import fetch_page, page_to_text
from chainreaction.runs import store as store_mod
from chainreaction.runs.scrub import ROOT, Scrubber, utc_now_iso
from chainreaction.verifier import target_python

ENV_PYTHON = "<env-python>"


class ReplayMiss(Exception):
    """A gateway call has no matching recording; replay stops here."""


class Cancelled(Exception):
    """The run was cancelled (ctx.cancelled was set)."""


class CommitMismatch(Exception):
    """The local target commit differs from the recorded resolved_commit."""


@dataclass
class CmdResult:
    returncode: int | None
    stdout: str
    stderr: str
    duration_ms: int = 0
    timed_out: bool = False
    missing: str | None = None  # executable that could not be started
    versions: dict = field(default_factory=dict)  # env package versions


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    finally:
        try:
            proc.kill()
        except Exception:
            pass


class RunContext:
    def __init__(self, *, run_id: str, manifest: dict, mode: str = "live",
                 source_dir=None, cancel_event=None,
                 live_llm=None, live_fetch=None):
        self.run_id = run_id
        self.mode = mode
        self.manifest = manifest
        self.dir = store_mod.run_dir(run_id)
        self.source_dir = Path(source_dir) if source_dir else None
        self.stage = "queued"
        self.state: dict = {}
        self.scrubber = Scrubber.default()
        self.cancel_event = cancel_event or threading.Event()
        self._live_llm = live_llm  # tests: (system, user, max, fmt) -> ChatResult
        self._live_fetch = live_fetch  # tests: (url, timeout) -> (status, raw)
        self._lock = threading.Lock()
        self._seq = 0
        self.live_children: set = set()  # Popen objects currently running
        self._env_versions: dict = {}
        self._llm_recs: list = []
        self._cmd_recs: list = []
        self._fetch_recs: list = []
        self._llm_keys: list = []
        self._cmd_keys: list = []
        self._fetch_keys: list = []
        self._used: set = set()  # (which, index) consumed by replay
        self._calls: dict = {}  # (which, key) -> occurrences consumed
        if mode == "replay":
            if self.source_dir is None:
                raise ValueError("replay needs source_dir")
            self._llm_recs = store_mod.read_jsonl(
                self.source_dir / store_mod.LLM_CALLS)
            self._cmd_recs = store_mod.read_jsonl(
                self.source_dir / store_mod.CMDS)
            self._fetch_recs = store_mod.read_jsonl(
                self.source_dir / store_mod.FETCHES)
            self._llm_keys = [r.get("prompt_hash") for r in self._llm_recs]
            self._cmd_keys = [(tuple(r.get("cmd", ())),
                               r.get("cwd"), r.get("env_name"))
                              for r in self._cmd_recs]
            self._fetch_keys = [r.get("url") for r in self._fetch_recs]

    # -- run state --

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled("run cancelled")

    def set_manifest(self, **fields) -> None:
        self.manifest.update(fields)
        store_mod.write_json(self.dir / store_mod.MANIFEST, self.manifest)

    def emit(self, type: str, data: dict):
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "ts": utc_now_iso(),
                     "run_id": self.run_id, "stage": self.stage,
                     "type": type, "data": data}
            store_mod.append_jsonl(self.dir / store_mod.EVENTS, event)
            return event

    def save_artifact(self, name: str, obj) -> Path:
        path = store_mod.save_artifact(self.dir, name, obj)
        self.emit("artifact", {"name": name})
        return path

    # -- replay matching --

    def _take(self, which: str, key, describe: str):
        """Consume the next recording for key; raise ReplayMiss when absent."""
        recs = getattr(self, f"_{which}_recs")
        keys = getattr(self, f"_{which}_keys")
        occ = self._calls.get((which, key), 0)
        for i, (rec, cand) in enumerate(zip(recs, keys)):
            if (which, i) in self._used or cand != key:
                continue
            rec_occ = sum(1 for j in range(i) if keys[j] == key)
            if rec_occ == occ:
                self._used.add((which, i))
                self._calls[(which, key)] = occ + 1
                return rec
        raise ReplayMiss(
            f"no recorded {which} call for {describe} "
            f"(occurrence {occ}); the stages diverged from the recording")

    def replay_warnings(self) -> list:
        """Describe recordings no stage consumed (replay only)."""
        if self.mode != "replay":
            return []
        warnings = []
        for which in ("llm", "cmd", "fetch"):
            recs = getattr(self, f"_{which}_recs")
            unused = [rec for i, rec in enumerate(recs)
                      if (which, i) not in self._used]
            if unused:
                warnings.append(
                    f"{len(unused)} unused {which} recording(s); "
                    "the stages diverged from the recording")
        return warnings

    # -- model --

    def llm(self, system: str, user: str, max_tokens: int = 8000,
            response_format=None) -> str:
        """Recorded model call; in replay returns the recorded response."""
        if self.mode == "replay":
            model = self.manifest.get("model") or llm_mod.model_name()
            prompt_hash = llm_mod.cache_key_for(model, system, user,
                                                response_format)
            rec = self._take("llm", prompt_hash,
                             f"prompt_hash={prompt_hash}")
            self.emit("llm_call", {"prompt_hash": prompt_hash,
                                   "model": rec.get("model"),
                                   "cached": rec.get("cached", False),
                                   "replayed": True})
            return rec["response"]
        self.check_cancelled()
        started = utc_now_iso()
        t0 = time.perf_counter()
        if self._live_llm is not None:
            res = self._live_llm(system, user, max_tokens, response_format)
        else:
            res = llm_mod.chat_full(system, user, max_tokens, response_format)
        duration_ms = int((time.perf_counter() - t0) * 1000)
        prompt_hash = llm_mod.cache_key_for(res.model, system, user,
                                            response_format)
        store_mod.append_jsonl(self.dir / store_mod.LLM_CALLS, {
            "prompt_hash": prompt_hash, "model": res.model,
            "params": {"max_tokens": max_tokens,
                       "response_format": response_format},
            "system": system, "user": user, "response": res.content,
            "usage": res.usage, "finish_reason": res.finish_reason,
            "started_at": started, "duration_ms": duration_ms,
            "cached": res.cached, "cache_mtime": res.cache_mtime,
        })
        self.emit("llm_call", {"prompt_hash": prompt_hash,
                               "model": res.model, "cached": res.cached})
        # Return exactly the recorded bytes (scrubbed): live and replay
        # stages see identical inputs.
        return self.scrubber.text(res.content)

    # -- subprocesses --

    def env_python(self, env_name: str) -> str:
        """Target interpreter (live) or a stable placeholder (replay).

        Replay never needs the environments built: test commands come
        back from the recordings.
        """
        if self.mode == "replay":
            return f"<env-python:{env_name}>"
        return target_python(env_name)

    def _cwd_key(self, cwd) -> str:
        text = str(cwd).replace("\\", "/").rstrip("/") or "."
        if os.path.isabs(str(cwd)):
            return self.scrubber.text(text)
        return text

    def _cmd_key(self, cmd: list, env_name) -> tuple:
        # Portable across machines: the env interpreter (an absolute,
        # OS-specific path) matches by role, and relative tool args
        # match with forward slashes.
        parts = [self.scrubber.text(part).replace("\\", "/") for part in cmd]
        if env_name is not None:
            parts[0] = ENV_PYTHON
        return tuple(parts)

    def _exec_cwd(self, cwd) -> Path:
        if os.path.isabs(str(cwd)):
            return Path(cwd)
        return ROOT / str(cwd).replace("\\", "/")

    def _env_versions_for(self, env_name: str) -> dict:
        if env_name in self._env_versions:
            return self._env_versions[env_name]
        versions: dict = {}
        try:
            exe = target_python(env_name)
            proc = subprocess.run(
                [exe, "-m", "pip", "freeze"], capture_output=True, text=True,
                timeout=60, cwd=str(ROOT))
            for line in proc.stdout.splitlines():
                if "==" in line:
                    name, _, ver = line.partition("==")
                    versions[name.strip().lower()] = ver.strip()
        except Exception:
            versions = {}
        self._env_versions[env_name] = versions
        return versions

    def run_cmd(self, cmd, cwd=".", env_name=None, timeout=300) -> CmdResult:
        """Recorded subprocess; in replay returns the recorded result."""
        cmd = [str(part) for part in cmd]
        cwd_rel = self._cwd_key(cwd)
        key_cmd = self._cmd_key(cmd, env_name)
        if self.mode == "replay":
            rec = self._take("cmd", (key_cmd, cwd_rel, env_name),
                             f"cmd={list(key_cmd)} cwd={cwd_rel} "
                             f"env={env_name}")
            self.emit("cmd", {"cmd": rec.get("cmd"), "cwd": cwd_rel,
                               "env_name": env_name,
                               "returncode": rec.get("returncode"),
                               "replayed": True})
            return CmdResult(rec.get("returncode"), rec.get("stdout", ""),
                             rec.get("stderr", ""),
                             rec.get("duration_ms", 0),
                             rec.get("timed_out", False),
                             rec.get("missing"),
                             rec.get("versions", {}))
        self.check_cancelled()
        started = utc_now_iso()
        t0 = time.perf_counter()
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(self._exec_cwd(cwd)),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                start_new_session=(os.name != "nt"))
        except FileNotFoundError:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            store_mod.append_jsonl(self.dir / store_mod.CMDS, {
                "cmd": list(key_cmd), "cmd_display": cmd, "cwd": cwd_rel,
                "env_name": env_name, "versions": {},
                "returncode": None, "stdout": "", "stderr": "",
                "duration_ms": duration_ms, "timed_out": False,
                "missing": cmd[0], "started_at": started,
            })
            self.emit("cmd", {"cmd": list(key_cmd), "cwd": cwd_rel,
                               "env_name": env_name, "missing": cmd[0]})
            return CmdResult(None, "", "", duration_ms, False, cmd[0], {})
        with self._lock:
            self.live_children.add(proc)
        try:
            out, err, timed_out = self._communicate(proc, timeout)
        finally:
            with self._lock:
                self.live_children.discard(proc)
        duration_ms = int((time.perf_counter() - t0) * 1000)
        versions = self._env_versions_for(env_name) if env_name else {}
        store_mod.append_jsonl(self.dir / store_mod.CMDS, {
            "cmd": list(key_cmd), "cmd_display": cmd, "cwd": cwd_rel,
            "env_name": env_name, "versions": versions,
            "returncode": proc.returncode, "stdout": out, "stderr": err,
            "duration_ms": duration_ms, "timed_out": timed_out,
            "missing": None, "started_at": started,
        })
        self.emit("cmd", {"cmd": list(key_cmd), "cwd": cwd_rel,
                           "env_name": env_name,
                           "returncode": proc.returncode,
                           "timed_out": timed_out})
        # Stages see exactly the recorded bytes (scrubbed).
        return CmdResult(proc.returncode, self.scrubber.text(out),
                         self.scrubber.text(err), duration_ms, timed_out,
                         None, versions)

    def _communicate(self, proc, timeout):
        t0 = time.monotonic()
        while True:
            try:
                out, err = proc.communicate(timeout=0.05)
                return out or "", err or "", False
            except subprocess.TimeoutExpired:
                if self.cancel_event.is_set():
                    _kill_tree(proc)
                    out, err = proc.communicate()
                    raise Cancelled("run cancelled")
                if timeout is not None and (time.monotonic() - t0) > timeout:
                    _kill_tree(proc)
                    out, err = proc.communicate()
                    return out or "", err or "", True

    # -- URL fetching --

    def fetch(self, url: str, timeout: int = 60) -> str:
        """Recorded URL fetch; in replay returns the recorded text."""
        if self.mode == "replay":
            rec = self._take("fetch", url, f"url={url}")
            self.emit("fetch", {"url": url, "status": rec.get("status"),
                                 "replayed": True})
            return rec["text"]
        self.check_cancelled()
        started = utc_now_iso()
        t0 = time.perf_counter()
        if self._live_fetch is not None:
            status, raw = self._live_fetch(url, timeout)
        else:
            status, raw = fetch_page(url, timeout)
        duration_ms = int((time.perf_counter() - t0) * 1000)
        text = page_to_text(raw)
        store_mod.append_jsonl(self.dir / store_mod.FETCHES, {
            "url": url, "status": status,
            "content_sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw), "text": text,
            "started_at": started, "duration_ms": duration_ms,
        })
        self.emit("fetch", {"url": url, "status": status, "bytes": len(raw)})
        return self.scrubber.text(text)
