"""Explainer report structure + claim checker (deterministic).

Renders the evidence-linked report from pipeline artifacts. The LLM
narrative plugs into `Report.narrative` later; the claim checker verifies
every structural claim against the graph right now.
"""

import json
from dataclasses import asdict, dataclass, field

import networkx as nx

from chainreaction.llm import chat


@dataclass
class LayerResult:
    name: str
    actual: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # actual but not predicted
    recall: float = 1.0
    error_tail: str = ""
    ok: bool = True  # the probe process exited 0 (import passed)


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
    spine_match: bool = False  # chain_match(spine, layers[0].actual)
    before_numpy: str | None = None
    pr_url: str = ""
    pr_files: list[str] = field(default_factory=list)
    file_precision: float | None = None
    file_recall: float | None = None
    file_missing: list[str] = field(default_factory=list)
    probe_kind: str = "import"  # "import" or "decision"
    change_items: list = field(default_factory=list)  # config ChangeItems
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
    flagged_set = set(report.hard) | set(report.at_risk)
    for layer in report.layers:
        for module in layer.actual:
            if module not in flagged_set and module not in layer.missing:
                violations.append(
                    f"{layer.name}: actual {module} neither predicted nor missing"
                )
        for module in layer.missing:
            if module in flagged_set:
                violations.append(
                    f"{layer.name}: {module} listed missing but predicted"
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
    numpy_label = report.numpy_version or (
        "n/a (decision probe)" if report.probe_kind == "decision" else "unknown")
    lines = [
        f"# ChainReaction report: {report.decision}",
        "",
        f"Target: `{report.target}` (pre-existing, analyzed by ChainReaction) | "
        f"numpy: {numpy_label}"
        + (f" (before: {report.before_numpy})" if report.before_numpy else ""),
        "",
        "## Change set",
        "",
        *[f"- `{s}`" for s in report.change_symbols],
        *[f"- `{i.symbol}` ({i.kind}: "
           f"{i.detail.expected} -> {i.detail.actual}"
           + (f"; {i.detail.note}" if i.detail.note else "")
           + ")"
           if i.detail is not None else f"- `{i.symbol}` ({i.kind})"
           for i in report.change_items],
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
        f"Chain vs layer-1 actual: "
        f"{'MATCH' if report.spine_match else 'DIFFERS'}",
        "",
    ]
    if report.pr_url:
        flagged = len(report.pr_files) - len(report.file_missing)
        lines += [
            "## Ground truth (PR)",
            "",
            f"URL: {report.pr_url}",
            f"Files needing edits: precision {report.file_precision:.2f}, "
            f"recall {report.file_recall:.2f} "
            f"({flagged}/{len(report.pr_files)} PR files flagged)",
            "Missed: "
            + (", ".join(f"`{m}`" for m in report.file_missing) or "none"),
            "",
        ]
    lines += ["## Verification", ""]
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
        f"{report.ruff_hits if report.ruff_hits is not None else 'n/a'} hits",
        "",
        "## Narrative",
        "",
        report.narrative or "_Pending Muse Spark (MODEL_API_KEY not configured)._",
        "",
    ]
    return "\n".join(lines)


def render_json(report: Report) -> str:
    return json.dumps(asdict(report), indent=2)


NARRATE_SYSTEM_PROMPT = """\
You are the ChainReaction explainer. Given an upgrade-impact report as
JSON, write a 3-5 paragraph plain-English summary for a maintainer:
what broke, the single verified worst chain of damage (name the
modules), how confident the evidence is, and what to fix first.
Refer to modules by exact name; do not invent modules, quotes, or
numbers beyond the report. Keep it under 300 words."""


def generate_narrative(report: Report) -> str:
    """Summarize a Report with Muse Spark (needs MODEL_API_KEY)."""
    facts = json.dumps({
        "decision": report.decision,
        "target": report.target,
        "package": report.package,
        "numpy_version": report.numpy_version,
        "change_symbols": report.change_symbols,
        "n_hard": len(report.hard),
        "n_at_risk": len(report.at_risk),
        "spine": report.spine,
        "spine_match": report.spine_match,
        "spine_evidence": [f"{imp} imports {frm}:{ln}"
                           for imp, frm, _sym, ln in report.spine_evidence],
        "pr_url": report.pr_url,
        "file_precision": report.file_precision,
        "file_recall": report.file_recall,
        "file_missing": report.file_missing,
        "layers": [{"name": l.name, "ok": l.ok, "recall": l.recall,
                    "missing": l.missing} for l in report.layers],
    }, indent=2)
    return chat(NARRATE_SYSTEM_PROMPT, f"Report JSON:\n{facts}\n",
                max_tokens=8000)
