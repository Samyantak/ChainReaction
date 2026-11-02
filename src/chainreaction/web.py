"""FastAPI backend: live pipeline JSON for the UI. Run with:

    uvicorn chainreaction.web:app --reload --port 8000
"""

import json
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from chainreaction import scenarios
from chainreaction.explain import claim_checks
from chainreaction.graph.repo import relative_posix
from chainreaction.pipeline import PipelineResult, run_pipeline
from chainreaction.runs import store as run_store
from chainreaction.runs import worker as run_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_worker.startup_sweep()
    yield


app = FastAPI(title="ChainReaction", lifespan=lifespan)
_cache: dict[str, PipelineResult] = {}


def _result(scenario_id: str) -> PipelineResult:
    if scenario_id not in _cache:
        try:
            _cache[scenario_id] = run_pipeline(scenario_id)
        except KeyError:
            raise HTTPException(404, f"unknown scenario: {scenario_id}") from None
    return _cache[scenario_id]


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/scenarios")
def list_scenarios() -> list[dict]:
    return [
        {"id": s.id, "decision": s.decision, "target": s.target}
        for s in scenarios.SCENARIOS.values()
    ]


@app.get("/api/report")
def get_report(scenario: str = "nibabel-numpy2") -> JSONResponse:
    res = _result(scenario)
    return JSONResponse({
        **asdict(res.report),
        "claim_checks": [asdict(c) for c in claim_checks(res.graph,
                                                         res.report)],
    })


@app.get("/api/graph")
def get_graph(scenario: str = "nibabel-numpy2") -> dict:
    """Module-level impacted subgraph: HARD/AT_RISK modules + their edges."""
    res = _result(scenario)
    g, impacts = res.graph, res.impacts
    modules = {
        n for n in impacts
        if g.nodes.get(n, {}).get("kind") == "module"
    } | set(res.report.spine)
    nodes = [
        {"id": n, "status": impacts[n].status if n in impacts else "OK"}
        for n in sorted(modules)
    ]
    edges = [
        {"source": u, "target": v, "kind": d.get("kind"),
         "file": relative_posix(d["file"], res.report.target)
         if d.get("file") else d.get("file"),
         "line": d.get("line")}
        for u, v, d in g.edges(data=True)
        if u in modules and v in modules
        and d.get("kind") in ("IMPORTS", "EXECUTES_AT_IMPORT")
    ]
    return {"nodes": nodes, "edges": edges,
            "spine": res.report.spine}


class CreateRunBody(BaseModel):
    repo_url: str | None = None
    ref: str | None = None
    decision: str | None = None
    guide_url: str | None = None
    preset: str | None = None
    mode: str = "live"
    replay_from: str | None = None


def _manifest_or_404(run_id: str) -> dict:
    try:
        path = run_store.run_dir(run_id) / run_store.MANIFEST
    except ValueError:
        raise HTTPException(404, f"unknown run: {run_id}") from None
    if not path.is_file():
        raise HTTPException(404, f"unknown run: {run_id}")
    return run_store.read_json(path)


@app.post("/api/runs")
def create_run(body: CreateRunBody) -> dict:
    try:
        run_id = run_worker.create_run(run_worker.RunRequest(
            preset=body.preset, mode=body.mode,
            replay_from=body.replay_from, decision=body.decision,
            guide_url=body.guide_url, repo_url=body.repo_url,
            ref=body.ref))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    run_worker.submit_run(run_id)
    return {"id": run_id}


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return [
        {"id": m.get("run_id"), "decision": m.get("decision"),
         "repo": m.get("repo_url") or m.get("preset"),
         "status": m.get("status"), "created_at": m.get("created_at"),
         "mode": m.get("mode")}
        for m in run_store.list_runs()
    ]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    manifest = _manifest_or_404(run_id)
    report = None
    report_path = (run_store.run_dir(run_id) / run_store.ARTIFACTS
                   / "report.json")
    if report_path.is_file():
        report = run_store.read_json(report_path)
    return {"manifest": manifest,
            "stage": manifest.get("current_stage"),
            "status": manifest.get("status"),
            "report": report}


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict:
    try:
        status = run_worker.cancel_run(run_id)
    except (KeyError, ValueError):
        raise HTTPException(404, f"unknown run: {run_id}") from None
    return {"id": run_id, "status": status}


@app.get("/api/runs/{run_id}/artifacts/{name:path}")
def get_artifact(run_id: str, name: str):
    _manifest_or_404(run_id)
    base = run_store.run_dir(run_id) / run_store.ARTIFACTS
    if not name or ".." in Path(name).parts or Path(name).is_absolute():
        raise HTTPException(400, f"invalid artifact: {name}")
    path = (base / name).resolve()
    if path != base.resolve() and base.resolve() not in path.parents:
        raise HTTPException(400, f"invalid artifact: {name}")
    if not path.is_file():
        raise HTTPException(404, f"unknown artifact: {name}")
    media = "application/json" if path.suffix == ".json" else "text/plain"
    return Response(path.read_bytes(), media_type=media)


def _sse(event: dict) -> str:
    return (f"id: {event.get('seq')}\nevent: {event.get('type')}\n"
            f"data: {json.dumps(event)}\n\n")


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str, request: Request,
               from_: int | None = Query(default=None, alias="from")):
    _manifest_or_404(run_id)
    start = 0
    last_id = request.headers.get("last-event-id")
    if last_id is not None:
        try:
            start = int(last_id)
        except ValueError:
            start = 0
    elif from_ is not None:
        start = max(0, from_)
    events_path = run_store.run_dir(run_id) / run_store.EVENTS

    def gen():
        pos = start
        idle = 0
        yield "retry: 1000\n\n"
        while True:
            batch = run_store.read_events_after(events_path, pos)
            for event in batch:
                pos = event.get("seq", pos)
                yield _sse(event)
            idle = 0 if batch else idle + 1
            try:
                status = run_store.read_json(
                    run_store.run_dir(run_id)
                    / run_store.MANIFEST).get("status")
            except (ValueError, OSError):
                status = "running"
            if status in run_store.TERMINAL:
                # Terminal manifest implies all events are written (the
                # worker emits the final log first); drain, then close.
                tail = run_store.read_events_after(events_path, pos)
                for event in tail:
                    pos = event.get("seq", pos)
                    yield _sse(event)
                break
            if idle and idle % 75 == 0:
                yield ": ping\n\n"  # keep proxies from idling out
            time.sleep(0.2)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


try:
    from fastapi.staticfiles import StaticFiles

    _dist = Path(__file__).resolve().parent.parent.parent / "ui" / "dist"
    if _dist.is_dir():
        app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
except ImportError:  # pragma: no cover - fastapi always ships staticfiles dep set
    pass
