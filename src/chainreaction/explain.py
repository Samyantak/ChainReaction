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
    # Deepest target frame of the probe traceback, repo-relative
    # "path/to/file.py:line" (None when the probe passed). The brief's
    # fix order lists these; the claim checker accepts them as evidence.
    failure_site: str | None = None


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
    # Warnings (deprecated, still work) and behavior-change flags: reported,
    # never counted as breakage or scored.
    deprecated_symbols: list[str] = field(default_factory=list)
    changed_symbols: list[str] = field(default_factory=list)
    # Interpreter recall against the guide's removal table (None when the
    # guide has no structured table): did the model cover every row?
    # blind_hits/blind_recall score the prose-only pass — the honest
    # baseline for the hybrid design.
    interpret_recall: float | None = None
    interpret_table_size: int = 0
    interpret_missed: list[str] = field(default_factory=list)
    interpret_blind_hits: int = 0
    interpret_blind_recall: float | None = None
    # Breaking symbols with at least one repo use (subset of
    # change_symbols). Only these can seed failures; the brief names
    # them so the narrative cites the used count, not the guide total.
    used_symbols: list[str] = field(default_factory=list)
    # Ordered "label: file:line" fix list: probe layer sites for import
    # upgrades, config break sites in probe-hit order plus the probe
    # site for vendor decisions. The brief reads this; the fix loop
    # peels it one entry at a time.
    fix_order: list[str] = field(default_factory=list)


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


def _test_numbers(extra: dict | None) -> set[str]:
    """Test-suite counts the generic narrative may cite ({} without scores)."""
    scores = (extra or {}).get("scores") or {}
    if not scores:
        return set()
    nums = {
        str(len(scores.get("actual_new") or [])),
        str(len(scores.get("predicted_failing") or [])),
        str(len(scores.get("baseline_failed") or [])),
        str(scores.get("testmap_tests") or 0),
        str(scores.get("testmap_collected") or 0),
    }
    for counts in (scores.get("baseline_counts") or {},
                   scores.get("after_counts") or {}):
        nums.update(str(v) for v in counts.values())
    for value in (scores.get("test_precision"), scores.get("test_recall")):
        if value is not None:
            try:
                nums.update(_float_spellings(float(value)))
            except (TypeError, ValueError):
                pass
    for grp in scores.get("failure_groups") or []:
        nums.add(str(grp.get("count", 0)))
    chain = scores.get("test_chain") or {}
    if chain.get("line"):
        nums.add(str(int(chain["line"])))
    return nums


def _report_numbers(report: Report, extra: dict | None = None) -> set[str]:
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
        str(len(report.used_symbols)),
        str(len(report.change_items)),
        str(len(report.deprecated_symbols)),
        str(len(report.changed_symbols)),
        str(report.grep_hits),
        str(len(report.pr_files)),
        str(len(report.file_missing)),
        str(len(report.pr_files) - len(report.file_missing)),  # flagged
        str(_file_score_bits(report)[2]),  # breaking-PR-file total
        str(len(report.file_behavior)),
        str(len(report.layers)),
    }
    if report.ruff_hits is not None:
        nums.add(str(report.ruff_hits))
    if report.interpret_table_size:
        nums.add(str(report.interpret_table_size))
        nums.add(str(report.interpret_blind_hits))
    if report.interpret_blind_recall is not None:
        nums.update(_float_spellings(report.interpret_blind_recall))
    for layer in report.layers:
        nums.add(str(len(layer.actual)))
        nums.add(str(len(layer.missing)))
        nums.update(_float_spellings(layer.recall))
    for value in (report.file_precision, report.file_recall):
        if value is not None:
            nums.update(_float_spellings(value))
    for _, _, _, line in report.spine_evidence:
        nums.add(str(line))
    for layer in report.layers:
        if layer.failure_site and ":" in layer.failure_site:
            _, _, lineno = layer.failure_site.rpartition(":")
            if lineno.isdigit():
                nums.add(lineno)
    for item in report.change_items:
        code_line = getattr(item, "code_line", 0)
        if getattr(item, "source", "") == "code" and code_line:
            nums.add(str(int(code_line)))
    for version in (report.numpy_version, report.before_numpy):
        if version:
            nums.add(version)
            parts = version.split(".")  # "numpy 2.0" short for "2.0.2"
            for i in range(1, len(parts)):
                nums.add(".".join(parts[:i]))
    blobs = [report.decision, report.target, report.pr_url,
             *report.change_symbols, *report.pr_files,
             *report.file_missing]
    for item in report.change_items:
        blobs.append(item.symbol)
        detail = getattr(item, "detail", None)
        if detail is not None:
            blobs.extend([detail.expected or "", detail.actual or "",
                          detail.note or ""])
    for blob in blobs:
        nums.update(_NUMBER_RE.findall(blob or ""))
    nums.update(_test_numbers(extra))
    return nums


def _narrative_violations_by_kind(report: Report, text: str,
                                  extra: dict | None = None
                                ) -> dict[str, list[str]]:
    """Narrative violations grouped by claim kind.

    Kinds are "module" (unknown dotted names), "evidence" (file:line
    with no matching spine hop, layer failure site, code-grounded
    config item, or test call chain), and "number" (not in the report's
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
             | set(report.change_symbols) | set(report.used_symbols)
             | set(report.deprecated_symbols) | set(report.changed_symbols)
             | {i.symbol for i in report.change_items}
             | {report.package})
    chain = ((extra or {}).get("scores") or {}).get("test_chain") or {}
    if chain.get("func"):
        known.add(chain["func"])
    if chain.get("symbol"):
        known.add(chain["symbol"])
    for layer in report.layers:
        known.update(layer.actual)
    for item in report.change_items:
        detail = getattr(item, "detail", None)
        if detail is not None:
            for blob in (detail.expected or "", detail.actual or "",
                         detail.note or ""):
                known.update(_MODULE_RE.findall(blob))
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
    for layer in report.layers:
        if layer.failure_site and ":" in layer.failure_site:
            path, _, lineno = layer.failure_site.rpartition(":")
            if lineno.isdigit():
                evidence.append((_norm_path(path), int(lineno)))
    for item in report.change_items:
        code_file = getattr(item, "code_file", "")
        code_line = getattr(item, "code_line", 0)
        if getattr(item, "source", "") == "code" and code_file and code_line:
            evidence.append((_norm_path(code_file), int(code_line)))
    if chain.get("file") and chain.get("line"):
        evidence.append((_norm_path(chain["file"]), int(chain["line"])))
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

    allowed = _report_numbers(report, extra)
    numbers: list[str] = []
    seen_numbers: set[str] = set()
    for number in _NUMBER_RE.findall(redacted):
        if number not in allowed and number not in seen_numbers:
            seen_numbers.add(number)
            numbers.append(f"narrative cites ungrounded number: {number}")
    return {"module": modules, "evidence": cites, "number": numbers}


def check_narrative(report: Report, narrative: str | None = None,
                    extra: dict | None = None
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
    grouped = _narrative_violations_by_kind(report, text, extra)
    return grouped["module"] + grouped["evidence"] + grouped["number"]


def _code_hop_item(report: Report, path: str, line: int):
    """Verified code item grounding a (path, line) hop, else None.

    Vendor call-path hops cite the config break's code quote rather
    than a graph edge; the quote is the grounding, checked here.
    """
    for item in getattr(report, "change_items", []) or []:
        if getattr(item, "source", "") != "code":
            continue
        if not getattr(item, "verified", False):
            continue
        code_file = getattr(item, "code_file", "")
        code_line = getattr(item, "code_line", 0)
        if not code_file or not code_line or int(code_line) != line:
            continue
        norm, want = _norm_path(code_file), _norm_path(path)
        if norm == want or norm.endswith("/" + want) \
                or want.endswith("/" + norm):
            return item
    return None


def _code_hop_ok(g: nx.DiGraph, report: Report, u: str, v: str,
                 path: str, line: int) -> bool:
    """A hop without a graph edge passes when a verified code item pins
    its file:line and the hop target is that item's symbol or scope."""
    item = _code_hop_item(report, path, line)
    if item is None or u not in g.nodes:
        return False
    symbol = item.symbol
    return (v == symbol or v == symbol.rpartition(".")[0]
            or symbol.startswith(v + "."))


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
            if not _code_hop_ok(g, report, u, v, path, line):
                violations.append(
                    f"evidence edge missing from graph: {u} -> {v}")
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
            if _code_hop_ok(g, report, u, v, path, line):
                checks.append(ClaimCheck(label, "pass",
                                         f"{path}:{line} (code-grounded)"))
            else:
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


def _file_score_bits(report: Report):
    """(flagged, total, breaking_total, missed) for ground-truth lines.

    Behavior-change files are flagged separately, never predicted, so
    recall reads both ways: overall (flagged/total) and breaking-only
    (flagged/breaking_total). Shared by the markdown scoreboard and the
    narrative brief so the two never disagree.
    """
    behavior = set(report.file_behavior)
    missed = [m for m in report.file_missing if m not in behavior]
    flagged = len(report.pr_files) - len(report.file_missing)
    return flagged, len(report.pr_files), flagged + len(missed), missed


def _generic_test_lines(extra: dict) -> list[str]:
    """Test-suite section for generic runs (M2 §7); [] without scores."""
    scores = (extra or {}).get("scores") or {}
    if not scores:
        return []
    lines = ["## Test suites", ""]
    if extra.get("untouched"):
        return lines + ["Decision package is not imported anywhere in "
                        "this repo: empty prediction, suites skipped.", ""]
    if not extra.get("has_suite", True):
        return lines + ["No test suite found: import probes only.", ""]
    if scores.get("suites") == "skipped-no-coverage":
        lines += ["Coverage was unavailable: baseline counts only, "
                  "no test-level scoring.", ""]
    before = scores.get("baseline_counts") or {}
    after = scores.get("after_counts") or {}
    fmt = lambda c: ", ".join(f"{v} {k}" for k, v in sorted(c.items())) \
        or "none"
    lines += [f"- before: {fmt(before)}", f"- after: {fmt(after)}"]
    new = scores.get("actual_new") or []
    lines.append(f"- new failures ({len(new)}): "
                 + (", ".join(f"`{t}`" for t in new[:20])
                    + (f", +{len(new) - 20} more" if len(new) > 20 else "")
                    or "none"))
    for grp in (scores.get("failure_groups") or [])[:8]:
        msg = grp.get("message", "other")
        lines.append(f"  - {grp.get('count', 0)} x {msg}")
    if scores.get("test_precision") is not None:
        pred = scores.get("predicted_failing") or []
        lines += [f"- predicted failing ({len(pred)}): "
                  + (", ".join(f"`{t}`" for t in pred[:20])
                     + (f", +{len(pred) - 20} more" if len(pred) > 20 else "")
                     or "none"),
                  f"- test precision/recall: "
                  f"{scores['test_precision']:.2f}/"
                  f"{scores['test_recall']:.2f}"]
    chain = scores.get("test_chain")
    if chain:
        lines.append(
            f"- example failure: `{chain['test']}` runs `{chain['func']}` "
            f"which uses `{chain['symbol']}` "
            f"(`{chain['file']}:{chain['line']}`)")
    if scores.get("unhealthy_baseline"):
        lines.append("- baseline is unhealthy (>20% failing before the "
                     "decision); treat scores with care")
    collateral = (extra or {}).get("collateral") or {}
    if collateral:
        lines.append("- collateral upgrades: " + ", ".join(
            f"`{n}` {v.get('before')}->{v.get('after')}"
            for n, v in sorted(collateral.items())))
    conflict = (extra or {}).get("conflict")
    if conflict:
        lines += ["", "## Dependency conflict (first domino)", "",
                  f"`{conflict['package']}=={conflict['to_version']}` "
                  "does not resolve against this repo:",
                  "```", conflict.get("detail", ""), "```"]
    return lines + [""]


def _prediction_chain_lines(report: Report, extra: dict | None,
                            decision: bool) -> list[str]:
    """Prediction chain block: import spine, or the test chain instead.

    When the import succeeds (empty spine) but tests fail at call time,
    the empty "Predicted layer-1 chain ... MATCH" lines say nothing, so
    the test call chain leads instead.
    """
    chain = ((extra or {}).get("scores") or {}).get("test_chain")
    if not report.spine_evidence and chain:
        return [
            "Import chain is clean (import succeeds); "
            "worst damage is at call time:",
            "",
            f"- `{chain['test']}` runs `{chain['func']}` "
            f"which uses `{chain['symbol']}` "
            f"(`{chain['file']}:{chain['line']}`)",
            "",
        ]
    return [
        "Predicted call chain:" if decision else "Predicted layer-1 chain:",
        "",
        *[
            f"- `{u}` -> `{v}` ({path}:{line})"
            for u, v, path, line in report.spine_evidence
        ],
        (f"Chain vs decision actual: "
         if decision else "Chain vs layer-1 actual: ")
        + f"{'MATCH' if report.spine_match else 'DIFFERS'}",
        "",
    ]


def interpret_m3_metrics(stats: dict | None) -> dict | None:
    """report.json "interpret_m3" block (§6); None for legacy flows.

    None keeps legacy reports byte-identical: the block, the extra
    key, and the Markdown section all vanish together.
    """
    if not stats or stats.get("survival_rate") is None:
        return None
    det = stats.get("det_by_method") or {}
    return {
        "quote_survival_rate": stats["survival_rate"],
        "blind_recall": stats.get("blind_recall"),
        "guided_recall": stats.get("recall"),
        "deterministic_rows": {m: det.get(m, 0)
                               for m in ("table", "list", "pattern")},
        "model_only": list(stats.get("model_only", [])),
        "completed": stats.get("completed", 0),
        "releases": list(stats.get("releases", [])),
    }


def _interpret_m3_lines(metrics: dict | None) -> list[str]:
    if not metrics:
        return []
    det = metrics["deterministic_rows"]
    blind = metrics["blind_recall"]
    guided = metrics["guided_recall"]
    only = metrics["model_only"]
    return [
        "",
        f"Interpreter metrics: quote survival "
        f"{metrics['quote_survival_rate']:.2f}; blind recall "
        + (f"{blind:.2f}" if blind is not None else "n/a")
        + f" ({det['table']}/{det['list']}/{det['pattern']} "
        "table/list/pattern rows); guided recall "
        + (f"{guided:.2f}" if guided is not None else "n/a")
        + f"; completion added {metrics['completed']}; "
        + (f"model-only finds ({len(only)}): "
           + ", ".join(f"`{s}`" for s in only) + "; "
           if only else "")
        + "releases with notes: "
        + (", ".join(f"`{v}`" for v in metrics["releases"]) or "none")
        + ".",
    ]


def render_markdown(report: Report, extra: dict | None = None) -> str:
    decision = report.probe_kind == "decision"
    target_line = (f"Target: `{report.target}` "
                   "(pre-existing, analyzed by ChainReaction)")
    if not decision:
        numpy_label = report.numpy_version or "unknown"
        target_line += (f" | numpy: {numpy_label}"
                        + (f" (before: {report.before_numpy})"
                           if report.before_numpy else ""))
    lines = [
        f"# ChainReaction report: {report.decision}",
        "",
        target_line,
        "",
        "## Change set",
        "",
        *[f"- `{s}`" for s in report.change_symbols],
        *[f"- `{i.symbol}` ({i.kind}: "
           f"{i.detail.expected} -> {i.detail.actual}"
           + (f"; {i.detail.note}" if i.detail.note else "")
           + ("; derived from probe" if i.source == "probe" else "")
           + (f"; `{i.code_file}:{i.code_line}`"
              if i.source == "code" and getattr(i, "code_file", "") else "")
           + ")"
           if i.detail is not None else f"- `{i.symbol}` ({i.kind}"
           + ("; derived from probe" if i.source == "probe" else "")
           + (f"; `{i.code_file}:{i.code_line}`"
              if i.source == "code" and getattr(i, "code_file", "") else "")
           + ")"
           for i in report.change_items],
        *([] if not report.used_symbols else [
            "",
            f"Used by this repo ({len(report.used_symbols)}): "
            + ", ".join(f"`{s}`" for s in report.used_symbols),
        ]),
        *([] if not report.deprecated_symbols else [
            "",
            "Deprecated (warnings — still work, excluded from scores):",
            "",
            *[f"- `{s}`" for s in report.deprecated_symbols],
        ]),
        *([] if not report.changed_symbols else [
            "",
            "Behavior-change flags (excluded from scores):",
            "",
            *[f"- `{s}`" for s in report.changed_symbols],
        ]),
        *([] if report.interpret_recall is None else [
            "",
            f"Removal-table recall: Muse Spark alone "
            f"{report.interpret_blind_hits}/{report.interpret_table_size} "
            f"→ with deterministic table extraction "
            f"{report.interpret_table_size - len(report.interpret_missed)}"
            f"/{report.interpret_table_size}"
            + (" (complete)" if not report.interpret_missed else
               " (missed: "
               + ", ".join(f"`{m}`" for m in report.interpret_missed) + ")"),
        ]),
        *_interpret_m3_lines((extra or {}).get("interpret_m3")),
        "",
        "## Prediction",
        "",
        f"HARD: {len(report.hard)} modules | AT_RISK: {len(report.at_risk)} nodes",
        "",
        *_prediction_chain_lines(report, extra, decision),
    ]
    if report.pr_url:
        flagged, total, breaking, missed = _file_score_bits(report)
        both = (f"; {flagged}/{breaking} of the breaking changes"
                if report.file_behavior else "")
        lines += [
            "## Ground truth (PR)",
            "",
            f"URL: {report.pr_url}",
            f"Files needing edits: precision {report.file_precision:.2f}, "
            f"recall {report.file_recall:.2f} "
            f"({flagged}/{total} PR files flagged{both})",
            "Missed: "
            + (", ".join(f"`{m}`" for m in missed) or "none"),
            "Behavior changes (flagged, not predicted): "
            + (", ".join(f"`{m}`" for m in report.file_behavior)
               or "none"),
            "",
        ]
    lines += _generic_test_lines(extra or {})
    lines += ["## Verification", ""]
    for layer in report.layers:
        clean = "(probe ok)" if decision else "(import ok)"
        lines += [
            f"### {layer.name} (recall {layer.recall:.2f})",
            "",
            f"Actual: {' -> '.join(f'`{m}`' for m in layer.actual) or clean}",
        ]
        if layer.failure_site:
            lines.append(f"Site: `{layer.failure_site}`")
        if layer.missing:
            lines.append(f"Missed: {', '.join(f'`{m}`' for m in layer.missing)}")
        if layer.error_tail:
            lines += ["```", layer.error_tail, "```"]
        lines.append("")
    lines += [
        "## Baselines",
        "",
        f"- grep: {report.grep_hits} hits",
        *([] if report.ruff_hits is None else [
            f"- ruff NPY201: {report.ruff_hits} hits",
        ]),
        "",
        "## Narrative",
        "",
        report.narrative or "_Pending Muse Spark (MODEL_API_KEY not configured)._",
        "",
        *_narrative_check_lines(report, extra),
    ]
    return "\n".join(lines)


def _narrative_check_lines(report: Report, extra: dict | None = None
                           ) -> list[str]:
    if not report.narrative:
        return ["Claim check: _pending (no narrative yet)._", ""]
    violations = check_narrative(report, extra=extra)
    if not violations:
        return ["Claim check: ✓ clean", ""]
    return ["Claim check: ✗ "
            f"{len(violations)} issue{'s' if len(violations) != 1 else ''}:",
            "", *[f"- {v}" for v in violations], ""]


def render_json(report: Report) -> str:
    return json.dumps(asdict(report), indent=2)


NARRATE_SYSTEM_PROMPT = """\
You are the ChainReaction explainer. Given an upgrade-impact brief,
write a 3-5 paragraph plain-English summary for a maintainer: what
broke, the single verified worst chain of damage (name the modules),
how confident the evidence is, and what to fix first. Write for a
reader who never saw the brief: plain sentences, never field names,
JSON keys, or raw labels like "numpy_version" or "spine_match" — say
"under NumPy 2.0.2" or "matches the observed failure" instead. Refer
to modules by exact name; do not invent modules, quotes, or numbers
beyond the brief. Make no claims about past runs or general reliability
— describe only this brief. Keep it under 300 words."""


def _test_brief_lines(report: Report, extra: dict | None) -> list[str]:
    """Test-suite facts for generic runs; [] without usable scores."""
    scores = (extra or {}).get("scores") or {}
    if not scores or (extra or {}).get("untouched"):
        return []
    if not (extra or {}).get("has_suite", True):
        return ["Test suites: this repo has no test suite."]
    if scores.get("suites") == "skipped-no-coverage":
        return ["Test suites: coverage was unavailable, "
                "so no test-level prediction was scored."]
    before = scores.get("baseline_counts") or {}
    after = scores.get("after_counts") or {}
    fmt = lambda c: ", ".join(f"{v} {k}" for k, v in sorted(c.items())) \
        or "none"
    new = scores.get("actual_new") or []
    lines = [f"Test suites: before ({fmt(before)}); after ({fmt(after)}); "
             f"{len(new)} new failures caused by the decision."]
    if scores.get("test_precision") is not None:
        pred = scores.get("predicted_failing") or []
        lines.append(
            f"Test prediction: {len(pred)} tests predicted to fail, "
            f"precision {scores['test_precision']:.2f}, recall "
            f"{scores['test_recall']:.2f}.")
    groups = scores.get("failure_groups") or []
    if groups:
        lines.append("Failure causes: " + "; ".join(
            f"{g['count']} x {g['message']}" for g in groups[:4]) + ".")
    chain = scores.get("test_chain")
    if chain:
        lines.append(
            f"Example test failure: {chain['test']} runs {chain['func']} "
            f"which uses {chain['symbol']} "
            f"({chain['file']}:{chain['line']}).")
    if not report.spine and new:
        lines.append("The import chain is clean, but tests fail at call "
                     "time via the example above; fix the removed-symbol "
                     "line first.")
    return lines


def narrative_brief(report: Report, extra: dict | None = None) -> str:
    """Human-readable facts for the narrator (no raw JSON keys)."""
    versions = (f"tested under NumPy {report.numpy_version}"
                + (f" (previously {report.before_numpy})"
                   if report.before_numpy else "")
                if report.numpy_version
                else ("previously under NumPy " + report.before_numpy
                      if report.before_numpy else ""))
    target_bit = f"Target: {report.target} (package {report.package})"
    if versions:
        target_bit += f", {versions}"
    lines = [
        f"Decision: {report.decision}.",
        target_bit + ".",
        f"Breaking symbols ({len(report.change_symbols)}): "
        f"{', '.join(report.change_symbols) or 'none'}.",
    ]
    if report.change_symbols or report.used_symbols:
        lines.append(
            f"Removed symbols used by this repo "
            f"({len(report.used_symbols)}): "
            f"{', '.join(report.used_symbols) or 'none'}. "
            "Only these can cause failures here.")
    if report.change_items:
        bits = []
        for item in report.change_items:
            bit = f"{item.symbol} ({item.kind}"
            detail = getattr(item, "detail", None)
            if detail is not None and (detail.expected or detail.actual):
                bit += (f": needs {detail.expected}; "
                        f"code has {detail.actual}")
            if getattr(item, "source", "") == "code" and getattr(
                    item, "code_file", ""):
                bit += f"; {item.code_file}:{item.code_line}"
            bit += ")"
            bits.append(bit)
        lines.append(
            f"Config breaks ({len(report.change_items)}): "
            + "; ".join(bits) + ".")
    if report.deprecated_symbols:
        lines.append(
            f"Deprecated but still working ({len(report.deprecated_symbols)}): "
            f"{', '.join(report.deprecated_symbols)}.")
    if report.changed_symbols:
        lines.append(
            f"Behavior-change flags ({len(report.changed_symbols)}): "
            f"{', '.join(report.changed_symbols)}.")
    fail_word = ("fail at import" if report.probe_kind == "import"
                 else "fail")
    lines.append(
        f"Impact: {len(report.hard)} modules {fail_word}, "
        f"{len(report.at_risk)} more are at risk.")
    lines.append(
        f"Worst chain: {' -> '.join(report.spine) or '(clean)'} "
        f"({'matches' if report.spine_match else 'differs from'} "
        "the observed failure).")
    if report.spine_evidence:
        spine_set = set(report.spine)

        def _hop(u: str, v: str, path: str, line: int) -> str:
            if _code_hop_item(report, path, line) is not None:
                return f"{u} reaches {v} ({path}:{line})"
            external = v != report.package and not v.startswith(
                report.package + ".")
            if external:
                return f"{u} uses {v} ({path}:{line})"
            if u in spine_set and v in spine_set:
                return f"{u} imports {v} ({path}:{line})"
            if u in spine_set:
                return f"{u} executes {v} at import ({path}:{line})"
            return f"{u} calls {v} ({path}:{line})"

        lines.append("Chain evidence: " + "; ".join(
            _hop(u, v, path, line)
            for u, v, path, line in report.spine_evidence) + ".")
    if report.pr_url:
        flagged, total, breaking, missed = _file_score_bits(report)
        both = (f" ({flagged}/{total} overall; {flagged}/{breaking} of "
                "the breaking changes)" if report.file_behavior
                else f" ({flagged}/{total})")
        lines.append(
            f"Ground-truth PR {report.pr_url}: file precision "
            f"{report.file_precision:.2f}, recall "
            f"{report.file_recall:.2f}{both}; "
            f"missed files: {', '.join(missed) or 'none'}.")
        if report.file_behavior:
            lines.append(
                "Behavior-change files (flagged separately, not scored): "
                f"{', '.join(report.file_behavior)}.")
    probe_word = "probe" if report.probe_kind == "decision" else "import"
    lines.append("Verification layers: " + "; ".join(
        f"{layer.name}: {probe_word} "
        f"{'works' if layer.ok else 'fails'}, "
        f"recall {layer.recall:.2f}"
        + (f", missed {', '.join(layer.missing)}" if layer.missing else "")
        for layer in report.layers) + ".")
    order = list(getattr(report, "fix_order", []) or []) or [
        f"{layer.name}: {layer.failure_site}" for layer in report.layers
        if not layer.ok and layer.failure_site]
    if order:
        lines.append("Fix order: " + "; ".join(order) + ".")
        if report.probe_kind == "decision":
            lines.append(
                "Fix each site in order and re-probe: each fix reveals "
                "the next layer.")
        else:
            lines.append(
                "The chain modules above are importers, not edit sites: "
                "fixing each site in order unblocks its chain in one step; "
                "do not edit the importers.")
    lines.extend(_test_brief_lines(report, extra))
    return "\n".join(lines)


def generate_narrative(report: Report, chat_fn=None,
                       extra: dict | None = None) -> str:
    """Summarize a Report with Muse Spark (needs MODEL_API_KEY).

    chat_fn defaults to llm.chat; the run store injects ctx.llm so the
    call is recorded and replayable.
    """
    chat_fn = chat_fn or chat
    return chat_fn(NARRATE_SYSTEM_PROMPT,
                   f"Upgrade-impact brief:\n"
                   f"{narrative_brief(report, extra)}\n",
                   max_tokens=8000)
