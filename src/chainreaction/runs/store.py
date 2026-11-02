"""Run store: runs/<id>/ layout, manifest, JSONL records, artifacts.

```
runs/<run_id>/
  manifest.json      # provenance + status (see worker.create_run)
  events.jsonl       # scrubbed event stream (seq, ts, run_id, stage, type, data)
  llm_calls.jsonl    # recorded model calls (prompt_hash, model, full prompt,
                     # raw response, usage, cached flag)
  cmds.jsonl         # recorded subprocesses (cmd, cwd-relative, env_name,
                     # returncode, scrubbed stdout/stderr)
  fetches.jsonl      # recorded URL fetches (url, status, content hash, text)
  artifacts/         # changeset.json, prediction.json, verify/*.log,
                     # report.json, report.md
runs/presets/<preset_id>/   # committed recordings (same layout)
```

All writes are scrubbed + LF + canonical JSON at this boundary, so no
caller can leak a key or an absolute path into a run file.
"""

import json
import os
import re
import time
from pathlib import Path

from chainreaction.runs.scrub import ROOT, Scrubber, canonical_json, canonical_line

MANIFEST = "manifest.json"
EVENTS = "events.jsonl"
LLM_CALLS = "llm_calls.jsonl"
CMDS = "cmds.jsonl"
FETCHES = "fetches.jsonl"
ARTIFACTS = "artifacts"

TERMINAL = ("done", "failed", "cancelled")
ACTIVE = ("queued", "running")

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def runs_dir() -> Path:
    override = os.environ.get("CHAINREACTION_RUNS_DIR")
    return Path(override) if override else ROOT / "runs"


def presets_dir() -> Path:
    return runs_dir() / "presets"


def check_id(value: str, what: str = "run id") -> str:
    if not isinstance(value, str) or not _ID_RE.match(value) or ".." in value:
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def run_dir(run_id: str) -> Path:
    return runs_dir() / check_id(run_id)


def preset_dir(preset_id: str) -> Path:
    return presets_dir() / check_id(preset_id, "preset id")


def _scrubber() -> Scrubber:
    return Scrubber.default()


def write_json(path: Path, obj) -> None:
    """Write canonical JSON atomically (tmp file + rename).

    The rename retries on PermissionError: on Windows it transiently
    fails while another thread (the SSE stream) has the file open. The
    retry is load-bearing — a failed terminal manifest write leaves
    subscribers hanging forever.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = canonical_json(_scrubber().obj(obj)) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    for attempt in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.02 * min(attempt + 1, 10))
    os.replace(tmp, path)  # last attempt raises (e.g. disk full)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = canonical_line(_scrubber().obj(obj)) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line)


def read_jsonl(path: Path) -> list:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def read_events_after(path: Path, after_seq: int) -> list:
    """Events with seq > after_seq; tolerates a partially-written tail."""
    if not path.is_file():
        return []
    out = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            if i == len(lines) - 1:
                break  # writer mid-append; the next poll retries
            continue
        if event.get("seq", 0) > after_seq:
            out.append(event)
    return out


def save_artifact(directory: Path, name: str, obj) -> Path:
    """Write name (subpaths allowed) under directory/artifacts/."""
    if not name or ".." in Path(name).parts or Path(name).is_absolute():
        raise ValueError(f"invalid artifact name: {name!r}")
    base = directory / ARTIFACTS
    target = base / name
    resolved = target.resolve()
    if resolved != base.resolve() and base.resolve() not in resolved.parents:
        raise ValueError(f"invalid artifact name: {name!r}")
    if isinstance(obj, str):
        text = _scrubber().text(obj)
        if not text.endswith("\n"):
            text += "\n"
    else:
        text = canonical_json(_scrubber().obj(obj)) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return target


def scan_for_leaks(directory: Path) -> list:
    """Suspicious content for committed runs: keys, abs paths, CR bytes."""
    needles = []
    key = os.environ.get("MODEL_API_KEY", "")
    if key:
        needles.append((key, "MODEL_API_KEY value"))
    home = os.path.expanduser("~")
    candidates = [str(ROOT), home]
    envs = ROOT / "envs"
    if envs.is_dir():
        try:
            children = sorted(p for p in envs.iterdir() if p.is_dir())
        except OSError:
            children = []
        for child in children:
            candidates.append(str(child))
    for cand in candidates:
        needles.append((cand, f"absolute path {cand}"))
        alt = cand.replace("\\", "/")
        if alt != cand:
            needles.append((alt, f"absolute path {cand}"))
    issues = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\r" in raw:
            issues.append(f"{path}: CR byte (runs/ must be LF-only)")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            issues.append(f"{path}: not UTF-8")
            continue
        for needle, what in needles:
            if needle and needle in text:
                issues.append(f"{path}: contains {what}")
    return issues


def list_runs() -> list:
    """Newest-first manifests for runs/ (presets excluded)."""
    root = runs_dir()
    if not root.is_dir():
        return []
    manifests = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name == "presets":
            continue
        manifest = child / MANIFEST
        if manifest.is_file():
            try:
                manifests.append(read_json(manifest))
            except (ValueError, OSError):
                continue
    manifests.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return manifests
