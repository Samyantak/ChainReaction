"""Explainer report structure + claim checker (deterministic).

Renders the evidence-linked report from pipeline artifacts. The LLM
narrative plugs into `Report.narrative` later; the claim checker verifies
every structural claim against the graph right now.
"""

import json
import math
import re
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
    # Missed PR files that are behavior changes, not removed-symbol uses
    # (flagged for honesty, not predicted).
    file_behavior: list[str] = field(default_factory=list)
    probe_kind: str = "import"  # "import" or "decision"
    change_items: list = field(default_factory=list)  # config ChangeItems
    layers: list[LayerResult] = field(default_factory=list)
    grep_hits: int = 0
    ruff_hits: int | None = None
    narrative: str | None = None  # filled by Muse Spark once the key exists


_MODULE_RE = re.compile(r"\b([A-Za-z_]\w*(?:\.\w+)+)\b")
_FILELINE_RE = re.compile(r"\b([\w\-./\\]+\.py):(\d+)\b")
_PY_NAME_RE = re.compile(r"\b[\w\-./\\]+\.py\b")
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)*\b")
_URL_RE = re.compile(r"https?://\S+")


def _norm_path(path: str) -> str:
    return path.replace("\\", "/")


def _float_spellings(value: float) -> set[str]:
    pct = value * 100
    return {
        str(value),
        f"{value:.2f}",
        f"{value:.1f}",
        f"{value:.0f}",
        # Percent styles: banker's ("62%"), half-up ("63%"), and
        # one-decimal ("62.5%") spellings of the same recall.
        str(int(round(pct))),
        str(math.floor(pct + 0.5)),
        f"{pct:.1f}",
    }


def _report_numbers(report: Report) -> set[str]:
    """Every number the narrative may legitimately cite.

    Membership set only: a number passes when it equals ANY count here,
    not when it supports the specific claim around it.
    """
    nums = {
        str(len(report.hard)),
        str(len(report.at_risk)),
        str(len(report.spine)),
        str(len(report.spine_evidence)),
        str(len(report.change_symbols)),
        str(report.grep_hits),
        str(len(report.pr_files)),
        str(len(report.file_missing)),
        str(len(report.pr_files) - len(report.file_missing)),  # flagged
        str(len(report.file_behavior)),
        str(len(report.layers)),
    }
    if report.ruff_hits is not None:
        nums.add(str(report.ruff_hits))
    for layer in report.layers:
        nums.add(str(len(layer.actual)))
        nums.add(str(len(layer.missing)))
        nums.update(_float_spellings(layer.recall))
    for value in (report.file_precision, report.file_recall):
        if value is not None:
            nums.update(_float_spellings(value))
    for _, _, _, line in report.spine_evidence:
        nums.add(str(line))
    for version in (report.numpy_version, report.before_numpy):
        if version:
            nums.add(version)
            parts = version.split(".")  # "numpy 2.0" short for "2.0.2"
            for i in range(1, len(parts)):
                nums.add(".".join(parts[:i]))
    for blob in (report.decision, report.target, report.pr_url,
                 *report.change_symbols, *report.pr_files,
                 *report.file_missing):
        nums.update(_NUMBER_RE.findall(blob or ""))
    return nums


def _narrative_violations_by_kind(report: Report, text: str
                                ) -> dict[str, list[str]]:
    """Narrative violations grouped by claim kind.

    Kinds are "module" (unknown dotted names), "evidence" (file:line
    with no matching spine hop), and "number" (not in the report's
    number set). Bare *.py filenames are stripped, not verified.
    """
    # Blank URLs, file:line spans, then bare filenames, so their
    # fragments (github.com, __init__.py, line numbers, casting.py)
    # don't read as module or number claims.
    redacted = _URL_RE.sub(" ", text)
    cited: list[tuple[str, int]] = []

    def _blank(match: re.Match) -> str:
        cited.append((match.group(1), int(match.group(2))))
        return " "

    redacted = _FILELINE_RE.sub(_blank, redacted)
    redacted = _PY_NAME_RE.sub(" ", redacted)

    known = (set(report.hard) | set(report.at_risk) | set(report.spine)
             | set(report.change_symbols)
             | {i.symbol for i in report.change_items}
             | {report.package})
    for layer in report.layers:
        known.update(layer.actual)
    for name in list(known):  # enclosing scopes of impacted defs are real
        parts = name.split(".")
        for i in range(2, len(parts)):
            known.add(".".join(parts[:i]))
    modules: list[str] = []
    seen: set[str] = set()
    for module in _MODULE_RE.findall(redacted):
        if module not in known and module not in seen:
            seen.add(module)
            modules.append(f"narrative mentions unknown module: {module}")

    evidence = [(_norm_path(path), line)
                for _, _, path, line in report.spine_evidence]
    cites: list[str] = []
    for path, line in cited:
        norm = _norm_path(path)
        ok = any(
            line == known_line
            and (norm == known_path
                 or known_path.endswith("/" + norm)
                 or norm.endswith("/" + known_path))
            for known_path, known_line in evidence
        )
        if not ok:
            cites.append(
                f"narrative cites unverified evidence: {path}:{line}")

    allowed = _report_numbers(report)
    numbers: list[str] = []
    seen_numbers: set[str] = set()
    for number in _NUMBER_RE.findall(redacted):
        if number not in allowed and number not in seen_numbers:
            seen_numbers.add(number)
            numbers.append(f"narrative cites ungrounded number: {number}")
    return {"module": modules, "evidence": cites, "number": numbers}


def check_narrative(report: Report, narrative: str | None = None
                    ) -> list[str]:
    """Verify narrative claims against the report; return violations.

    Pulls every dotted module name, number, and file:line out of the
    narrative text. Unknown modules, ungrounded numbers, and file:line
    pairs with no matching spine evidence are violations. A missing or
    empty narrative is clean.

    Number checking is loose on purpose: a number passes when it appears
    anywhere in the report's number set (counts, recalls, line numbers,
    versions), not when it supports the specific claim around it. Bare
    *.py filenames are stripped, not verified.
    """
    text = report.narrative if narrative is None else narrative
    if not text:
        return []
    grouped = _narrative_violations_by_kind(report, text)
    return grouped["module"] + grouped["evidence"] + grouped["number"]


def check_report(g: nx.DiGraph, report: Report) -> list[str]:
    """Verify every structural and narrative claim; return violations."""
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
        elif _norm_path(str(data.get("file"))) != _norm_path(path) \
                or data.get("line") != line:
            violations.append(
                f"evidence mismatch for {u} -> {v}: graph has "
                f"{data.get('file')}:{data.get('line')}, report has {path}:{line}"
            )
    violations.extend(check_narrative(report))
    return violations


@dataclass
class ClaimCheck:
    label: str
    status: str  # "pass" | "fail" | "pending"
    detail: str = ""


def claim_checks(g: nx.DiGraph, report: Report) -> list[ClaimCheck]:
    """Per-claim verification for the API/UI (check mark or flag each)."""
    checks: list[ClaimCheck] = []
    predicted = sorted(set(report.hard) | set(report.at_risk))
    missing = [n for n in predicted if n not in g.nodes]
    if missing:
        shown = ", ".join(missing[:3])
        if len(missing) > 3:
            shown += f", +{len(missing) - 3} more"
        checks.append(ClaimCheck("predicted nodes exist in graph",
                                 "fail", f"missing: {shown}"))
    else:
        checks.append(ClaimCheck("predicted nodes exist in graph",
                                 "pass", f"{len(predicted)} nodes"))
    flagged_set = set(report.hard) | set(report.at_risk)
    for layer in report.layers:
        issues = [
            f"actual {m} neither predicted nor missing"
            for m in layer.actual
            if m not in flagged_set and m not in layer.missing
        ] + [
            f"{m} listed missing but predicted"
            for m in layer.missing if m in flagged_set
        ]
        if issues:
            checks.append(ClaimCheck(
                f"{layer.name}: actual modules accounted for",
                "fail", "; ".join(issues)))
        else:
            checks.append(ClaimCheck(
                f"{layer.name}: actual modules accounted for",
                "pass", f"recall {layer.recall:.2f}"))
    for u, v, path, line in report.spine_evidence:
        label = f"{u} → {v}"
        data = g.edges.get((u, v))
        if data is None:
            checks.append(ClaimCheck(label, "fail",
                                     "edge missing from graph"))
        elif _norm_path(str(data.get("file"))) != _norm_path(path) \
                or data.get("line") != line:
            checks.append(ClaimCheck(
                label, "fail",
                f"graph has {data.get('file')}:{data.get('line')}, "
                f"report has {path}:{line}"))
        else:
            checks.append(ClaimCheck(label, "pass", f"{path}:{line}"))
    if not report.narrative:
        for label in ("narrative module names",
                      "narrative file:line citations",
                      "narrative numbers"):
            checks.append(ClaimCheck(label, "pending", "no narrative yet"))
    else:
        grouped = _narrative_violations_by_kind(report, report.narrative)
        for label, kind in (("narrative module names", "module"),
                            ("narrative file:line citations", "evidence"),
                            ("narrative numbers", "number")):
            items = grouped[kind]
            detail = "; ".join(v.split(": ", 1)[-1] for v in items)
            checks.append(ClaimCheck(
                label, "fail" if items else "pass", detail))
    return checks


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
           + ("; derived from probe" if i.source != "guide" else "")
           + ")"
           if i.detail is not None else f"- `{i.symbol}` ({i.kind}"
           + ("; derived from probe" if i.source != "guide" else "")
           + ")"
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
        behavior = set(report.file_behavior)
        missed = [m for m in report.file_missing if m not in behavior]
        lines += [
            "## Ground truth (PR)",
            "",
            f"URL: {report.pr_url}",
            f"Files needing edits: precision {report.file_precision:.2f}, "
            f"recall {report.file_recall:.2f} "
            f"({flagged}/{len(report.pr_files)} PR files flagged)",
            "Missed: "
            + (", ".join(f"`{m}`" for m in missed) or "none"),
            "Behavior changes (flagged, not predicted): "
            + (", ".join(f"`{m}`" for m in report.file_behavior)
               or "none"),
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
        *_narrative_check_lines(report),
    ]
    return "\n".join(lines)


def _narrative_check_lines(report: Report) -> list[str]:
    if not report.narrative:
        return ["Claim check: _pending (no narrative yet)._", ""]
    violations = check_narrative(report)
    if not violations:
        return ["Claim check: ✓ clean", ""]
    return ["Claim check: ✗ "
            f"{len(violations)} issue{'s' if len(violations) != 1 else ''}:",
            "", *[f"- {v}" for v in violations], ""]


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
        "spine_evidence": [f"{u} → {v} at {path}:{line}"
                           for u, v, path, line in report.spine_evidence],
        "pr_url": report.pr_url,
        "file_precision": report.file_precision,
        "file_recall": report.file_recall,
        "file_missing": report.file_missing,
        "layers": [{"name": l.name, "ok": l.ok, "recall": l.recall,
                    "missing": l.missing} for l in report.layers],
    }, indent=2, ensure_ascii=False)
    return chat(NARRATE_SYSTEM_PROMPT, f"Report JSON:\n{facts}\n",
                max_tokens=8000)
