"""Key-free pipeline runner: scenario -> graph -> impacts -> report.

Shared by the demo scripts and the web backend so both serve identical
results. Live guide fetching is opt-in (needs network).
"""

from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from chainreaction import scenarios
from chainreaction.baselines import (
    grep_hits,
    patterns_for_changeset,
    ruff_npy201,
)
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
    interpret_vendor,
    seed_exclusions,
    signature_seeds,
    verify_all,
)
from chainreaction.propagate import (
    Impact,
    failure_path,
    order_config_by_entry,
    predicted_chain,
    probe_entries,
    propagate,
    vendor_chain,
)
from chainreaction.scorer import (
    chain_match,
    direct_use_files,
    precision_recall,
    used_breaking_symbols,
)
from chainreaction.verifier import (
    run_import_probe,
    run_snippet_probe,
    site_packages_subpath,
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
        if scen.code_aware:
            changeset = interpret_vendor(
                scen.decision, text, scen.target, scen.package,
                scen.guide_url)
        else:
            changeset = interpret(scen.decision, text, scen.guide_url)
        symbols = change_symbols(changeset)
        g = build_graph_from_repo(scen.target, scen.package)
        cfg = config_items(changeset)
        entries = probe_entries(scen.probe_code or "", scen.package, g)
        impacts = propagate(g, symbols, cfg, scen.package,
                            entries=tuple(entries),
                            excluded=seed_exclusions(changeset),
                            sig_seeds=signature_seeds(changeset))
        return scen, changeset, symbols, g, impacts
    changeset = ChangeSet(scen.decision, scen.guide_url,
                          [i for i in scen.stub_items])
    if fetch_guide:
        text = guide_text if guide_text is not None else fetch_text(scen.guide_url)
        changeset.items, _ = verify_all(changeset.items, text, scen.target,
                                         scen.package)
    else:
        for item in changeset.items:
            item.verified = True  # offline: trust the recorded quotes
    symbols = change_symbols(changeset)

    g = build_graph_from_repo(scen.target, scen.package)
    cfg = config_items(changeset)
    entries = probe_entries(scen.probe_code or "", scen.package, g)
    impacts = propagate(g, symbols, cfg, scen.package,
                        entries=tuple(entries),
                        excluded=seed_exclusions(changeset),
                        sig_seeds=signature_seeds(changeset))
    return scen, changeset, symbols, g, impacts


def spine_for_scenario(g, impacts, symbols: set, scen, config,
                       excluded=None, sig_seeds=None) -> list:
    """Predicted failure hops: vendor call path or import chain.

    Decision probes with known entries predict the call path from the
    probe entry to the first rejecting site; everything else (and any
    vendor case without code grounding) falls back to the import-time
    failure path. Shared by stage_predict and build_report so the two
    never disagree.
    """
    probe_code = getattr(scen, "probe_code", "")
    if probe_code:
        entries = probe_entries(probe_code, scen.package, g)
        if entries:
            ordered = order_config_by_entry(g, entries, list(config),
                                            scen.package)
            hops = vendor_chain(g, scen.package, entries, ordered)
            if hops:
                return [(u, v, relative_posix(path, scen.target), line)
                        for u, v, path, line in hops]
    return [(u, v, relative_posix(path, scen.target), line)
            for u, v, path, line in failure_path(g, impacts, symbols,
                                                 scen.package, excluded,
                                                 sig_seeds)]


def _failure_site(frames, root: str | Path, package: str = ""
                ) -> str | None:
    """Deepest target frame of a traceback, as repo-relative "path:line".

    The probe's cwd is the target root, so frames under it relativize
    cleanly (nibabel/casting.py:717); frames in the env's installed
    copy (site-packages/<package>/) map back through the package dir.
    Foreign env frames and <string> never match and are skipped. None
    when the probe passed or no frame maps back.
    """
    try:
        root_abs = Path(root).resolve()
    except OSError:
        return None
    for frame in reversed(frames or []):
        if not str(frame.file).endswith(".py"):
            continue
        try:
            rel = Path(frame.file).resolve().relative_to(root_abs)
        except (ValueError, OSError):
            rel = None
        if rel is not None:
            return f"{rel.as_posix()}:{frame.line}"
        sub = site_packages_subpath(frame.file, package)
        if sub is not None:
            return f"{relative_posix(package_dir(root, package) / sub, root)}:{frame.line}"
    return None


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
            _failure_site(getattr(probe, "frames", None), scen.target,
                          scen.package),
        ))
    before_numpy = probes[-1][1].numpy_version if scen.before_env else None
    return layers, before_numpy


def build_report(scen, changeset: ChangeSet, symbols: set, g, impacts: dict,
                 layers: list, numpy_version, before_numpy,
                 grep_count: int, ruff_count, interpret_stats=None,
                 decision_package: str = "",
                 before_version: str | None = None,
                 after_version: str | None = None,
                 fix: dict | None = None):
    """Assemble the deterministic Report from prediction + verification."""
    interpret_stats = interpret_stats or {}
    hard = {n for n, i in impacts.items() if i.status == "HARD"}
    risky = {n for n, i in impacts.items() if i.status == "AT_RISK"}
    cfg = [i for i in changeset.items
           if i.kind in CONFIG_KINDS and i.verified]
    spine_evidence = spine_for_scenario(g, impacts, symbols, scen, cfg,
                                        seed_exclusions(changeset),
                                        signature_seeds(changeset))
    predicted = predicted_chain(g, spine_evidence)
    layer_fixes = [f"{layer.name}: {layer.failure_site}" for layer in layers
                   if not layer.ok and layer.failure_site]
    if getattr(scen, "probe_code", ""):
        entries = probe_entries(scen.probe_code, scen.package, g)
        ordered = (order_config_by_entry(g, entries, cfg, scen.package)
                   if entries else list(cfg))
        fix_order = [
            f"{item.symbol}: {item.code_file}:{item.code_line}"
            for item in ordered
            if getattr(item, "source", "") == "code"
            and getattr(item, "code_file", "")
            and getattr(item, "code_line", 0)
        ] + layer_fixes
    else:
        fix_order = layer_fixes
    score = chain_match(predicted, layers[0].actual,
                        failed=not layers[0].ok)
    if (decision_package or "").lower() == "numpy":
        numpy_version, before_numpy = after_version, before_version
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
        decision_package=decision_package or "",
        before_version=before_version,
        after_version=after_version,
        change_symbols=sorted(symbols),
        used_symbols=sorted(used_breaking_symbols(g, symbols)),
        deprecated_symbols=sorted(deprecated_symbols(changeset)),
        changed_symbols=sorted(changed_symbols(changeset)),
        interpret_recall=interpret_stats.get("recall"),
        interpret_table_size=interpret_stats.get("table_size", 0),
        interpret_missed=list(interpret_stats.get("missed", [])),
        interpret_blind_hits=interpret_stats.get("blind_hits", 0),
        interpret_blind_recall=interpret_stats.get("blind_recall"),
        change_items=[i for i in changeset.items
                      if i.kind in CONFIG_KINDS],
        hard=sorted(hard), at_risk=sorted(risky),
        spine=predicted, spine_evidence=spine_evidence,
        spine_match=score.match, fix_order=fix_order,
        pr_url=scen.pr_url, pr_files=list(scen.pr_files),
        file_precision=file_score.precision if file_score else None,
        file_recall=file_score.recall if file_score else None,
        file_missing=file_missing,
        file_behavior=sorted(set(scen.pr_behavior) & set(file_missing)),
        layers=layers, grep_hits=grep_count,
        ruff_hits=ruff_count, fix=dict(fix or {}),
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
    hits = grep_hits(pkgdir, patterns_for_changeset(changeset))
    ruff = ruff_npy201(pkgdir) if scen.run_ruff else None
    report = build_report(scen, changeset, symbols, g, impacts, layers,
                          probes[0][1].numpy_version, before_numpy,
                          len(hits),
                          len(ruff) if ruff is not None else None)
    return PipelineResult(g, impacts, report, symbols)
