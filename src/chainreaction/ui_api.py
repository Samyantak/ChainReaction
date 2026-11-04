"""UI-facing API endpoints (M6 §11), kept in one router so web.py stays
a thin composition root.

All read endpoints work in replay with no key. Responses stay tolerant
of recordings made before a field existed: the UI hides sections whose
data is absent rather than crashing (M6 plan point 3).
"""

import io
import json
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

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


def _pr_ref(pr_url: str) -> str | None:
    match = re.search(r"/pull/(\d+)", pr_url or "")
    if match:
        return f"PR #{match.group(1)}"
    return "upstream PR" if pr_url else None


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
    pr_ref = _pr_ref(report.get("pr_url") or "")
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
        "decision_package": report.get("decision_package") or "",
        "before_version": report.get("before_version"),
        "after_version": report.get("after_version"),
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


class ParseDecisionBody(BaseModel):
    decision: str = ""


@router.post("/api/parse-decision")
def ui_parse_decision(body: ParseDecisionBody) -> dict:
    """Dry-run the decision grammar for the New-analysis preview.

    Returns the parsed package/from/to versions, or nulls when the
    text matches no pattern (a free-form or vendor decision, which the
    model interprets at run time). Never needs a key.
    """
    from chainreaction.generic import parse_decision  # noqa: PLC0415 - lazy

    parsed = parse_decision(body.decision or "")
    if parsed is None:
        return {"package": None, "from_version": "", "to_version": None}
    return {"package": parsed.package, "from_version": parsed.from_version,
            "to_version": parsed.to_version}


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


def _iter_preset_jsonl(root: Path, filename: str):
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        path = child / filename
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _iter_llm_calls(root: Path):
    yield from _iter_preset_jsonl(root, run_store.LLM_CALLS)


def _count_grounded_searches(root: Path) -> int:
    """Recorded search calls that returned citations (search grounding)."""
    return sum(1 for rec in _iter_preset_jsonl(root, run_store.SEARCHES)
               if rec.get("citations"))


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
    web_search += _count_grounded_searches(Path(root_str))
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


GRAPH_FULL_THRESHOLD = 60
GRAPH_MAX_NEIGHBORS = 35


def build_tiles(spine: list, spine_evidence: list) -> list:
    """Domino tiles, break side first, package root last.

    Every tile shows its OWN line: the decision (red) tile the
    first-break site, every other tile its outgoing hop toward the
    break (the import statement in its own file). Missing sites come
    back null and the tile shows the module only.
    """
    if not spine:
        return []
    first = _first_break(spine_evidence or [])
    from_hop = {}
    for hop in spine_evidence or []:
        if (isinstance(hop, (list, tuple)) and len(hop) >= 4
                and hop[0] not in from_hop):
            from_hop[hop[0]] = hop
    tiles = []
    for module in reversed(spine):
        if module == spine[-1]:
            tiles.append({
                "module": module, "role": "decision",
                "file": (first or {}).get("path"),
                "line": (first or {}).get("line"),
                "symbol": (first or {}).get("symbol"),
            })
        else:
            hop = from_hop.get(module)
            tiles.append({
                "module": module,
                "role": "root" if module == spine[0] else "link",
                "file": hop[2] if hop else None,
                "line": hop[3] if hop else None,
            })
    return tiles


def cluster_impacted(modules: dict, edges: list, main: list,
                     max_total: int = GRAPH_FULL_THRESHOLD,
                     max_neighbors: int = GRAPH_MAX_NEIGHBORS) -> dict:
    """Chain + most-connected neighbors; the rest grouped by status.

    Small graphs pass through whole. Large ones show the chain plus
    the most chain-connected first-degree neighbors (HARD first, then
    edge count to the chain, then alphabetical — deterministic), with
    "+N more" clusters for the rest. nibabel lands at 5 + 35 = 40.
    """
    main = [m for m in main if m in modules]
    if len(modules) <= max_total:
        ids = sorted(modules)
        return {
            "nodes": [_node(modules, i, i in main) for i in ids],
            "edges": [e for e in edges
                      if e["source"] in modules and e["target"] in modules],
            "clusters": [],
        }
    main_set = set(main)
    touch: dict[str, int] = {}
    for edge in edges:
        ends = {edge["source"], edge["target"]}
        if len(ends & main_set) != 1:
            continue
        other = (ends - main_set).pop()
        if other in modules:
            touch[other] = touch.get(other, 0) + 1
    neighbors = sorted(
        touch, key=lambda m: (modules[m]["status"] != "HARD",
                              -touch[m], m))
    shown = list(main) + neighbors[:max_neighbors]
    shown_set = set(shown)
    hidden = sorted(set(modules) - shown_set)
    clusters = []
    for status in ("HARD", "AT_RISK"):
        count = sum(1 for m in hidden if modules[m]["status"] == status)
        if count:
            clusters.append({
                "id": f"cluster:{status}", "status": status,
                "count": count, "label": f"+{count} more {status}",
            })
    return {
        "nodes": [_node(modules, i, i in main) for i in shown],
        "edges": [e for e in edges
                  if e["source"] in shown_set and e["target"] in shown_set],
        "clusters": clusters,
    }


def _node(modules: dict, module: str, on_chain: bool) -> dict:
    info = modules[module]
    node = {"id": module, "status": info["status"],
            "file": info.get("file"), "chain": on_chain,
            "behavior": bool(info.get("behavior"))}
    if info.get("tests") is not None:
        node["tests"] = info["tests"]
        node["tests_more"] = info.get("tests_more", 0)
    return node


@lru_cache(maxsize=8)
def cached_repo_graph(target: str, package: str, commit: str | None):
    """AST graph per target checkout (targets are pinned; restart the
    server to pick up a re-checked-out target)."""
    from chainreaction.graph.repo import (  # noqa: PLC0415 - lazy, heavy
        build_graph_from_repo)

    return build_graph_from_repo(ROOT / target, package)


def _covering_tests(directory: Path) -> dict:
    """module -> sorted test ids, from testmap.json when recorded."""
    testmap_path = directory / run_store.ARTIFACTS / "testmap.json"
    if not testmap_path.is_file():
        return {}
    try:
        testmap = run_store.read_json(testmap_path)
    except (ValueError, OSError):
        return {}
    by_module: dict[str, set] = {}
    tests = testmap.get("tests") or {}
    if not isinstance(tests, dict):
        return {}
    for test_id, mods in tests.items():
        for mod in mods or []:
            by_module.setdefault(mod, set()).add(test_id)
    return {mod: sorted(ids) for mod, ids in by_module.items()}


@router.get("/api/runs/{run_id}/graph")
def ui_graph(run_id: str, full: bool = False) -> dict:
    """Domino tiles + impacted module subgraph for the graph view.

    `run_id` accepts a run id or `preset:<id>`; `full=1` skips
    clustering (cluster expansion). 404 while the prediction isn't
    written yet or the target checkout is missing.
    """
    try:
        directory = run_or_preset_dir(run_id)
    except ValueError:
        raise HTTPException(404, f"unknown run: {run_id}") from None
    manifest = run_store.read_json(directory / run_store.MANIFEST)
    pred_path = directory / run_store.ARTIFACTS / "prediction.json"
    if not pred_path.is_file():
        raise HTTPException(404, "prediction not available yet")
    prediction = run_store.read_json(pred_path)
    report_path = directory / run_store.ARTIFACTS / "report.json"
    report = run_store.read_json(report_path) if report_path.is_file() else {}
    package = report.get("package") or manifest.get("package") or ""
    if not package:
        raise HTTPException(404, "prediction not available yet")
    _source_root(directory, manifest)  # 404 unless checked out
    target = _target_string(directory, manifest)
    graph = cached_repo_graph(
        target, package, manifest.get("resolved_commit"))
    hard = set(prediction.get("hard") or [])
    at_risk = set(prediction.get("at_risk") or [])
    spine = list(prediction.get("spine") or [])
    impacted = {n for n in hard | at_risk | set(spine)
                if graph.nodes.get(n, {}).get("kind")
                in ("module", "function", "class")}
    behavior_files = set(report.get("file_behavior") or [])
    modules = {
        n: {"status": "HARD" if n in hard else "AT_RISK",
            "file": graph.nodes[n].get("file"),
            "behavior": graph.nodes[n].get("file") in behavior_files}
        for n in impacted
    }
    edges = [
        {"source": u, "target": v, "kind": d.get("kind"),
         "file": d.get("file"), "line": d.get("line")}
        for u, v, d in graph.edges(data=True)
        if u in modules and v in modules
        and d.get("kind") in ("IMPORTS", "EXECUTES_AT_IMPORT")
    ]
    covering = _covering_tests(directory)
    for module, tests in covering.items():
        if module in modules:
            modules[module]["tests"] = tests[:15]
            modules[module]["tests_more"] = max(0, len(tests) - 15)
    if full:
        clustered = cluster_impacted(modules, edges, spine,
                                     max_total=len(modules))
    else:
        clustered = cluster_impacted(modules, edges, spine)
    return {
        "tiles": build_tiles(spine, prediction.get("spine_evidence") or []),
        "spine": spine,
        **clustered,
        "counts": {"hard": len(hard), "at_risk": len(at_risk),
                   "shown": len(clustered["nodes"]),
                   "hidden": sum(c["count"]
                                 for c in clustered["clusters"])},
        "truncated": bool(clustered["clusters"]),
    }


def _target_string(directory: Path, manifest: dict) -> str:
    """Target dir for a run or preset, as a ROOT-relative string.

    Prefers the run's own record (report.json target), then the generic
    workspace, then the pinned preset target. Unresolved on purpose:
    the checkout may be a symlink/junction (dev worktrees), which must
    not break the cache key or the graph build.
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
    return target


def _source_root(directory: Path, manifest: dict) -> Path:
    """Resolved target checkout, confined to targets/ and workspaces/.

    Anything else is a 404, never a wider read.
    """
    target = _target_string(directory, manifest)
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


def _read_run_json(directory: Path, *parts: str):
    """Artifact JSON, or None when missing/unreadable (optional data)."""
    path = directory
    for part in parts:
        path = path / part
    if not path.is_file():
        return None
    try:
        return run_store.read_json(path)
    except (ValueError, OSError):
        return None


def _jsonl_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


@router.get("/api/runs/{run_id}/provenance")
def ui_provenance(run_id: str) -> dict:
    """Recorded-call counts for the report provenance block.

    Manifest fields (model, times, SHAs, replay source) come from
    GET /api/runs/{id}; only the JSONL counts need a dedicated read.
    """
    try:
        directory = run_or_preset_dir(run_id)
    except ValueError:
        raise HTTPException(404, f"unknown run: {run_id}") from None
    return {
        "llm_calls": _jsonl_count(directory / run_store.LLM_CALLS),
        "cmds": _jsonl_count(directory / run_store.CMDS),
        "fetches": _jsonl_count(directory / run_store.FETCHES),
    }


def _namespace(obj):
    """Dicts/lists to attribute-accessed namespaces (claim checker input)."""
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _namespace(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_namespace(v) for v in obj]
    return obj


def report_from_json(data: dict):
    """Rebuild a Report from its recorded report.json.

    Only known fields are passed through, so recordings from newer
    code with extra keys still load.
    """
    from chainreaction.explain import (  # noqa: PLC0415 - lazy, heavy
        LayerResult, Report)

    layers = []
    for raw in data.get("layers") or []:
        if not isinstance(raw, dict):
            continue
        known = {k: raw.get(k) for k in
                 ("name", "actual", "missing", "recall", "error_tail",
                  "ok", "failure_site") if k in raw}
        try:
            layers.append(LayerResult(**known))
        except TypeError:
            continue
    items = [_namespace(i) for i in data.get("change_items") or []]
    return Report(
        decision=data.get("decision") or "",
        target=data.get("target") or "",
        package=data.get("package") or "",
        numpy_version=data.get("numpy_version"),
        decision_package=data.get("decision_package") or "",
        before_version=data.get("before_version"),
        after_version=data.get("after_version"),
        change_symbols=list(data.get("change_symbols") or []),
        hard=list(data.get("hard") or []),
        at_risk=list(data.get("at_risk") or []),
        spine=list(data.get("spine") or []),
        spine_evidence=[tuple(h) for h in data.get("spine_evidence") or []
                       if isinstance(h, (list, tuple)) and len(h) >= 4],
        spine_match=bool(data.get("spine_match")),
        before_numpy=data.get("before_numpy"),
        pr_url=data.get("pr_url") or "",
        pr_files=list(data.get("pr_files") or []),
        file_precision=data.get("file_precision"),
        file_recall=data.get("file_recall"),
        file_missing=list(data.get("file_missing") or []),
        file_behavior=list(data.get("file_behavior") or []),
        probe_kind=data.get("probe_kind") or "import",
        change_items=items,
        layers=layers,
        grep_hits=data.get("grep_hits") or 0,
        ruff_hits=data.get("ruff_hits"),
        narrative=data.get("narrative"),
        deprecated_symbols=list(data.get("deprecated_symbols") or []),
        changed_symbols=list(data.get("changed_symbols") or []),
        interpret_recall=data.get("interpret_recall"),
        interpret_table_size=data.get("interpret_table_size") or 0,
        interpret_missed=list(data.get("interpret_missed") or []),
        interpret_blind_hits=data.get("interpret_blind_hits") or 0,
        interpret_blind_recall=data.get("interpret_blind_recall"),
        used_symbols=list(data.get("used_symbols") or []),
        fix_order=list(data.get("fix_order") or []),
    )


@router.get("/api/runs/{run_id}/claims")
def ui_claims(run_id: str) -> dict:
    """Per-claim verification (label, status, detail) for the report view.

    Recomputed from the recorded report.json against the checkout's AST
    graph — the same checker the pipeline uses, so the UI can never
    show a greener result than the artifacts support.
    """
    from chainreaction.explain import (  # noqa: PLC0415 - lazy, heavy
        claim_checks)

    try:
        directory = run_or_preset_dir(run_id)
    except ValueError:
        raise HTTPException(404, f"unknown run: {run_id}") from None
    manifest = run_store.read_json(directory / run_store.MANIFEST)
    report_data = _read_run_json(directory, run_store.ARTIFACTS,
                                 "report.json")
    if not isinstance(report_data, dict):
        raise HTTPException(404, "report not available yet")
    package = report_data.get("package") or manifest.get("package") or ""
    if not package:
        raise HTTPException(404, "report not available yet")
    _source_root(directory, manifest)  # 404 unless checked out
    target = _target_string(directory, manifest)
    graph = cached_repo_graph(
        target, package, manifest.get("resolved_commit"))
    checks = claim_checks(graph, report_from_json(report_data))
    return {"claims": [
        {"label": c.label, "status": c.status, "detail": c.detail}
        for c in checks]}


EXPORT_FORMATS = ("md", "json", "bundle")


def _export_filename(run_id: str, suffix: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", run_id).strip("-") or "run"
    return f"chainreaction-{slug}.{suffix}"


@router.get("/api/runs/{run_id}/export")
def ui_export(run_id: str,
              format: str = Query(default="md")) -> Response:
    """Download the report (md|json) or the full run bundle (zip).

    Bytes come straight from the run directory, so an export always
    matches the artifacts the views render. Accepts run ids and
    `preset:<id>`.
    """
    if format not in EXPORT_FORMATS:
        raise HTTPException(
            400, f"invalid format: {format!r} (want md|json|bundle)")
    try:
        directory = run_or_preset_dir(run_id)
    except ValueError:
        raise HTTPException(404, f"unknown run: {run_id}") from None
    if format in ("md", "json"):
        name = "report.md" if format == "md" else "report.json"
        path = directory / run_store.ARTIFACTS / name
        if not path.is_file():
            raise HTTPException(404, f"{name} not available yet")
        media = ("text/markdown" if format == "md"
                 else "application/json")
        return Response(
            path.read_bytes(), media_type=media, headers={
                "Content-Disposition":
                    f'attachment; filename="{_export_filename(run_id, format)}"',
            })
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(p for p in directory.rglob("*")
                           if p.is_file()):
            archive.write(path, path.relative_to(directory).as_posix())
    return Response(
        buffer.getvalue(), media_type="application/zip", headers={
            "Content-Disposition":
                f'attachment; filename="{_export_filename(run_id, "zip")}"',
        })


def _hit_files(hits, root: str | Path) -> set:
    """Repo-relative hit files, test files excluded like the file score."""
    from chainreaction.graph.repo import (  # noqa: PLC0415 - lazy, cheap
        relative_posix)

    files = set()
    for hit in hits:
        rel = relative_posix(hit.file, root)
        if "/tests/" not in f"/{rel}" and not rel.startswith("tests/"):
            files.add(rel)
    return files


def _recorded_ruff_stdout(directory: Path) -> str | None:
    """Stdout of the recorded `ruff check` invocation, if any.

    The pipeline records tool runs in cmds.jsonl, so replay parses the
    same bytes the live run saw — ruff is never executed here.
    """
    path = directory / run_store.CMDS
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        cmd = rec.get("cmd") or []
        if cmd[:1] == ["<tool:ruff>"] and not rec.get("missing"):
            stdout = rec.get("stdout")
            return stdout if isinstance(stdout, str) else None
    return None


def _score_files(files: set, actual: set) -> dict:
    from chainreaction.scorer import (  # noqa: PLC0415 - lazy, cheap
        precision_recall)

    score = precision_recall(set(files), set(actual))
    return {"hits": None, "files": sorted(files),
            "precision": score.precision, "recall": score.recall}


@router.get("/api/runs/{run_id}/baseline-files")
def ui_baseline_files(run_id: str) -> dict:
    """Grep and ruff scored as file sets vs the upstream PR.

    Raw hit counts can't show ChainReaction did better; file
    precision/recall against the same PR files can. Grep re-runs the
    pipeline's own deterministic regex over the pinned checkout with
    patterns from the recorded changeset; ruff parses the recorded
    tool stdout (never executed). Either baseline is null when its
    data is unavailable. 404 without an upstream PR to score against.
    """
    from chainreaction.baselines import (  # noqa: PLC0415 - lazy, cheap
        grep_hits, patterns_for_changeset, ruff_npy201)
    from chainreaction.graph.repo import package_dir

    try:
        directory = run_or_preset_dir(run_id)
    except ValueError:
        raise HTTPException(404, f"unknown run: {run_id}") from None
    manifest = run_store.read_json(directory / run_store.MANIFEST)
    report = _read_run_json(directory, run_store.ARTIFACTS, "report.json")
    if not isinstance(report, dict):
        raise HTTPException(404, "report not available yet")
    pr_files = list(report.get("pr_files") or [])
    if not pr_files:
        raise HTTPException(404, "no upstream PR recorded for this run")
    actual = set(pr_files)
    target = _target_string(directory, manifest)
    package = report.get("package") or manifest.get("package") or ""

    grep = None
    changeset = _read_run_json(directory, run_store.ARTIFACTS,
                               "changeset.json")
    if isinstance(changeset, dict) and package:
        try:
            root = _source_root(directory, manifest)
        except HTTPException:
            root = None
        if root is not None:
            items = [_namespace(i) for i in changeset.get("items") or []
                     if isinstance(i, dict)]
            patterns = patterns_for_changeset(
                SimpleNamespace(items=items))
            hits = grep_hits(package_dir(root, package), patterns)
            grep = _score_files(_hit_files(hits, root), actual)
            grep["hits"] = len(hits)

    ruff = None
    stdout = _recorded_ruff_stdout(directory)
    if stdout is not None:
        hits = ruff_npy201(
            target,
            run=lambda cmd, cwd: SimpleNamespace(stdout=stdout,
                                                 missing=None))
        ruff = _score_files(_hit_files(hits or [], target), actual)
        ruff["hits"] = len(hits or [])

    return {"pr_files": sorted(actual), "pr_ref": _pr_ref(
        report.get("pr_url") or ""), "grep": grep, "ruff": ruff}
