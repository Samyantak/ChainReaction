"""The eight run stages (M1).

setup/interpret/predict/verify/report run the real pipeline through
the RunContext gateways; investigate/fix are stubs until M4/M5 and say
so in the event stream. Replay follows the manifest provenance
(changeset_source/narrative_source), never the environment: a replay
with a key still serves the recording, and a replay without one still
works.
"""

import re
import subprocess
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from chainreaction import llm as llm_mod
from chainreaction import scenarios
from chainreaction.baselines import grep_hits, ruff_npy201
from chainreaction.explain import (
    check_narrative,
    generate_narrative,
    render_markdown,
)
from chainreaction.graph.repo import (
    build_graph_from_repo,
    package_dir,
    relative_posix,
)
from chainreaction.interpret import (
    BreakDetail,
    ChangeItem,
    ChangeSet,
    change_symbols,
    changed_symbols,
    config_items,
    deprecated_symbols,
    interpret,
    interpret_with_table,
)
from chainreaction.pipeline import build_layers, build_report
from chainreaction.propagate import failure_path, predicted_chain, propagate
from chainreaction.runs.context import CommitMismatch
from chainreaction.runs.scrub import ROOT
from chainreaction.verifier import run_import_probe, run_snippet_probe

NOT_IN_M1 = "not in M1"


def _git_commit(cwd: Path) -> str | None:
    # Only the target's OWN history counts: without this guard git walks
    # up to the ChainReaction repo and the run records the wrong SHA.
    if not (cwd / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and sha else None


def _artifact_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "probe"


def _env_adapter(ctx, env_name: str, timeout: int = 300):
    """verifier/baseline runner over ctx.run_cmd for a target env.

    Outputs come back unscrubbed so traceback paths map to modules;
    stage_verify re-scrubs before anything is stored.
    """

    def run(cmd, cwd):
        res = ctx.run_cmd(cmd, cwd=cwd, env_name=env_name, timeout=timeout)
        return SimpleNamespace(
            returncode=res.returncode,
            stdout=ctx.scrubber.untext(res.stdout),
            stderr=ctx.scrubber.untext(res.stderr), missing=res.missing)

    return run


def _tool_adapter(ctx, timeout: int = 300):
    """ctx.run_cmd adapter for PATH tools (ruff); env_name=None."""

    def run(cmd, cwd):
        res = ctx.run_cmd(cmd, cwd=cwd, env_name=None, timeout=timeout)
        return SimpleNamespace(
            returncode=res.returncode,
            stdout=ctx.scrubber.untext(res.stdout),
            stderr=ctx.scrubber.untext(res.stderr), missing=res.missing)

    return run


def _commit_matches(live_commit: str, ref: str) -> bool:
    return live_commit == ref or (len(ref) < 40
                                  and live_commit.startswith(ref))


def stage_setup(ctx) -> None:
    preset = ctx.manifest.get("preset")
    try:
        scen = scenarios.get(preset)
    except KeyError:
        raise RuntimeError(f"unknown preset: {preset!r}") from None
    repo_url = ctx.manifest.get("repo_url") or scen.repo_url
    ref = ctx.manifest.get("ref") or scen.ref
    target = Path(scen.target)
    if not (ROOT / target).is_dir():
        if ctx.mode == "replay":
            raise RuntimeError(
                f"target not on disk: {scen.target}; replay needs the "
                "source (run scripts/fetch_targets.py)")
        if not repo_url or not ref:
            raise RuntimeError(
                f"target not on disk: {scen.target} (fetch per README)")
        ctx.emit("log", {"message":
                         f"setup: cloning {repo_url} at {ref[:12]}"})
        ctx.run_cmd(["git", "clone", repo_url, scen.target], cwd=".",
                    env_name=None, timeout=1200)
        ctx.run_cmd(["git", "-C", scen.target, "checkout", ref], cwd=".",
                    env_name=None, timeout=300)
    live_commit = _git_commit(ROOT / target)
    if ref and live_commit and not _commit_matches(live_commit, ref):
        raise CommitMismatch(
            f"target {scen.target} is at {live_commit}, but preset "
            f"{preset} pins {ref}; check out the pinned commit")
    if ref and not live_commit:
        ctx.emit("log", {"message":
                         f"warning: cannot verify the target commit for "
                         f"{scen.target} (no git history)"})
    if ctx.mode == "replay":
        expected = ctx.manifest.get("resolved_commit")
        if expected and live_commit and live_commit != expected:
            raise CommitMismatch(
                f"target {scen.target} is at {live_commit}, but the "
                f"recording needs {expected}; check out the recorded "
                "commit to replay")
        if expected and not live_commit:
            raise CommitMismatch(
                f"cannot verify the target commit for {scen.target} "
                f"(no git HEAD); the recording needs {expected}")
        if not expected:
            ctx.emit("log", {"message":
                             "no recorded commit; skipping target check"})
        else:
            ctx.emit("log", {"message":
                             f"target commit verified: {live_commit[:12]}"})
    elif live_commit:
        ctx.set_manifest(resolved_commit=live_commit, repo_url=repo_url,
                         ref=ref)
    ctx.state["scenario"] = scen
    ctx.emit("log", {"message":
                     f"setup: preset={preset} target={scen.target} "
                     f"commit={live_commit or 'unknown'}"})


def _stub_changeset(decision: str, guide_url: str, scen) -> ChangeSet:
    # Copy: the scenario globals must never be mutated (live and replay
    # runs share this process).
    items = [ChangeItem(symbol=i.symbol, kind=i.kind, quote=i.quote,
                        replacement=i.replacement,
                        detail=(BreakDetail(i.detail.expected,
                                            i.detail.actual, i.detail.note)
                                if i.detail else None),
                        verified=True, source=i.source)
             for i in scen.stub_items]
    return ChangeSet(decision, guide_url, items)


def stage_interpret(ctx) -> None:
    scen = ctx.state["scenario"]
    decision = ctx.manifest.get("decision") or scen.decision
    guide_url = ctx.manifest.get("guide_url") or scen.guide_url
    if ctx.mode == "replay":
        use_model = ctx.manifest.get("changeset_source") == "muse-spark"
    else:
        use_model = llm_mod.has_key() and scen.live_interpret
    if use_model:
        guide_text = ctx.fetch(guide_url)
        changeset, stats = interpret_with_table(
            decision, guide_text, guide_url,
            guide_text.removal_table, chat_fn=ctx.llm)
        ctx.set_manifest(changeset_source="muse-spark")
        ctx.state["interpret_stats"] = stats
        symbols = sorted(i.symbol for i in changeset.items)
        recall = (f"; removal-table recall {stats['recall']:.2f} "
                  f"({stats['table_size'] - len(stats['missed'])}"
                  f"/{stats['table_size']})"
                  if stats["recall"] is not None else "")
        ctx.emit("log", {"message":
                         f"interpret: {len(changeset.items)} items "
                         f"({len(stats['missed'])} table-completed)"
                         f"{recall}: {', '.join(symbols) or '(none)'}"})
        if stats["quote_misses"]:
            ctx.emit("log", {"message":
                             "warning: table quotes absent from cleaned "
                             f"text: {', '.join(stats['quote_misses'])}"})
    else:
        changeset = _stub_changeset(decision, guide_url, scen)
        ctx.set_manifest(changeset_source="hand-recorded")
        ctx.state["interpret_stats"] = {"recall": None, "missed": [],
                                        "table_size": 0, "quote_misses": []}
        if ctx.mode == "live" and not scen.live_interpret:
            reason = (f"scenario {scen.id} pins the hand-recorded set "
                      "(code-grounded; live interpreter is guide-only)")
        else:
            reason = ("no MODEL_API_KEY; hand-recorded change set "
                      "(see manifest changeset_source)")
        ctx.emit("log", {"message": f"interpret: {reason}"})
    symbols = change_symbols(changeset)
    ctx.state["changeset"] = changeset
    ctx.state["symbols"] = symbols
    ctx.state["config"] = config_items(changeset)
    ctx.state["deprecated"] = deprecated_symbols(changeset)
    ctx.state["changed"] = changed_symbols(changeset)
    stats = ctx.state["interpret_stats"]
    ctx.save_artifact("changeset.json", {
        "decision": changeset.decision, "source_url": changeset.source_url,
        "changeset_source": ctx.manifest.get("changeset_source"),
        "interpret_recall": stats["recall"],
        "interpret_table_size": stats["table_size"],
        "interpret_missed": stats["missed"],
        "items": [asdict(i) for i in changeset.items],
    })


def stage_investigate(ctx) -> None:
    ctx.emit("log", {"message":
                     f"investigator agent arrives in M4 ({NOT_IN_M1})"})


def stage_predict(ctx) -> None:
    scen = ctx.state["scenario"]
    symbols = ctx.state["symbols"]
    config = ctx.state["config"]
    g = build_graph_from_repo(scen.target, scen.package)
    impacts = propagate(g, symbols, config, scen.package)
    hard = sorted(n for n, i in impacts.items() if i.status == "HARD")
    at_risk = sorted(n for n, i in impacts.items() if i.status == "AT_RISK")
    spine_evidence = [
        (u, v, relative_posix(path, scen.target), line)
        for u, v, path, line in failure_path(g, impacts, symbols,
                                             scen.package)
    ]
    spine = predicted_chain(g, spine_evidence)
    ctx.state["graph"] = g
    ctx.state["impacts"] = impacts
    ctx.state["hard"] = hard
    ctx.state["at_risk"] = at_risk
    ctx.save_artifact("prediction.json", {
        "hard": hard, "at_risk": at_risk, "spine": spine,
        "spine_evidence": spine_evidence,
        "change_symbols": sorted(symbols),
        "deprecated_symbols": sorted(ctx.state["deprecated"]),
        "changed_symbols": sorted(ctx.state["changed"]),
    })
    ctx.emit("log", {"message":
                     f"predict: HARD={len(hard)} AT_RISK={len(at_risk)} "
                     f"spine={' -> '.join(spine) or '(clean)'}"})


def stage_verify(ctx) -> None:
    scen = ctx.state["scenario"]
    verify_py = ctx.env_python(scen.verify_env)
    run = _env_adapter(ctx, scen.verify_env)
    probes = []
    if scen.probe_code:
        probes.append(("decision", run_snippet_probe(
            verify_py, scen.target, scen.probe_code, scen.package,
            run=run)))
    else:
        probes.append(("layer1", run_import_probe(
            verify_py, scen.target, scen.package, run=run)))
        if scen.peel_preamble:
            probes.append(("layer2 (peeled)", run_import_probe(
                verify_py, scen.target, scen.package,
                preamble=scen.peel_preamble, run=run)))
    if scen.before_env:
        before_py = ctx.env_python(scen.before_env)
        probes.append((f"before ({scen.before_env})", run_import_probe(
            before_py, scen.target, scen.package,
            run=_env_adapter(ctx, scen.before_env))))
    # Probes parsed unscrubbed text (module mapping needs real paths);
    # re-scrub before storing so error tails stay canonical. Live and
    # replay land on identical bytes: scrub(unscrub(recorded)) == recorded.
    # Recordings pin the scrubber version: re-record presets after any
    # scrubber change (the byte-compare test enforces it).
    for _, probe in probes:
        probe.stderr = ctx.scrubber.text(probe.stderr)
    for name, probe in probes:
        ctx.save_artifact(
            f"verify/{_artifact_slug(name)}.log",
            f"# probe: {name}\n# ok: {probe.ok}\n"
            f"# numpy: {probe.numpy_version}\n"
            f"# modules: {' -> '.join(probe.modules) or '(none)'}\n"
            f"## stderr\n{probe.stderr}")
    ctx.state["probes"] = probes
    ctx.emit("log", {"message":
                     f"verify: {len(probes)} probe(s): "
                     + ", ".join(f"{n}={'ok' if p.ok else 'FAILED'}"
                                 for n, p in probes)})


def stage_fix(ctx) -> None:
    ctx.emit("log", {"message": f"fix loop arrives in M5 ({NOT_IN_M1})"})


def stage_report(ctx) -> None:
    scen = ctx.state["scenario"]
    changeset = ctx.state["changeset"]
    symbols = ctx.state["symbols"]
    g = ctx.state["graph"]
    impacts = ctx.state["impacts"]
    probes = ctx.state["probes"]
    flagged = set(ctx.state["hard"]) | set(ctx.state["at_risk"])
    layers, before_numpy = build_layers(scen, probes, flagged)
    pkgdir = package_dir(scen.target, scen.package)
    grep_count = len(grep_hits(pkgdir, scen.grep_patterns))
    if scen.run_ruff:
        ruff = ruff_npy201(relative_posix(pkgdir, "."), run=_tool_adapter(ctx))
        ruff_count = len(ruff) if ruff is not None else None
    else:
        ruff_count = None
    report = build_report(scen, changeset, symbols, g, impacts, layers,
                          probes[0][1].numpy_version, before_numpy,
                          grep_count, ruff_count,
                          ctx.state.get("interpret_stats"))
    if ctx.mode == "replay":
        want_narrative = (ctx.manifest.get("narrative_source")
                          == "muse-spark")
    else:
        want_narrative = llm_mod.has_key()
    if want_narrative:
        report.narrative = generate_narrative(report, chat_fn=ctx.llm)
        ctx.set_manifest(narrative_source="muse-spark")
        violations = check_narrative(report)
        if violations:
            shown = "; ".join(violations[:5])
            ctx.emit("log", {"message":
                             f"narrative: {len(violations)} claim issue(s): "
                             f"{shown}"})
        else:
            ctx.emit("log", {"message": "narrative: claim check clean"})
    else:
        if ctx.mode == "live":
            ctx.set_manifest(narrative_source="skipped-no-key")
            ctx.emit("log", {"message":
                             "narrative: skipped (no MODEL_API_KEY)"})
        else:
            ctx.emit("log", {"message":
                             "narrative: skipped (not in recording)"})
    ctx.save_artifact("report.json", asdict(report))
    ctx.save_artifact("report.md", render_markdown(report))
    file_stats = ""
    if report.file_precision is not None:
        file_stats = (f"; file P/R "
                      f"{report.file_precision:.2f}/{report.file_recall:.2f}")
    ctx.emit("log", {"message":
                     f"report: chain "
                     f"{'MATCH' if report.spine_match else 'DIFFERS'}"
                     f"{file_stats}"})


STAGES = [
    ("setup", stage_setup),
    ("interpret", stage_interpret),
    ("investigate", stage_investigate),
    ("predict", stage_predict),
    ("verify", stage_verify),
    ("fix", stage_fix),
    ("report", stage_report),
]
