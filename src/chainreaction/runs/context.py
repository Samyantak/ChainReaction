"""RunContext: the only gateway stages use for events, model, subprocesses.

Live mode executes for real and records every call. Replay mode
re-executes the same stages but serves every gateway call from the
source run's recordings — no network, no key — matched in order by
(prompt_hash/cmd/fetch key, occurrence index). Any call without a
matching recording raises ReplayMiss: replay would rather fail loudly
than silently diverge.
"""

import builtins
import hashlib
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path

from chainreaction import llm as llm_mod
from chainreaction.baselines import resolve_tool
from chainreaction.interpret import (
    FetchedText,
    decode_page,
    extract_guide_tables,
    fetch_page,
    html_to_text,
)
from chainreaction.runs import store as store_mod
from chainreaction.runs.scrub import ROOT, Scrubber, utc_now_iso
from chainreaction.verifier import target_python

ENV_PYTHON = "<env-python>"


def canon_cmd_key(cmd) -> tuple:
    """Match key for a command: --tb flavor is not identity.

    Traceback verbosity is a record-time choice, not part of what a
    command IS: --tb=no (recordings from before short-traceback
    diagnostics) and --tb=short (current builders) match each other.
    The stored argv keeps whatever actually ran.
    """
    return tuple("--tb" if str(p).startswith("--tb=") else p for p in cmd)

# M2 §8: target code never sees MODEL_API_KEY or any other token.
# run_cmd passes only this allowlist (lookups, home/cache dirs, temp
# dirs, locale). Everything else — keys, tokens, editor sessions,
# CI variables — stays outside the subprocess.
_SANITIZED_ENV_KEYS = (
    "PATH", "PATHEXT", "OS",
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "TEMP", "TMP", "TMPDIR",
    "SYSTEMROOT", "COMSPEC", "WINDIR",
    "LANG", "LC_ALL", "LC_CTYPE", "LANGUAGE", "TZ",
    "PYTHONUTF8", "PYTHONIOENCODING",
)


def sanitized_env() -> dict:
    """Allowlisted environment for every run_cmd subprocess."""
    return {k: v for k, v in os.environ.items() if k in _SANITIZED_ENV_KEYS}


class ReplayMiss(Exception):
    """A gateway call has no matching recording; replay stops here."""


class Cancelled(Exception):
    """The run was cancelled (ctx.cancelled was set)."""


class CommitMismatch(Exception):
    """The local target commit differs from the recorded resolved_commit."""


def _replay_error(class_name: str, message: str) -> Exception:
    """Rebuild a recorded model failure for replay.

    Builtin exception types are restored exactly (callers treat
    TimeoutError separately from other model errors); anything else
    keeps its recorded class name on a RuntimeError subclass so
    retry notes match the live run byte for byte.
    """
    builtin = getattr(builtins, class_name, None)
    if isinstance(builtin, type) and issubclass(builtin, Exception):
        try:
            return builtin(message)
        except Exception:
            pass
    return type(class_name, (RuntimeError,), {})(message)


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
                 live_llm=None, live_fetch=None, live_search=None):
        self.run_id = run_id
        self.mode = mode
        self.manifest = manifest
        self.dir = store_mod.run_dir(run_id)
        self.source_dir = Path(source_dir) if source_dir else None
        self.stage = "queued"
        self.state: dict = {}
        self.scrubber = Scrubber.default()
        self.cancel_event = cancel_event or threading.Event()
        # tests: (system, user, max, fmt) -> ChatResult, or the 6-arg
        # chat_turn form (system, user, max, fmt, tools, tool_choice).
        self._live_llm = live_llm
        self._live_fetch = live_fetch  # tests: (url, timeout) -> (status, raw)
        self._live_search = live_search  # tests: (query,) -> WebSearchResult
        self._lock = threading.Lock()
        self._seq = 0
        self.live_children: set = set()  # Popen objects currently running
        self._env_versions: dict = {}
        self._llm_recs: list = []
        self._cmd_recs: list = []
        self._fetch_recs: list = []
        self._search_recs: list = []
        self._llm_keys: list = []
        self._cmd_keys: list = []
        self._fetch_keys: list = []
        self._search_keys: list = []
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
            self._search_recs = store_mod.read_jsonl(
                self.source_dir / store_mod.SEARCHES)
            self._llm_keys = [r.get("prompt_hash") for r in self._llm_recs]
            self._cmd_keys = [(canon_cmd_key(r.get("cmd", ())),
                               r.get("cwd"), r.get("env_name"))
                              for r in self._cmd_recs]
            self._fetch_keys = [r.get("url") for r in self._fetch_recs]
            self._search_keys = [r.get("query") for r in self._search_recs]

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
        for which in ("llm", "cmd", "fetch", "search"):
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
            response_format=None, reasoning_effort=None) -> str:
        """Recorded model call; in replay returns the recorded response.

        Length-truncated live calls retry once with +50% max tokens;
        both attempts are recorded, so replay follows the same path.
        Failed live calls (empty reply, outage) are recorded as
        tombstones; replay re-raises the same failure instead of
        diverging past a hole in the recording.
        """
        if self.mode == "replay":
            model = self.manifest.get("model") or llm_mod.model_name()
            prompt_hash = llm_mod.cache_key_for(model, system, user,
                                                response_format,
                                                reasoning_effort)
            rec = self._take("llm", prompt_hash,
                             f"prompt_hash={prompt_hash}")
            if rec.get("finish_reason") == "length" \
                    and not rec.get("cached", False):
                # The live run retried: consume the retry's recording.
                rec = self._take("llm", prompt_hash,
                                 f"prompt_hash={prompt_hash}")
            if rec.get("error_class") is not None:
                self.emit("llm_call", {"prompt_hash": prompt_hash,
                                       "model": rec.get("model"),
                                       "error": rec.get("error_class"),
                                       "replayed": True})
                raise _replay_error(rec["error_class"],
                                    rec.get("error") or "")
            self.emit("llm_call", {"prompt_hash": prompt_hash,
                                   "model": rec.get("model"),
                                   "cached": rec.get("cached", False),
                                   "replayed": True})
            return rec["response"]
        res = self._llm_live(system, user, max_tokens, response_format,
                            reasoning_effort)
        # Return exactly the recorded bytes (scrubbed): live and replay
        # stages see identical inputs.
        return self.scrubber.text(res.content)

    def _llm_live(self, system: str, user: str, max_tokens: int,
                  response_format, reasoning_effort=None,
                  retried: bool = False):
        self.check_cancelled()
        started = utc_now_iso()
        t0 = time.perf_counter()
        try:
            if self._live_llm is not None:
                res = self._live_llm(system, user, max_tokens,
                                     response_format)
            else:
                res = llm_mod.chat_full(system, user, max_tokens,
                                        response_format, reasoning_effort)
        except Cancelled:
            raise
        except Exception as exc:
            # A failed model call still advances the caller's retry
            # state (its feedback shapes the next prompt), so the
            # failure itself must be recorded: replay re-raises from
            # the tombstone instead of missing the next prompt.
            duration_ms = int((time.perf_counter() - t0) * 1000)
            model = llm_mod.model_name()
            prompt_hash = llm_mod.cache_key_for(model, system, user,
                                                response_format,
                                                reasoning_effort)
            store_mod.append_jsonl(self.dir / store_mod.LLM_CALLS, {
                "prompt_hash": prompt_hash, "model": model,
                "params": {"max_tokens": max_tokens,
                           "response_format": response_format,
                           "reasoning_effort": reasoning_effort},
                "system": system, "user": user, "response": None,
                "error_class": exc.__class__.__name__,
                "error": str(exc),
                "usage": None, "finish_reason": None,
                "started_at": started, "duration_ms": duration_ms,
                "cached": False, "cache_mtime": None,
            })
            self.emit("llm_call", {"prompt_hash": prompt_hash,
                                   "model": model,
                                   "error": exc.__class__.__name__,
                                   "retried": retried})
            raise
        duration_ms = int((time.perf_counter() - t0) * 1000)
        prompt_hash = llm_mod.cache_key_for(res.model, system, user,
                                            response_format,
                                            reasoning_effort)
        store_mod.append_jsonl(self.dir / store_mod.LLM_CALLS, {
            "prompt_hash": prompt_hash, "model": res.model,
            "params": {"max_tokens": max_tokens,
                       "response_format": response_format,
                       "reasoning_effort": reasoning_effort},
            "system": system, "user": user, "response": res.content,
            "usage": res.usage, "finish_reason": res.finish_reason,
            "started_at": started, "duration_ms": duration_ms,
            "cached": res.cached, "cache_mtime": res.cache_mtime,
        })
        self.emit("llm_call", {"prompt_hash": prompt_hash,
                               "model": res.model, "cached": res.cached,
                               "retried": retried})
        if res.finish_reason == "length" and not res.cached \
                and not retried:
            self.emit("log", {"message":
                              "llm: length finish, retrying with "
                              f"{int(max_tokens * 1.5)} max tokens"})
            return self._llm_live(system, user, int(max_tokens * 1.5),
                                  response_format, reasoning_effort,
                                  retried=True)
        return res

    def chat_turn(self, system: str, user: str,
                  max_tokens: int = 8000, response_format=None,
                  reasoning_effort=None, tools=None,
                  tool_choice=None):
        """One recorded tool-calling turn; replay serves the recorded turn.

        Unlike llm() there is no length retry: the caller owns the
        conversation and decides what a truncation means (usually a
        nudge turn). Tombstones behave as in llm(): a failed live turn
        is recorded with response null, and replay re-raises it.
        """
        if self.mode == "replay":
            model = self.manifest.get("model") or llm_mod.model_name()
            prompt_hash = llm_mod.cache_key_for(model, system, user,
                                                response_format,
                                                reasoning_effort, tools,
                                                tool_choice)
            rec = self._take("llm", prompt_hash,
                             f"prompt_hash={prompt_hash}")
            if rec.get("error_class") is not None:
                self.emit("llm_call", {"prompt_hash": prompt_hash,
                                       "model": rec.get("model"),
                                       "error": rec.get("error_class"),
                                       "replayed": True})
                raise _replay_error(rec["error_class"],
                                    rec.get("error") or "")
            self.emit("llm_call", {"prompt_hash": prompt_hash,
                                   "model": rec.get("model"),
                                   "cached": rec.get("cached", False),
                                   "tool_calls": len(rec.get("tool_calls")
                                                     or []),
                                   "replayed": True})
            return llm_mod.ChatResult(
                rec["response"], rec.get("model"), rec.get("usage"),
                rec.get("finish_reason"), True, rec.get("cache_mtime"),
                tool_calls=rec.get("tool_calls"))
        res = self._chat_turn_live(system, user, max_tokens,
                                   response_format, reasoning_effort,
                                   tools, tool_choice)
        # Return exactly the recorded bytes (scrubbed): live and replay
        # stages see identical inputs. Tool-call arguments pass through
        # unscrubbed: the loop executes them, and replay serves the
        # same recorded arguments back.
        return llm_mod.ChatResult(
            self.scrubber.text(res.content), res.model, res.usage,
            res.finish_reason, res.cached, res.cache_mtime,
            tool_calls=res.tool_calls)

    def _chat_turn_live(self, system: str, user: str, max_tokens: int,
                        response_format, reasoning_effort, tools,
                        tool_choice):
        self.check_cancelled()
        started = utc_now_iso()
        t0 = time.perf_counter()
        try:
            if self._live_llm is not None:
                res = self._live_llm(system, user, max_tokens,
                                     response_format, tools, tool_choice)
            else:
                res = llm_mod.chat_full(system, user, max_tokens,
                                        response_format, reasoning_effort,
                                        tools, tool_choice)
        except Cancelled:
            raise
        except Exception as exc:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            model = llm_mod.model_name()
            prompt_hash = llm_mod.cache_key_for(model, system, user,
                                                response_format,
                                                reasoning_effort, tools,
                                                tool_choice)
            store_mod.append_jsonl(self.dir / store_mod.LLM_CALLS, {
                "prompt_hash": prompt_hash, "model": model,
                "params": {"max_tokens": max_tokens,
                           "response_format": response_format,
                           "reasoning_effort": reasoning_effort,
                           "tools": tools, "tool_choice": tool_choice},
                "system": system, "user": user, "response": None,
                "tool_calls": None,
                "error_class": exc.__class__.__name__,
                "error": str(exc),
                "usage": None, "finish_reason": None,
                "started_at": started, "duration_ms": duration_ms,
                "cached": False, "cache_mtime": None,
            })
            self.emit("llm_call", {"prompt_hash": prompt_hash,
                                   "model": model,
                                   "error": exc.__class__.__name__})
            raise
        duration_ms = int((time.perf_counter() - t0) * 1000)
        prompt_hash = llm_mod.cache_key_for(res.model, system, user,
                                            response_format,
                                            reasoning_effort, tools,
                                            tool_choice)
        store_mod.append_jsonl(self.dir / store_mod.LLM_CALLS, {
            "prompt_hash": prompt_hash, "model": res.model,
            "params": {"max_tokens": max_tokens,
                       "response_format": response_format,
                       "reasoning_effort": reasoning_effort,
                       "tools": tools, "tool_choice": tool_choice},
            "system": system, "user": user, "response": res.content,
            "tool_calls": res.tool_calls,
            "usage": res.usage, "finish_reason": res.finish_reason,
            "started_at": started, "duration_ms": duration_ms,
            "cached": res.cached, "cache_mtime": res.cache_mtime,
        })
        self.emit("llm_call", {"prompt_hash": prompt_hash,
                               "model": res.model, "cached": res.cached,
                               "tool_calls": len(res.tool_calls or [])})
        return res

    def web_search(self, query: str):
        """Recorded web search; in replay returns the recorded citations.

        Live calls go through the Responses API web_search tool; the
        query, reply text, and every url_citation (URL + cited span)
        are recorded so replay reproduces the same sources offline.
        """
        if self.mode == "replay":
            rec = self._take("search", query, f"query={query!r}")
            self.emit("search", {"query": query,
                                 "citations": len(rec.get("citations", [])),
                                 "replayed": True})
            res = llm_mod.WebSearchResult(
                rec.get("text", ""),
                [llm_mod.WebCitation(c.get("url", ""),
                                     c.get("title", ""), c.get("span", ""))
                 for c in rec.get("citations", [])],
                rec.get("searches", 0), rec.get("model", ""),
                rec.get("usage"), rec.get("cached", False), None)
            return self._scrub_search(res)
        self.check_cancelled()
        started = utc_now_iso()
        t0 = time.perf_counter()
        if self._live_search is not None:
            res = self._live_search(query)
        else:
            res = llm_mod.web_search(query)
        duration_ms = int((time.perf_counter() - t0) * 1000)
        store_mod.append_jsonl(self.dir / store_mod.SEARCHES, {
            "query": query, "model": res.model, "text": res.text,
            "citations": [{"url": c.url, "title": c.title, "span": c.span}
                          for c in res.citations],
            "searches": res.searches, "usage": res.usage,
            "started_at": started, "duration_ms": duration_ms,
            "cached": res.cached,
        })
        self.emit("search", {"query": query,
                             "citations": len(res.citations),
                             "searches": res.searches})
        return self._scrub_search(res)

    def _scrub_search(self, res):
        scrub = self.scrubber.text
        return llm_mod.WebSearchResult(
            scrub(res.text),
            [llm_mod.WebCitation(scrub(c.url), scrub(c.title),
                                 scrub(c.span)) for c in res.citations],
            res.searches, res.model, res.usage, res.cached,
            res.cache_mtime)

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

    def _cmd_key(self, cmd: list, env_name, tool=None) -> tuple:
        # Portable across machines: the env interpreter (an absolute,
        # OS-specific path) matches by role, PATH tools match by logical
        # name (<tool:ruff>), and relative args match with forward slashes.
        # M2: env paths in ANY arg position (uv --python <abs>) match by
        # role too — live records <env:before>/..., replay passes
        # <env-python:...>. Preset commands carry no such parts, so their
        # keys are unchanged.
        parts = [self.scrubber.text(part).replace("\\", "/") for part in cmd]
        if tool is not None:
            parts[0] = f"<tool:{tool}>"
        elif env_name is not None:
            parts[0] = ENV_PYTHON
        return tuple(ENV_PYTHON if (i and part.startswith("<env")) else part
                     for i, part in enumerate(parts))

    def _exec_cwd(self, cwd) -> Path:
        if os.path.isabs(str(cwd)):
            return Path(cwd)
        return ROOT / str(cwd).replace("\\", "/")

    def _env_versions_for(self, env_name: str) -> dict:
        if env_name in self._env_versions:
            return self._env_versions[env_name]
        versions: dict = {}
        try:
            from chainreaction.generic import FREEZE_CODE

            exe = target_python(env_name)
            proc = subprocess.run(
                [exe, "-c", FREEZE_CODE], capture_output=True, text=True,
                timeout=60, cwd=str(ROOT), env=sanitized_env())
            for line in proc.stdout.splitlines():
                if "==" in line:
                    name, _, ver = line.partition("==")
                    versions[name.strip().lower()] = ver.strip()
        except Exception:
            versions = {}
        self._env_versions[env_name] = versions
        return versions

    def run_cmd(self, cmd, cwd=".", env_name=None, timeout=300,
                tool: str | None = None, extra_env: dict | None = None
                ) -> CmdResult:
        """Recorded subprocess; in replay returns the recorded result.

        tool= names a PATH tool ("ruff"): live resolves it to a real
        executable but records under <tool:name>; replay matches the
        logical name and never looks the executable up. extra_env=
        adds caller-controlled variables (e.g. COVERAGE_FILE) atop the
        sanitized allowlist; it is recorded scrubbed and ignored for
        replay matching.
        """
        cmd = [str(part) for part in cmd]
        cwd_rel = self._cwd_key(cwd)
        key_cmd = self._cmd_key(cmd, env_name, tool)
        if self.mode == "replay":
            rec = self._take("cmd",
                             (canon_cmd_key(key_cmd), cwd_rel, env_name),
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
        if tool is not None:
            exe = resolve_tool(tool)
            if exe is None:
                duration_ms = int((time.perf_counter() - t0) * 1000)
                store_mod.append_jsonl(self.dir / store_mod.CMDS, {
                    "cmd": list(key_cmd),
                    "cmd_display": [f"<tool:{tool}>", *cmd[1:]],
                    "cwd": cwd_rel, "env_name": env_name, "versions": {},
                    "returncode": None, "stdout": "", "stderr": "",
                    "duration_ms": duration_ms, "timed_out": False,
                    "missing": f"<tool:{tool}>", "started_at": started,
                })
                self.emit("cmd", {"cmd": list(key_cmd), "cwd": cwd_rel,
                                   "env_name": env_name,
                                   "missing": f"<tool:{tool}>"})
                return CmdResult(None, "", "", duration_ms, False,
                                 f"<tool:{tool}>", {})
            cmd = [exe, *cmd[1:]]
        try:
            merged = sanitized_env()
            if extra_env:
                merged.update({str(k): str(v)
                               for k, v in extra_env.items()})
            proc = subprocess.Popen(
                cmd, cwd=str(self._exec_cwd(cwd)),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                env=merged,
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
            "extra_env": ({str(k): str(v) for k, v in extra_env.items()}
                          if extra_env else {}),
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

    def fetch(self, url: str, timeout: int = 60,
              clean: str = "html") -> FetchedText:
        """Recorded URL fetch; in replay returns the recorded text.

        clean="html" (default) returns one-line quotable guide text plus
        the guide's structured table rows (FetchedText.removal_table).
        clean="text" records the decoded body verbatim (newlines kept)
        for changelogs and APIs, with an empty table.

        HTTP error statuses are recorded with empty text (not raised)
        so discovery probing replays deterministically; only network
        failures raise. The result carries .status, .bytes, and .sha256
        for sources.json provenance.
        """
        if self.mode == "replay":
            rec = self._take("fetch", url, f"url={url}")
            self.emit("fetch", {"url": url, "status": rec.get("status"),
                                 "replayed": True})
            table = rec.get("removal_table") or {"removed": [],
                                                 "deprecated": []}
            return self._fetched(rec["text"], table, rec.get("status"),
                                 rec.get("bytes", 0),
                                 rec.get("content_sha256", ""))
        self.check_cancelled()
        started = utc_now_iso()
        t0 = time.perf_counter()
        if self._live_fetch is not None:
            status, raw = self._live_fetch(url, timeout)
        else:
            try:
                status, raw = fetch_page(url, timeout)
            except urllib.error.HTTPError as exc:
                status, raw = exc.code, b""
        duration_ms = int((time.perf_counter() - t0) * 1000)
        page = decode_page(raw)
        if clean == "text" or page.lstrip().startswith("{"):
            # JSON APIs (PyPI) and changelogs: keep the body parseable,
            # no HTML cleaning.
            text = page.replace("\r\n", "\n").replace("\r", "\n")
            table = {"removed": [], "deprecated": []}
        else:
            text = html_to_text(page)
            table = self.scrubber.obj(extract_guide_tables(page))
        sha256 = hashlib.sha256(raw).hexdigest()
        store_mod.append_jsonl(self.dir / store_mod.FETCHES, {
            "url": url, "status": status,
            "content_sha256": sha256,
            "bytes": len(raw), "text": text, "removal_table": table,
            "clean": clean,
            "started_at": started, "duration_ms": duration_ms,
        })
        self.emit("fetch", {"url": url, "status": status, "bytes": len(raw),
                             "table_rows": len(table["removed"])
                             + len(table["deprecated"])})
        return self._fetched(self.scrubber.text(text), table, status,
                             len(raw), sha256)

    @staticmethod
    def _fetched(text, table, status, nbytes, sha256) -> FetchedText:
        result = FetchedText(text, table)
        result.status = status
        result.bytes = nbytes
        result.sha256 = sha256
        return result
