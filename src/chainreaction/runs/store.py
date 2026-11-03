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
  searches.jsonl     # recorded web searches (query, citations, spans)
  artifacts/         # changeset.json, prediction.json, verify/*.log,
                     # report.json, report.md
runs/presets/<preset_id>/   # committed recordings (same layout)
```

Large recordings (cmds, fetches, llm calls, testmap) are stored
gzip-compressed in presets (*.gz); live runs always write plain for
cheap appends, and every reader below accepts either form.

All writes are scrubbed + LF + canonical JSON at this boundary, so no
caller can leak a key or an absolute path into a run file.
"""

import gzip
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
SEARCHES = "searches.jsonl"
ARTIFACTS = "artifacts"
TESTMAP = "testmap.json"

# Preset members eligible for compression (never manifests, reports,
# scores, or byte-compared artifacts, which stay human-readable).
GZ_CANDIDATES = (LLM_CALLS, CMDS, FETCHES, SEARCHES,
                 f"{ARTIFACTS}/{TESTMAP}")
GZ_THRESHOLD_BYTES = 1_000_000

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


def resolve_record(path: Path) -> Path:
    """Existing record for path: plain wins, else the .gz twin, else path."""
    if path.is_file():
        return path
    gzipped = path.with_name(path.name + ".gz")
    return gzipped if gzipped.is_file() else path


def read_text_record(path: Path) -> str:
    """Record text, transparently through gzip (missing reads as "")."""
    resolved = resolve_record(path)
    if not resolved.is_file():
        return ""
    if resolved.suffix == ".gz":
        return gzip.decompress(resolved.read_bytes()).decode("utf-8")
    return resolved.read_text(encoding="utf-8")


def read_json(path: Path):
    resolved = resolve_record(path)
    if resolved.suffix == ".gz":
        return json.loads(
            gzip.decompress(resolved.read_bytes()).decode("utf-8"))
    return json.loads(resolved.read_text(encoding="utf-8"))


def append_jsonl(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = canonical_line(_scrubber().obj(obj)) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line)


def read_jsonl(path: Path) -> list:
    rows = []
    for line in read_text_record(path).splitlines():
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


def gzip_file(path: Path) -> Path:
    """Compress path to path.gz deterministically; remove the original."""
    gzipped = path.with_name(path.name + ".gz")
    gzipped.write_bytes(
        gzip.compress(path.read_bytes(), compresslevel=9, mtime=0))
    path.unlink()
    return gzipped


def compress_preset(preset_dir: Path,
                    threshold: int = GZ_THRESHOLD_BYTES) -> list[str]:
    """Gzip large recordings in a preset dir; return compressed names.

    Only GZ_CANDIDATES at or above threshold move; reports, scores,
    manifests, and small files stay plain. Idempotent.
    """
    done = []
    for name in GZ_CANDIDATES:
        plain = preset_dir / name
        if plain.is_file() and plain.stat().st_size >= threshold:
            gzip_file(plain)
            done.append(name + ".gz")
    return done


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
        if path.suffix == ".gz":
            try:
                raw = gzip.decompress(raw)
            except (OSError, EOFError):
                issues.append(f"{path}: not valid gzip")
                continue
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
