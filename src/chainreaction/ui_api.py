"""UI-facing API endpoints (M6 §11), kept in one router so web.py stays
a thin composition root.

All read endpoints work in replay with no key. Responses stay tolerant
of recordings made before a field existed: the UI hides sections whose
data is absent rather than crashing (M6 plan point 3).
"""

from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException

from chainreaction import __version__, llm as llm_mod
from chainreaction.runs import store as run_store
from chainreaction.runs.scrub import ROOT, Scrubber

router = APIRouter()

# Read-only source views are confined to analyzed inputs: preset targets
# under targets/ and generic workspaces under workspaces/.
SOURCE_ROOTS = ("targets", "workspaces")


def run_or_preset_dir(run_id: str) -> Path:
    """Run dir for a run id, preset dir for 'preset:<id>' (landing and
    feature pages render preset data without starting a run)."""
    if run_id.startswith("preset:"):
        directory = run_store.preset_dir(run_id.split(":", 1)[1])
    else:
        directory = run_store.run_dir(run_id)
    if not (directory / run_store.MANIFEST).is_file():
        raise ValueError(f"unknown run: {run_id}")
    return directory


@router.get("/api/status")
def ui_status() -> dict:
    """Key presence, live-mode availability, and build version."""
    key_present = llm_mod.has_key()
    return {
        "key_present": key_present,
        "live_available": key_present,
        "version": __version__,
    }


def _repo_short(repo_url: str, target: str, preset_id: str) -> str:
    if repo_url:
        name = urlparse(repo_url).path.strip("/").rsplit("/", 1)[-1]
        if name:
            return name
    if target:
        return target.strip("/").rsplit("/", 1)[-1]
    return preset_id


def _first_break(spine_evidence: list) -> dict | None:
    """Deepest chain hop: the site the hero code drawing centers on."""
    if not spine_evidence:
        return None
    hop = spine_evidence[-1]
    if not isinstance(hop, (list, tuple)) or len(hop) < 4:
        return None
    return {"from": hop[0], "symbol": hop[1], "path": hop[2], "line": hop[3]}


def _headlines(report: dict | None, fix: dict | None) -> dict | None:
    """Landing-page numbers, all optional-tolerant (M6 plan point 3).

    Sections the backend hasn't produced yet come back as None and the
    UI hides them instead of crashing on missing keys.
    """
    if report is None:
        return None
    evidence = report.get("spine_evidence") or []
    table_size = report.get("interpret_table_size") or 0
    missed = report.get("interpret_missed") or []
    fix_rounds = None
    if isinstance(fix, dict):
        rounds = fix.get("rounds") or []
        fix_rounds = {
            "rounds": len(rounds),
            "status": fix.get("final_status") or fix.get("status"),
        }
    return {
        "hard": len(report.get("hard") or []),
        "at_risk": len(report.get("at_risk") or []),
        "chain": list(report.get("spine") or []),
        "chain_evidence": [list(h) for h in evidence],
        "chain_match": report.get("spine_match"),
        "first_break": _first_break(evidence),
        "probe_kind": report.get("probe_kind"),
        "numpy_version": report.get("numpy_version"),
        "before_numpy": report.get("before_numpy"),
        "change_symbols": list(report.get("change_symbols") or []),
        "interpret": {
            "found": (table_size - len(missed)) if table_size else None,
            "table_size": table_size or None,
            "blind_hits": report.get("interpret_blind_hits"),
            "recall": report.get("interpret_recall"),
        },
        "file_precision": report.get("file_precision"),
        "file_recall": report.get("file_recall"),
        "grep_hits": report.get("grep_hits"),
        "ruff_hits": report.get("ruff_hits"),
        "fix": fix_rounds,
        # Populated by Session A (test-failure groups, line-level test
        # prediction, vendor call chains); absent until then.
        "test_groups": report.get("test_groups"),
        "test_prediction": report.get("test_prediction"),
        "vendor_chains": report.get("vendor_chains"),
    }


def _preset_entry(preset_id: str) -> dict | None:
    directory = run_store.preset_dir(preset_id)
    manifest_path = directory / run_store.MANIFEST
    if not manifest_path.is_file():
        return None
    manifest = run_store.read_json(manifest_path)
    report_path = directory / run_store.ARTIFACTS / "report.json"
    report = run_store.read_json(report_path) if report_path.is_file() else None
    fix_path = directory / run_store.ARTIFACTS / "fix.json"
    fix = run_store.read_json(fix_path) if fix_path.is_file() else None
    decision = manifest.get("decision") or ""
    repo_url = manifest.get("repo_url") or ""
    target = (report or {}).get("target") or ""
    return {
        "id": preset_id,
        "title": f"{_repo_short(repo_url, target, preset_id)} × {decision}",
        "repo": repo_url,
        "decision": decision,
        "target": target,
        "has_recording": manifest.get("status") == "done" and report is not None,
        "headlines": _headlines(report, fix),
    }


@router.get("/api/presets")
def ui_presets() -> list[dict]:
    """Committed preset recordings with landing-page headlines.

    Lists whatever is on disk (including generic-path recordings like
    anndata-numpy2 that have no scenarios.py entry).
    """
    root = run_store.presets_dir()
    if not root.is_dir():
        return []
    entries = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        try:
            entry = _preset_entry(child.name)
        except (ValueError, OSError):
            continue
        if entry is not None:
            entries.append(entry)
    return entries


def _source_root(directory: Path, manifest: dict) -> Path:
    """Target checkout for a run or preset recording.

    Prefers the run's own record (report.json target), then the generic
    workspace, then the pinned preset target. Confined to targets/ and
    workspaces/; anything else is a 404, never a wider read.
    """
    target = ""
    report_path = directory / run_store.ARTIFACTS / "report.json"
    if report_path.is_file():
        try:
            target = run_store.read_json(report_path).get("target") or ""
        except (ValueError, OSError):
            target = ""
    if not target:
        target = manifest.get("workspace") or ""
    if not target and manifest.get("preset"):
        from chainreaction import scenarios  # noqa: PLC0415 - lazy, cheap

        try:
            target = scenarios.get(manifest["preset"]).target
        except KeyError:
            target = ""
    if not target or ".." in Path(target).parts or Path(target).is_absolute():
        raise HTTPException(404, "target checkout unknown for this run")
    root = (ROOT / target).resolve()
    allowed = [(ROOT / name).resolve() for name in SOURCE_ROOTS]
    if not any(root == base or base in root.parents for base in allowed):
        raise HTTPException(404, "target checkout unknown for this run")
    if not root.is_dir():
        raise HTTPException(
            404, f"target not on disk: {target} "
            "(run scripts/fetch_targets.py)")
    return root


@router.get("/api/runs/{run_id}/source")
def ui_source(run_id: str, path: str, start: int = 1,
              end: int | None = None) -> dict:
    """Scrubbed source lines around a site, read-only.

    `run_id` accepts a run id or `preset:<id>`. Lines are 1-based
    inclusive, capped at 200 per request.
    """
    try:
        directory = run_or_preset_dir(run_id)
    except ValueError:
        raise HTTPException(404, f"unknown run: {run_id}") from None
    manifest = run_store.read_json(directory / run_store.MANIFEST)
    if start < 1 or (end is not None and end < start):
        raise HTTPException(400, f"invalid range: start={start} end={end}")
    if end is None:
        end = start + 40
    truncated = False
    if end - start + 1 > 200:
        end = start + 199
        truncated = True
    if not path or ".." in Path(path).parts or Path(path).is_absolute():
        raise HTTPException(400, f"invalid path: {path}")
    if not path.endswith(".py"):
        raise HTTPException(400, "only .py source is served")
    root = _source_root(directory, manifest)
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(400, f"invalid path: {path}")
    if not target.is_file():
        raise HTTPException(404, f"unknown file: {path}")
    try:
        text = Scrubber.default().text(
            target.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise HTTPException(400, f"not readable text: {path}") from None
    lines = text.splitlines()
    total = len(lines)
    window = lines[start - 1:end]
    return {
        "path": path,
        "start": start,
        "end": start + len(window) - 1 if window else start,
        "total_lines": total,
        "truncated": truncated,
        "lines": [{"no": no, "text": text}
                  for no, text in enumerate(window, start=start)],
    }
