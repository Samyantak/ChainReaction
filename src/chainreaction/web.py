"""FastAPI backend: live pipeline JSON for the UI. Run with:

    uvicorn chainreaction.web:app --reload --port 8000
"""

from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from chainreaction import scenarios
from chainreaction.pipeline import PipelineResult, run_pipeline

app = FastAPI(title="ChainReaction")
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
    return JSONResponse(asdict(_result(scenario).report))


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
         "file": d.get("file"), "line": d.get("line")}
        for u, v, d in g.edges(data=True)
        if u in modules and v in modules
        and d.get("kind") in ("IMPORTS", "EXECUTES_AT_IMPORT")
    ]
    return {"nodes": nodes, "edges": edges,
            "spine": res.report.spine}


try:
    from fastapi.staticfiles import StaticFiles

    _dist = Path(__file__).resolve().parent.parent.parent / "ui" / "dist"
    if _dist.is_dir():
        app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
except ImportError:  # pragma: no cover - fastapi always ships staticfiles dep set
    pass
