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
from chainreaction.baselines import (
    grep_hits,
    patterns_for_changeset,
    ruff_npy201,
)
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
    drop_out_of_package_items,
    interpret_vendor,
    interpret_with_table,
)
from chainreaction.pipeline import build_layers, build_report, spine_for_scenario
from chainreaction.propagate import predicted_chain, probe_entries, propagate
from chainreaction.scorer import used_breaking_symbols
from chainreaction.runs.context import CommitMismatch, ReplayMiss
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

    def run(cmd, cwd, extra_env=None):
        res = ctx.run_cmd(cmd, cwd=cwd, env_name=env_name, timeout=timeout,
                          extra_env=extra_env)
        return SimpleNamespace(
            returncode=res.returncode,
            stdout=ctx.scrubber.untext(res.stdout),
            stderr=ctx.scrubber.untext(res.stderr), missing=res.missing,
            timed_out=res.timed_out)

    return run


def _tool_adapter(ctx, tool: str = "ruff", timeout: int = 300):
    """ctx.run_cmd adapter for PATH tools; recorded under <tool:name>."""

    def run(cmd, cwd):
        res = ctx.run_cmd(cmd, cwd=cwd, env_name=None, timeout=timeout,
                          tool=tool)
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
    if preset:
        _stage_setup_preset(ctx, preset)
    else:
        _stage_setup_generic(ctx)


def _stage_setup_generic(ctx) -> None:
    from chainreaction.generic import (
        build_envs,
        detect_project,
        ensure_workspace,
        project_record,
        resolve_decision,
    )
    from chainreaction.runs.scrub import Scrubber
    from chainreaction.scenarios import Scenario

    repo_url = ctx.manifest.get("repo_url") or ""
    ref = ctx.manifest.get("ref") or ""
    decision_text = ctx.manifest.get("decision") or ""
    if not repo_url or not decision_text:
        raise RuntimeError("generic runs need repo_url and decision")
    ws_rel, sha, commit_date = ensure_workspace(ctx, repo_url, ref)
    info = detect_project(ROOT / ws_rel,
                          ctx.manifest.get("package") or "")
    ctx.save_artifact("project.json", project_record(
        info, repo_url=repo_url, ref=ref, resolved_commit=sha,
        commit_date=commit_date, workspace=ws_rel))
    ctx.state["project"] = info
    ctx.state["workspace"] = ws_rel
    key = ws_rel.removeprefix("workspaces/")
    parsed = resolve_decision(ctx, decision_text)
    ctx.state["decision_parsed"] = parsed
    scen = Scenario(
        id=f"generic:{key}", decision=decision_text, target=ws_rel,
        package=info.package, guide_url=ctx.manifest.get("guide_url") or "",
        verify_env=f"{key}/after", before_env=f"{key}/before",
        run_ruff=(parsed.package.lower() == "numpy"),
        repo_url=repo_url, ref=ref)
    ctx.state["scenario"] = scen
    build_envs(ctx, key=key, ws_rel=ws_rel, info=info, decision=parsed,
               commit_date=commit_date, sha=sha)
    # Workspaces and envs did not exist when the run's scrubber was
    # built: refresh so <ws> placeholders map back for traceback
    # parsing (recorded bytes are unchanged — the regexes agree).
    ctx.scrubber = Scrubber.default()
    ctx.set_manifest(detected_package=info.package,
                     decision_package=parsed.package,
                     decision_version=parsed.to_version)
    ctx.emit("log", {"message":
                     f"setup: generic target={ws_rel} package={info.package} "
                     f"commit={sha[:12]} date={commit_date or 'unknown'} "
                     f"suite={'yes' if info.has_suite else 'no'} "
                     f"decision={parsed.package}=={parsed.to_version}"})


def _stage_setup_preset(ctx, preset: str) -> None:
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
                        verified=True, source=i.source,
                        code_quote=i.code_quote, code_file=i.code_file,
                        code_line=i.code_line)
             for i in scen.stub_items]
    return ChangeSet(decision, guide_url, items)


def _stage_interpret_generic(ctx) -> None:
    from chainreaction.generic import discover_guide

    scen = ctx.state["scenario"]
    parsed = ctx.state["decision_parsed"]
    decision = ctx.manifest.get("decision") or scen.decision
    if ctx.mode == "replay":
        use_model = ctx.manifest.get("changeset_source") == "muse-spark"
    else:
        use_model = llm_mod.has_key()
    if not use_model:
        raise RuntimeError(
            "generic runs need MODEL_API_KEY for live interpretation "
            "(only presets have hand-recorded fallbacks)")
    guide_text, guide_url, method = discover_guide(
        ctx, package=parsed.package, to_version=parsed.to_version)
    changeset, stats = interpret_with_table(
        decision, guide_text, guide_url, guide_text.removal_table,
        chat_fn=ctx.llm)
    changeset, dropped = drop_out_of_package_items(changeset, scen.package)
    ctx.set_manifest(changeset_source="muse-spark", guide_url=guide_url)
    ctx.state["interpret_stats"] = stats
    symbols = sorted(i.symbol for i in changeset.items)
    ctx.emit("log", {"message":
                     f"interpret: guide via {method} ({guide_url}); "
                     f"{len(changeset.items)} items: "
                     f"{', '.join(symbols) or '(none)'}"})
    if dropped:
        ctx.emit("log", {"message":
                         f"warning: dropped {len(dropped)} model "
                         f"symbols outside package {scen.package}: "
                         f"{', '.join(dropped)}"})
    ctx.state["changeset"] = changeset
    ctx.state["symbols"] = change_symbols(changeset)
    ctx.state["config"] = config_items(changeset)
    ctx.state["deprecated"] = deprecated_symbols(changeset)
    ctx.state["changed"] = changed_symbols(changeset)
    ctx.save_artifact("changeset.json", {
        "decision": changeset.decision, "source_url": changeset.source_url,
        "changeset_source": ctx.manifest.get("changeset_source"),
        "guide_method": method,
        "interpret_recall": stats["recall"],
        "interpret_table_size": stats["table_size"],
        "interpret_missed": stats["missed"],
        "interpret_blind_hits": stats["blind_hits"],
        "interpret_blind_recall": stats["blind_recall"],
        "dropped_symbols": dropped,
        "items": [asdict(i) for i in changeset.items],
    })


def stage_interpret(ctx) -> None:
    if ctx.manifest.get("preset") is None:
        _stage_interpret_generic(ctx)
        return
    scen = ctx.state["scenario"]
    decision = ctx.manifest.get("decision") or scen.decision
    guide_url = ctx.manifest.get("guide_url") or scen.guide_url
    if ctx.mode == "replay":
        use_model = ctx.manifest.get("changeset_source") == "muse-spark"
    else:
        use_model = llm_mod.has_key()
    dropped: list[str] = []
    if use_model:
        guide_text = ctx.fetch(guide_url)
        if scen.code_aware:
            changeset = interpret_vendor(
                decision, guide_text, scen.target, scen.package,
                guide_url, chat_fn=ctx.llm)
            changeset, dropped = drop_out_of_package_items(
                changeset, scen.package)
            stats = {"recall": None, "missed": [], "table_size": 0,
                     "quote_misses": [], "blind_hits": 0,
                     "blind_recall": None}
            ctx.set_manifest(changeset_source="muse-spark")
            ctx.state["interpret_stats"] = stats
            symbols = sorted(i.symbol for i in changeset.items)
            ctx.emit("log", {"message":
                             f"interpret: code-aware, {len(changeset.items)} "
                             f"items: {', '.join(symbols) or '(none)'}"})
        else:
            changeset, stats = interpret_with_table(
                decision, guide_text, guide_url,
                guide_text.removal_table, chat_fn=ctx.llm)
            changeset, dropped = drop_out_of_package_items(
                changeset, scen.package)
            ctx.set_manifest(changeset_source="muse-spark")
            ctx.state["interpret_stats"] = stats
            symbols = sorted(i.symbol for i in changeset.items)
            recall = (f"; Muse Spark alone: {stats['blind_hits']}"
                      f"/{stats['table_size']} → with table extraction: "
                      f"{stats['table_size'] - len(stats['missed'])}"
                      f"/{stats['table_size']}"
                      if stats["recall"] is not None else "")
            ctx.emit("log", {"message":
                             f"interpret: {len(changeset.items)} items "
                             f"({len(stats['missed'])} table-completed)"
                             f"{recall}: {', '.join(symbols) or '(none)'}"})
            if stats["quote_misses"]:
                ctx.emit("log", {"message":
                                 "warning: table quotes absent from cleaned "
                                 f"text: {', '.join(stats['quote_misses'])}"})
        if dropped:
            ctx.emit("log", {"message":
                             f"warning: dropped {len(dropped)} model "
                             f"symbols outside package {scen.package}: "
                             f"{', '.join(dropped)}"})
    else:
        changeset = _stub_changeset(decision, guide_url, scen)
        ctx.set_manifest(changeset_source="hand-recorded")
        ctx.state["interpret_stats"] = {"recall": None, "missed": [],
                                        "table_size": 0, "quote_misses": [],
                                        "blind_hits": 0, "blind_recall": None}
        ctx.emit("log", {"message":
                         "interpret: no MODEL_API_KEY; hand-recorded "
                         "change set (see manifest changeset_source)"})
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
        "interpret_blind_hits": stats["blind_hits"],
        "interpret_blind_recall": stats["blind_recall"],
        "dropped_symbols": dropped,
        "items": [asdict(i) for i in changeset.items],
    })


def stage_investigate(ctx) -> None:
    ctx.emit("log", {"message":
                     f"investigator agent arrives in M4 ({NOT_IN_M1})"})


def _stage_predict_untouched(ctx, scen) -> None:
    symbols = ctx.state["symbols"]
    ctx.state["graph"] = None
    ctx.state["impacts"] = {}
    ctx.state["hard"] = []
    ctx.state["at_risk"] = []
    ctx.state["untouched"] = True
    ctx.save_artifact("prediction.json", {
        "hard": [], "at_risk": [], "spine": [], "spine_evidence": [],
        "change_symbols": sorted(symbols),
        "used_symbols": [],
        "deprecated_symbols": sorted(ctx.state["deprecated"]),
        "changed_symbols": sorted(ctx.state["changed"]),
        "probe_entries": [],
        "untouched_warning": (
            f"this decision doesn't touch {scen.target}: "
            f"{ctx.state['decision_parsed'].package} is not imported "
            "anywhere in the repo"),
    })
    ctx.emit("log", {"message":
                     "predict: decision package not imported anywhere; "
                     "empty prediction (warning, not a failure)"})


def stage_predict(ctx) -> None:
    scen = ctx.state["scenario"]
    symbols = ctx.state["symbols"]
    config = ctx.state["config"]
    if ctx.manifest.get("preset") is None:
        from chainreaction.generic import package_imported_anywhere

        parsed = ctx.state.get("decision_parsed")
        if parsed is not None and not package_imported_anywhere(
                ROOT / scen.target, parsed.package):
            _stage_predict_untouched(ctx, scen)
            return
    ctx.state["untouched"] = False
    g = build_graph_from_repo(scen.target, scen.package)
    entries = probe_entries(scen.probe_code or "", scen.package, g)
    impacts = propagate(g, symbols, config, scen.package,
                        entries=tuple(entries))
    hard = sorted(n for n, i in impacts.items() if i.status == "HARD")
    at_risk = sorted(n for n, i in impacts.items() if i.status == "AT_RISK")
    spine_evidence = spine_for_scenario(g, impacts, symbols, scen, config)
    spine = predicted_chain(g, spine_evidence)
    ctx.state["graph"] = g
    ctx.state["impacts"] = impacts
    ctx.state["hard"] = hard
    ctx.state["at_risk"] = at_risk
    ctx.save_artifact("prediction.json", {
        "hard": hard, "at_risk": at_risk, "spine": spine,
        "spine_evidence": spine_evidence,
        "change_symbols": sorted(symbols),
        "used_symbols": sorted(used_breaking_symbols(g, symbols)),
        "deprecated_symbols": sorted(ctx.state["deprecated"]),
        "changed_symbols": sorted(ctx.state["changed"]),
        "probe_entries": entries,
    })
    ctx.emit("log", {"message":
                     f"predict: HARD={len(hard)} AT_RISK={len(at_risk)} "
                     f"spine={' -> '.join(spine) or '(clean)'}"})


def _suite_log(role: str, result, output: str, extra: str = "") -> str:
    from chainreaction.generic import log_tail

    counts = ", ".join(f"{v} {k}" for k, v in sorted(result.counts.items()))
    lines = [f"# suite: {role}", f"# counts: {counts or 'none'}",
             f"# failed ids ({len(result.failed)}):",
             *[f"- {t}" for t in sorted(result.failed)],
             f"# error files ({len(result.error_files)}):",
             *[f"- {f}" for f in sorted(result.error_files)]]
    if extra:
        lines.append(extra)
    lines += ["## tail", log_tail(output, 30)]
    return "\n".join(lines) + "\n"


def _run_after_suite(after_run, after_py: str, target_abs: str, ws_rel: str,
                     testmap, emit) -> tuple:
    """After suite with the repo's conftest; --noconftest only on collapse.

    The before (coverage) run uses the conftest, so the after run must
    too: skipping it (fixtures, sys.path hooks) invents failures the
    decision did not cause. When collection collapses entirely (the
    broken conftest imports the broken package, as nibabel's does),
    retry once with --noconftest and report the escalation.
    Returns (SuiteResult, stdout, escalated).
    """
    from chainreaction.testmap import parse_suite

    base = [after_py, "-m", "pytest", ".", "-q", "--tb=no",
            "-p", "no:cacheprovider", "--continue-on-collection-errors",
            "-rfE"]
    wide = {"COLUMNS": "200"}
    res = after_run(base, target_abs, extra_env=wide)
    if res.timed_out:
        raise RuntimeError("after suite timed out after 1200s") from None
    after = parse_suite(res.stdout, ws_rel, testmap)
    if after.total == 0:
        emit("log", {"message":
                     "verify: after suite collected nothing with the "
                     "repo conftest; retrying with --noconftest"})
        res = after_run([*base, "--noconftest"], target_abs,
                        extra_env=wide)
        if res.timed_out:
            raise RuntimeError(
                "after suite timed out after 1200s") from None
        after = parse_suite(res.stdout, ws_rel, testmap)
        if after.total == 0:
            emit("log", {"message":
                         "verify: after suite collected nothing even "
                         "without the conftest; check the repo's addopts "
                         "and plugins rather than blaming the decision"})
        return after, res.stdout, True
    return after, res.stdout, False


def _recording_before_pytests(ctx) -> tuple[bool, bool]:
    """(has_cov, has_plain) among the recording's before-env pytests.

    Replay-only: the coverage-vs-plain decision comes from the recording,
    never from local state. A live run that fell back to plain records
    both commands; a clean coverage run records only the coverage one.
    """
    has_cov = has_plain = False
    for rec in getattr(ctx, "_cmd_recs", []) or []:
        if not str(rec.get("env_name") or "").endswith("/before"):
            continue
        cmd = rec.get("cmd") or []
        if len(cmd) < 3 or cmd[1] != "-m" or cmd[2] != "pytest":
            continue
        if any(str(a).startswith("--cov") for a in cmd):
            has_cov = True
        else:
            has_plain = True
    return has_cov, has_plain


def _run_plain_before(before_run, before_py: str, target_abs: str,
                      ws_rel: str):
    """Plain before suite (no coverage); returns (SuiteResult, stdout)."""
    from chainreaction.generic import InstallFailed
    from chainreaction.testmap import parse_suite

    plain = before_run(
        [before_py, "-m", "pytest", ".", "-q", "--tb=no",
         "-p", "no:cacheprovider", "--continue-on-collection-errors",
         "-rfE"], target_abs, extra_env={"COLUMNS": "200"})
    if plain.timed_out:
        raise RuntimeError("before suite timed out after 1200s") from None
    if "No module named pytest" in (plain.stdout + plain.stderr):
        raise InstallFailed(
            "before env has no pytest; delete "
            f"envs/{ws_rel.removeprefix('workspaces/')}/before and re-run"
            if ws_rel.startswith("workspaces/") else
            "before env has no pytest")
    return parse_suite(plain.stdout, ws_rel, None), plain.stdout


def _stage_verify_generic(ctx) -> None:
    from chainreaction import testmap as testmap_mod
    from chainreaction.generic import (
        log_tail,
        new_failures,
        test_precision_recall,
    )
    from chainreaction.testmap import parse_suite, predicted_failing
    from chainreaction.verifier import ProbeResult

    scen = ctx.state["scenario"]
    info = ctx.state["project"]
    ws_rel = ctx.state["workspace"]
    target_abs = str(ROOT / ws_rel)
    conflict = ctx.state.get("dependency_conflict")
    if ctx.state.get("untouched"):
        probe = ProbeResult(ok=True, numpy_version=None, frames=[],
                            modules=[], stderr="")
        ctx.state["probes"] = [("skipped (untouched)", probe)]
        ctx.save_artifact("verify/skipped-untouched.log",
                          "# untouched: decision package not imported; "
                          "suites skipped\n")
        scores = {"suites": "skipped-untouched", "test_precision": None,
                  "test_recall": None, "predicted_failing": [],
                  "actual_new": [], "failure_groups": [], "test_chain": None,
                  "baseline_failed": [],
                  "baseline_counts": {}, "after_counts": {},
                  "unhealthy_baseline": False, "testmap_tests": 0,
                  "after_escalated": False, "conflict": None}
        ctx.save_artifact("scores.json", scores)
        ctx.state["scores"] = scores
        ctx.state["testmap"] = None
        ctx.emit("log", {"message": "verify: untouched; suites skipped"})
        return
    before_run = _env_adapter(ctx, scen.before_env, timeout=1200)
    before_py = ctx.env_python(scen.before_env)
    outputs: dict = {}
    testmap = None
    if info.has_suite:
        # Replay follows the recording's coverage decision: a coverage
        # parse failure on a machine without the envs must not silently
        # fall back to a plain run the recording never made (ReplayMiss).
        if ctx.mode == "replay":
            has_cov, has_plain = _recording_before_pytests(ctx)
            use_cov = has_cov or not has_plain
        else:
            use_cov, has_plain = True, True
        if use_cov:
            try:
                testmap, cov_output = testmap_mod.build_map_with(
                    before_run, before_py, target_abs, info.package, ".",
                    timeout=1200, built_with=scen.before_env,
                    id_prefix=ws_rel + "/")
                before = parse_suite(cov_output, ws_rel, testmap)
                outputs["before"] = cov_output
                testmap.target = ws_rel
                if ctx.mode == "live":
                    ctx.set_manifest(coverage_used=True)
            except RuntimeError as exc:
                if "timed out" in str(exc):
                    raise
                if ctx.mode == "replay" and not has_plain:
                    # The recording ran coverage and never fell back: a
                    # parse failure here is a replay bug, not a plain run.
                    raise RuntimeError(
                        f"coverage replay failed ({exc}); the recording "
                        "ran the before suite under coverage") from exc
                testmap = None
                ctx.emit("log", {"message":
                                 f"verify: coverage unavailable ({exc}); "
                                 "test-level scoring skipped"})
                before, outputs["before"] = _run_plain_before(
                    before_run, before_py, target_abs, ws_rel)
                if ctx.mode == "live":
                    ctx.set_manifest(coverage_used=False)
        else:
            # Recording ran plain (no coverage): skip straight to it.
            testmap = None
            ctx.emit("log", {"message":
                             "verify: recording ran the before suite "
                             "without coverage; test-level scoring skipped"})
            before, outputs["before"] = _run_plain_before(
                before_run, before_py, target_abs, ws_rel)
    else:
        before = None
        ctx.emit("log", {"message":
                         "verify: no test suite; import probes only"})
    after = None
    after_escalated = False
    if info.has_suite and conflict is None:
        after_run = _env_adapter(ctx, scen.verify_env, timeout=1200)
        after_py = ctx.env_python(scen.verify_env)
        after, outputs["after"], after_escalated = _run_after_suite(
            after_run, after_py, target_abs, ws_rel, testmap, ctx.emit)
    probes = []
    if conflict is not None:
        probes.append(("after (conflict)", ProbeResult(
            ok=False, numpy_version=None, frames=[], modules=[],
            stderr=log_tail(conflict.get("detail", ""), 10))))
    else:
        after_run = _env_adapter(ctx, scen.verify_env)
        probes.append(("after-import", run_import_probe(
            ctx.env_python(scen.verify_env), target_abs, info.package,
            run=after_run)))
    probes.append(("before-import", run_import_probe(
        before_py, target_abs, info.package, run=before_run)))
    for _, probe in probes:
        probe.stderr = ctx.scrubber.text(probe.stderr)
    for name, probe in probes:
        ctx.save_artifact(
            f"verify/{_artifact_slug(name)}.log",
            f"# probe: {name}\n# ok: {probe.ok}\n"
            f"# numpy: {probe.numpy_version}\n"
            f"# modules: {' -> '.join(probe.modules) or '(none)'}\n"
            f"## stderr\n{probe.stderr}")
    for role, result in (("before", before), ("after", after)):
        if result is not None:
            ctx.save_artifact(f"verify/{role}-suite.log",
                              _suite_log(role, result, outputs[role]))
    predicted: set = set()
    actual_new: set = set()
    precision = recall = None
    groups: list = []
    chain: dict | None = None
    if before is not None and after is not None:
        actual_new = new_failures(before.failed, after.failed)
        if testmap is not None and ctx.state.get("graph") is not None:
            predicted = predicted_failing(
                testmap, ctx.state["graph"], set(ctx.state.get("hard", [])),
                scen.target, set(ctx.state.get("at_risk", [])),
                set(ctx.state.get("symbols", set())))
            precision, recall = test_precision_recall(predicted, actual_new)
            from chainreaction.testmap import test_call_chain as _chain

            hit = _chain(testmap, ctx.state["graph"], actual_new,
                         set(ctx.state.get("symbols", set())), scen.target)
            if hit is not None:
                tid, func, sym, path, line = hit
                chain = {"test": tid, "func": func, "symbol": sym,
                         "file": path, "line": line}
        if actual_new and outputs.get("after"):
            from chainreaction.testmap import failure_groups as _groups

            groups = _groups(actual_new, outputs["after"], ws_rel)
    unhealthy = bool(before is not None and before.total
                     and len(before.failed) / before.total > 0.2)
    if unhealthy:
        ctx.emit("log", {"message":
                         "verify: unhealthy baseline "
                         f"({len(before.failed)}/{before.total} failing)"})
    if testmap is not None:
        ctx.save_artifact("testmap.json", asdict(testmap))
    scores = {
        "suites": ("ok" if after is not None else
                   ("conflict-no-after" if conflict is not None
                    else ("skipped-no-suite" if before is None
                          else "skipped-no-coverage"))),
        "test_precision": precision, "test_recall": recall,
        "predicted_failing": sorted(predicted),
        "actual_new": sorted(actual_new),
        "failure_groups": groups,
        "test_chain": chain,
        "baseline_failed": sorted(before.failed) if before else [],
        "baseline_counts": before.counts if before else {},
        "after_counts": after.counts if after else {},
        "unhealthy_baseline": unhealthy,
        "testmap_tests": len(testmap.tests) if testmap else 0,
        "testmap_collected": testmap.collected if testmap else 0,
        "after_escalated": after_escalated,
        "conflict": conflict,
    }
    ctx.save_artifact("scores.json", scores)
    ctx.state["scores"] = scores
    ctx.state["testmap"] = testmap
    ctx.state["probes"] = probes
    detail = (f"; test P/R {precision:.2f}/{recall:.2f}"
              if precision is not None else "")
    ctx.emit("log", {"message":
                     f"verify: {len(probes)} probe(s), "
                     f"{len(actual_new)} new failure(s){detail}"})


def stage_verify(ctx) -> None:
    if ctx.manifest.get("preset") is None:
        _stage_verify_generic(ctx)
        return
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


def _stage_report_generic(ctx) -> None:
    import networkx as nx

    scen = ctx.state["scenario"]
    changeset = ctx.state["changeset"]
    symbols = ctx.state["symbols"]
    g = ctx.state.get("graph") or nx.DiGraph()
    impacts = ctx.state.get("impacts") or {}
    probes = ctx.state["probes"]
    flagged = set(ctx.state.get("hard", [])) | set(ctx.state.get("at_risk", []))
    layers, before_numpy = build_layers(scen, probes, flagged)
    pkgdir = package_dir(scen.target, scen.package)
    grep_count = len(grep_hits(pkgdir,
                           patterns_for_changeset(changeset)))
    if scen.run_ruff:
        ruff = ruff_npy201(relative_posix(pkgdir, "."), run=_tool_adapter(ctx))
        ruff_count = len(ruff) if ruff is not None else None
    else:
        ruff_count = None
    report = build_report(scen, changeset, symbols, g, impacts, layers,
                          probes[0][1].numpy_version, before_numpy,
                          grep_count, ruff_count,
                          ctx.state.get("interpret_stats"))
    scores = ctx.state.get("scores") or {}
    extra = {
        "scores": scores,
        "collateral": (ctx.state.get("envs") or {}).get("collateral", {}),
        "conflict": ctx.state.get("dependency_conflict"),
        "untouched": ctx.state.get("untouched", False),
        "has_suite": ctx.state.get("project").has_suite
        if ctx.state.get("project") else True,
    }
    if ctx.mode == "replay":
        want_narrative = (ctx.manifest.get("narrative_source")
                          == "muse-spark")
    else:
        want_narrative = llm_mod.has_key()
    if want_narrative:
        used_extra: dict | None = extra
        try:
            report.narrative = generate_narrative(report, chat_fn=ctx.llm,
                                                 extra=extra)
        except ReplayMiss:
            # Pre-test-brief recordings: the recorded prompt lacks the
            # test-suite lines, so replay the old brief verbatim.
            if ctx.mode != "replay":
                raise
            report.narrative = generate_narrative(report, chat_fn=ctx.llm,
                                                 extra=None)
            used_extra = None
        ctx.set_manifest(narrative_source="muse-spark")
        violations = check_narrative(report, extra=used_extra)
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
    ctx.save_artifact("report.md", render_markdown(report, extra))
    stats = ""
    if scores.get("test_precision") is not None:
        stats = (f"; test P/R {scores['test_precision']:.2f}/"
                 f"{scores['test_recall']:.2f}")
    ctx.emit("log", {"message":
                     f"report: chain "
                     f"{'MATCH' if report.spine_match else 'DIFFERS'}"
                     f"{stats}"})


def stage_report(ctx) -> None:
    if ctx.manifest.get("preset") is None:
        _stage_report_generic(ctx)
        return
    scen = ctx.state["scenario"]
    changeset = ctx.state["changeset"]
    symbols = ctx.state["symbols"]
    g = ctx.state["graph"]
    impacts = ctx.state["impacts"]
    probes = ctx.state["probes"]
    flagged = set(ctx.state["hard"]) | set(ctx.state["at_risk"])
    layers, before_numpy = build_layers(scen, probes, flagged)
    pkgdir = package_dir(scen.target, scen.package)
    grep_count = len(grep_hits(pkgdir,
                           patterns_for_changeset(changeset)))
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
