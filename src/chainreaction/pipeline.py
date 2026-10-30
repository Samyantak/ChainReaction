"""Key-free pipeline runner: scenario -> graph -> impacts -> report.

Shared by the demo scripts and the web backend so both serve identical
results. Live guide fetching is opt-in (needs network).
"""

from dataclasses import dataclass

import networkx as nx

from chainreaction import scenarios
from chainreaction.baselines import grep_hits, ruff_npy201
from chainreaction.explain import LayerResult, Report
from chainreaction.graph.repo import build_graph_from_repo
from chainreaction.interpret import (
    ChangeSet,
    change_symbols,
    fetch_text,
    verify_quotes,
)
from chainreaction.propagate import Impact, failure_path, predicted_chain, propagate
from chainreaction.scorer import chain_match
from chainreaction.verifier import run_import_probe, target_python


@dataclass
class PipelineResult:
    graph: nx.DiGraph
    impacts: dict[str, Impact]
    report: Report
    symbols: set[str]


def run_pipeline(scenario_id: str, fetch_guide: bool = False) -> PipelineResult:
    scen = scenarios.get(scenario_id)
    changeset = ChangeSet(scen.decision, scen.guide_url,
                          [i for i in scen.stub_items])
    if fetch_guide:
        text = fetch_text(scen.guide_url)
        changeset.items, _ = verify_quotes(changeset.items, text)
    else:
        for item in changeset.items:
            item.verified = True  # stub: quotes recorded by hand
    symbols = change_symbols(changeset)

    g = build_graph_from_repo(scen.target, scen.package)
    impacts = propagate(g, symbols)
    hard = {n for n, i in impacts.items() if i.status == "HARD"}
    risky = {n for n, i in impacts.items() if i.status == "AT_RISK"}

    spine_evidence = failure_path(g, impacts, symbols, scen.package)
    predicted = predicted_chain(g, spine_evidence)

    after = target_python("numpy2")
    probes = [
        ("layer1", run_import_probe(after, scen.target, scen.package)),
        ("layer2 (peeled)", run_import_probe(after, scen.target,
                                             scen.package,
                                             preamble=scen.peel_preamble)),
        ("before (numpy 1.26)", run_import_probe(target_python("numpy1"),
                                                 scen.target, scen.package)),
    ]
    layers = []
    for name, probe in probes:
        missing = [m for m in probe.modules if m not in hard]
        recall = 1.0 - len(missing) / len(probe.modules) if probe.modules else 1.0
        layers.append(LayerResult(
            name, probe.modules, missing, recall,
            "\n".join(probe.stderr.splitlines()[-4:]) if probe.stderr else "",
            probe.ok,
        ))

    hits = grep_hits(f"{scen.target}/{scen.package}")
    ruff = ruff_npy201(f"{scen.target}/{scen.package}")
    score = chain_match(predicted, layers[0].actual)
    report = Report(
        decision=changeset.decision, target=scen.target, package=scen.package,
        numpy_version=probes[0][1].numpy_version,
        before_numpy=probes[2][1].numpy_version,
        change_symbols=sorted(symbols),
        hard=sorted(hard), at_risk=sorted(risky),
        spine=predicted, spine_evidence=spine_evidence,
        spine_match=score.match,
        layers=layers, grep_hits=len(hits),
        ruff_hits=len(ruff) if ruff is not None else None,
    )
    return PipelineResult(g, impacts, report, symbols)
