"""Key-free pipeline runner: scenario -> graph -> impacts -> report.

Shared by the demo scripts and the web backend so both serve identical
results. Live guide fetching is opt-in (needs network).
"""

from dataclasses import dataclass

import networkx as nx

from chainreaction import scenarios
from chainreaction.baselines import grep_hits, ruff_npy201
from chainreaction.explain import LayerResult, Report
from chainreaction.graph.repo import (
    build_graph_from_repo,
    package_dir,
    relative_posix,
)
from chainreaction.interpret import (
    CONFIG_KINDS,
    ChangeSet,
    change_symbols,
    changed_symbols,
    config_items,
    deprecated_symbols,
    fetch_text,
    interpret,
    verify_quotes,
)
from chainreaction.propagate import Impact, failure_path, predicted_chain, propagate
from chainreaction.scorer import chain_match, direct_use_files, precision_recall
from chainreaction.verifier import (
    run_import_probe,
    run_snippet_probe,
    target_python,
)


@dataclass
class PipelineResult:
    graph: nx.DiGraph
    impacts: dict[str, Impact]
    report: Report
    symbols: set[str]


def predict(scenario_id: str, fetch_guide: bool = False,
            guide_text: str | None = None, live_interpret: bool = False):
    """Scenario -> change set -> graph -> impacts. No probes run."""
    scen = scenarios.get(scenario_id)
    if live_interpret:
        text = (guide_text if guide_text is not None
                else fetch_text(scen.guide_url))
        changeset = interpret(scen.decision, text, scen.guide_url)
        symbols = change_symbols(changeset)
        g = build_graph_from_repo(scen.target, scen.package)
        impacts = propagate(g, symbols, config_items(changeset), scen.package)
        return scen, changeset, symbols, g, impacts
    changeset = ChangeSet(scen.decision, scen.guide_url,
                          [i for i in scen.stub_items])
    if fetch_guide:
        text = guide_text if guide_text is not None else fetch_text(scen.guide_url)
        changeset.items, _ = verify_quotes(changeset.items, text)
    else:
        for item in changeset.items:
            item.verified = True  # offline: trust the recorded quotes
    symbols = change_symbols(changeset)

    g = build_graph_from_repo(scen.target, scen.package)
    impacts = propagate(g, symbols, config_items(changeset), scen.package)
    return scen, changeset, symbols, g, impacts


def build_layers(scen, probes, flagged: set):
    """Probe results -> per-layer recall rows (+ before numpy version)."""
    layers = []
    for name, probe in probes:
        missing = [m for m in probe.modules if m not in flagged]
        recall = 1.0 - len(missing) / len(probe.modules) if probe.modules else 1.0
        layers.append(LayerResult(
            name, probe.modules, missing, recall,
            "\n".join(probe.stderr.splitlines()[-4:]) if probe.stderr else "",
            probe.ok,
        ))
    before_numpy = probes[-1][1].numpy_version if scen.before_env else None
    return layers, before_numpy


def build_report(scen, changeset: ChangeSet, symbols: set, g, impacts: dict,
                 layers: list, numpy_version, before_numpy,
                 grep_count: int, ruff_count, interpret_stats=None):
    """Assemble the deterministic Report from prediction + verification."""
    interpret_stats = interpret_stats or {}
    hard = {n for n, i in impacts.items() if i.status == "HARD"}
    risky = {n for n, i in impacts.items() if i.status == "AT_RISK"}
    spine_evidence = [
        (u, v, relative_posix(path, scen.target), line)
        for u, v, path, line in failure_path(g, impacts, symbols, scen.package)
    ]
    predicted = predicted_chain(g, spine_evidence)
    score = chain_match(predicted, layers[0].actual)
    file_score = (precision_recall(direct_use_files(g, symbols, scen.target),
                                   set(scen.pr_files))
                  if scen.pr_files else None)
    file_missing = (sorted(file_score.actual - file_score.predicted)
                    if file_score else [])
    return Report(
        decision=changeset.decision, target=scen.target, package=scen.package,
        probe_kind="decision" if scen.probe_code else "import",
        numpy_version=numpy_version,
        before_numpy=before_numpy,
        change_symbols=sorted(symbols),
        deprecated_symbols=sorted(deprecated_symbols(changeset)),
        changed_symbols=sorted(changed_symbols(changeset)),
        interpret_recall=interpret_stats.get("recall"),
        interpret_table_size=interpret_stats.get("table_size", 0),
        interpret_missed=list(interpret_stats.get("missed", [])),
        change_items=[i for i in changeset.items
                      if i.kind in CONFIG_KINDS],
        hard=sorted(hard), at_risk=sorted(risky),
        spine=predicted, spine_evidence=spine_evidence,
        spine_match=score.match,
        pr_url=scen.pr_url, pr_files=list(scen.pr_files),
        file_precision=file_score.precision if file_score else None,
        file_recall=file_score.recall if file_score else None,
        file_missing=file_missing,
        file_behavior=sorted(set(scen.pr_behavior) & set(file_missing)),
        layers=layers, grep_hits=grep_count,
        ruff_hits=ruff_count,
    )


def run_pipeline(scenario_id: str, fetch_guide: bool = False,
                 guide_text: str | None = None,
                 live_interpret: bool = False) -> PipelineResult:
    scen, changeset, symbols, g, impacts = predict(
        scenario_id, fetch_guide, guide_text, live_interpret)
    hard = {n for n, i in impacts.items() if i.status == "HARD"}
    risky = {n for n, i in impacts.items() if i.status == "AT_RISK"}
    flagged = hard | risky
    verify_py = target_python(scen.verify_env)
    if scen.probe_code:
        probes = [("decision",
                   run_snippet_probe(verify_py, scen.target, scen.probe_code,
                                     scen.package))]
    else:
        probes = [("layer1",
                   run_import_probe(verify_py, scen.target, scen.package))]
        if scen.peel_preamble:
            probes.append(("layer2 (peeled)",
                           run_import_probe(verify_py, scen.target,
                                            scen.package,
                                            preamble=scen.peel_preamble)))
    if scen.before_env:
        before = run_import_probe(target_python(scen.before_env),
                                  scen.target, scen.package)
        probes.append((f"before ({scen.before_env})", before))
    layers, before_numpy = build_layers(scen, probes, flagged)

    pkgdir = package_dir(scen.target, scen.package)
    hits = grep_hits(pkgdir, scen.grep_patterns)
    ruff = ruff_npy201(pkgdir) if scen.run_ruff else None
    report = build_report(scen, changeset, symbols, g, impacts, layers,
                          probes[0][1].numpy_version, before_numpy,
                          len(hits),
                          len(ruff) if ruff is not None else None)
    return PipelineResult(g, impacts, report, symbols)
