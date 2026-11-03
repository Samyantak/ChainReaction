"""UI-facing API endpoints (M6 §11), kept in one router so web.py stays
a thin composition root.

All read endpoints work in replay with no key. Responses stay tolerant
of recordings made before a field existed: the UI hides sections whose
data is absent rather than crashing (M6 plan point 3).
"""

import json
import re
from functools import lru_cache
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


def _file_stats(report: dict, behavior: set) -> dict | None:
    """Upstream-file overlap split into breaking vs behavior files.

    "5/5 breaking files" with "5/8 including behavior changes" says what
    the report says; a bare recall percentage undersells it.
    """
    pr_files = list(report.get("pr_files") or [])
    if not pr_files:
        return None
    missing = set(report.get("file_missing") or [])
    total = len(pr_files)
    behavior_in_pr = set(pr_files) & set(behavior)
    breaking_total = total - len(behavior_in_pr)
    breaking_found = breaking_total - len(missing - behavior_in_pr)
    pr_url = report.get("pr_url") or ""
    match = re.search(r"/pull/(\d+)", pr_url)
    pr_ref = f"PR #{match.group(1)}" if match else (
        "upstream PR" if pr_url else None)
    return {
        "breaking_found": breaking_found,
        "breaking_total": breaking_total,
        "found": total - len(missing),
        "total": total,
        "pr_ref": pr_ref,
    }


def _suite_stats(scores: dict | None) -> dict | None:
    """After-suite failures vs baseline (generic-path runs only)."""
    if not isinstance(scores, dict):
        return None
    actual_new = scores.get("actual_new")
    if not isinstance(actual_new, list):
        return None
    return {
        "new_failures": len(actual_new),
        "precision": scores.get("test_precision"),
        "recall": scores.get("test_recall"),
    }


def _headlines(report: dict | None, fix: dict | None,
               scores: dict | None = None,
               behavior: set | None = None) -> dict | None:
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
        "files": _file_stats(report, behavior or set()),
        "suite": _suite_stats(scores),
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
    scores_path = directory / run_store.ARTIFACTS / "scores.json"
    scores = run_store.read_json(scores_path) if scores_path.is_file() else None
    decision = manifest.get("decision") or ""
    repo_url = manifest.get("repo_url") or ""
    target = (report or {}).get("target") or ""
    return {
        "id": preset_id,
        "title": _repo_short(repo_url, target, preset_id),
        "repo": repo_url,
        "decision": decision,
        "decision_parsed": _parse_decision(decision),
        "target": target,
        "has_recording": manifest.get("status") == "done" and report is not None,
        "headlines": _headlines(report, fix, scores,
                                _pr_behavior(preset_id, report)),
    }


def _parse_decision(decision: str) -> dict | None:
    """Deterministic package/version parse; None for vendor decisions."""
    from chainreaction.generic import parse_decision  # noqa: PLC0415 - lazy

    parsed = parse_decision(decision)
    if parsed is None:
        return None
    return {"package": parsed.package, "version": parsed.to_version}


def _pr_behavior(preset_id: str, report: dict | None) -> set:
    """Upstream-PR files that are behavior changes, not breakage."""
    from chainreaction import scenarios  # noqa: PLC0415 - lazy, cheap

    try:
        return set(scenarios.get(preset_id).pr_behavior)
    except KeyError:
        pass
    return set((report or {}).get("file_behavior") or [])


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


# A recorded call proves large-context use only past ordinary windows.
CONTEXT_TOKENS_THRESHOLD = 100_000


def _iter_llm_calls(root: Path):
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        calls_path = child / run_store.LLM_CALLS
        if not calls_path.is_file():
            continue
        try:
            text = calls_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _tool_uses_web_search(tools) -> bool:
    if not tools:
        return False
    entries = tools if isinstance(tools, list) else [tools]
    return any("web_search" in json.dumps(entry).lower()
               for entry in entries)


@lru_cache(maxsize=32)
def _scan_meta_usage(root_str: str) -> list:
    """Meta capabilities evidenced by preset recordings.

    Each line appears only once a recording proves it, so the landing
    page never claims tooling that hasn't landed yet. Cached per runs
    dir; recordings are static within a server process lifetime for the
    committed presets.
    """
    structured, schemas = 0, set()
    tool_calls = web_search = 0
    max_prompt = 0
    for call in _iter_llm_calls(Path(root_str)):
        params = call.get("params") or {}
        fmt = params.get("response_format")
        if fmt:
            structured += 1
            name = (fmt.get("json_schema") or {}).get("name") \
                if isinstance(fmt, dict) else None
            schemas.add(name or "json")
        tools = params.get("tools")
        if tools:
            tool_calls += 1
            if _tool_uses_web_search(tools):
                web_search += 1
        prompt = (call.get("usage") or {}).get("prompt_tokens") or 0
        max_prompt = max(max_prompt, prompt)
    capabilities = []
    if structured:
        capabilities.append({
            "id": "structured-output",
            "label": "Structured output",
            "where": "change sets, validated against a strict schema",
            "evidence": (f"{structured} recorded calls "
                         f"({', '.join(sorted(schemas))})"),
        })
    if tool_calls:
        capabilities.append({
            "id": "tool-calling",
            "label": "Tool calling",
            "where": "the investigator agent's deterministic tools",
            "evidence": f"{tool_calls} recorded calls",
        })
    if web_search:
        capabilities.append({
            "id": "search-grounding",
            "label": "Search grounding",
            "where": "finds and cites the migration guide",
            "evidence": f"{web_search} recorded calls",
        })
    if max_prompt >= CONTEXT_TOKENS_THRESHOLD:
        capabilities.append({
            "id": "context-1m",
            "label": "1M context",
            "where": "whole-repo baselines and full-guide reads",
            "evidence": f"{max_prompt:,} prompt tokens in one call",
        })
    capabilities.append({
        "id": "muse-code",
        "label": "Muse Code",
        "where": "built ChainReaction during the hackathon",
        "evidence": None,
    })
    return capabilities


@router.get("/api/meta-usage")
def ui_meta_usage() -> dict:
    """Meta tooling evidenced by preset recordings (landing band)."""
    return {"capabilities": _scan_meta_usage(str(run_store.presets_dir()))}


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
