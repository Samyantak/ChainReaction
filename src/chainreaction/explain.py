"""Explainer report structure + claim checker (deterministic).

Renders the evidence-linked report from pipeline artifacts. The LLM
narrative plugs into `Report.narrative` later; the claim checker verifies
every structural claim against the graph right now.
"""

import json
from dataclasses import asdict, dataclass, field

import networkx as nx


@dataclass
class LayerResult:
    name: str
    actual: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # actual but not predicted
    recall: float = 1.0
    error_tail: str = ""


@dataclass
class Report:
    decision: str
    target: str
    package: str
    numpy_version: str | None
    change_symbols: list[str]
    hard: list[str]
    at_risk: list[str]
    spine: list[str]  # predicted layer-1 chain, outermost first
    spine_evidence: list[tuple[str, str, str, int]] = field(default_factory=list)
    layers: list[LayerResult] = field(default_factory=list)
    grep_hits: int = 0
    ruff_hits: int | None = None
    narrative: str | None = None  # filled by Muse Spark once the key exists


def check_report(g: nx.DiGraph, report: Report) -> list[str]:
    """Verify every structural claim; return violations (empty = clean)."""
    violations = []
    for node in report.hard + report.at_risk:
        if node not in g.nodes:
            violations.append(f"predicted node not in graph: {node}")
    hard_set = set(report.hard)
    for layer in report.layers:
        for module in layer.actual:
            if module not in hard_set and module not in layer.missing:
                violations.append(
                    f"{layer.name}: actual {module} neither predicted nor missing"
                )
        for module in layer.missing:
            if module in hard_set:
                violations.append(
                    f"{layer.name}: {module} listed missing but predicted HARD"
                )
    for hop in report.spine_evidence:
        u, v, path, line = hop
        data = g.edges.get((u, v))
        if data is None:
            violations.append(f"evidence edge missing from graph: {u} -> {v}")
        elif data.get("file") != path or data.get("line") != line:
            violations.append(
                f"evidence mismatch for {u} -> {v}: graph has "
                f"{data.get('file')}:{data.get('line')}, report has {path}:{line}"
            )
    return violations


def render_markdown(report: Report) -> str:
    lines = [
        f"# ChainReaction report: {report.decision}",
        "",
        f"Target: `{report.target}` (pre-existing, analyzed by ChainReaction) | "
        f"numpy: {report.numpy_version or 'unknown'}",
        "",
        "## Change set",
        "",
        *[f"- `{s}`" for s in report.change_symbols],
        "",
        "## Prediction",
        "",
        f"HARD: {len(report.hard)} modules | AT_RISK: {len(report.at_risk)} nodes",
        "",
        "Predicted layer-1 chain:",
        "",
        *[
            f"- `{u}` -> `{v}` ({path}:{line})"
            for u, v, path, line in report.spine_evidence
        ],
        "",
        "## Verification",
        "",
    ]
    for layer in report.layers:
        lines += [
            f"### {layer.name} (recall {layer.recall:.2f})",
            "",
            f"Actual: {' -> '.join(f'`{m}`' for m in layer.actual) or '(import ok)'}",
        ]
        if layer.missing:
            lines.append(f"Missed: {', '.join(f'`{m}`' for m in layer.missing)}")
        if layer.error_tail:
            lines += ["```", layer.error_tail, "```"]
        lines.append("")
    lines += [
        "## Baselines",
        "",
        f"- grep: {report.grep_hits} hits",
        f"- ruff NPY201: "
        f"{report.ruff_hits if report.ruff_hits is not None else 'not installed'} hits",
        "",
        "## Narrative",
        "",
        report.narrative or "_Pending Muse Spark (MODEL_API_KEY not configured)._",
        "",
    ]
    return "\n".join(lines)


def render_json(report: Report) -> str:
    return json.dumps(asdict(report), indent=2)
