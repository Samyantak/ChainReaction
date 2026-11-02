"""Target fetching: scenario pins, setup clone/verify, fetch script."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from chainreaction import scenarios
from chainreaction.runs import stages as stages_mod
from chainreaction.runs.context import CommitMismatch

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fetch_targets = _load("fetch_targets")
record_preset = _load("record_preset")


def test_scenarios_carry_repo_pins():
    nib = scenarios.get("nibabel-numpy2")
    assert nib.repo_url == "https://github.com/nipy/nibabel"
    assert nib.ref == "67ea99725d7e40bb6738fc55f8360a609212e72c"
    fit = scenarios.get("sponsorfit-meta")
    assert fit.repo_url == "https://github.com/Samyantak/sponsorfit"
    assert fit.ref == "af24b102773c15c2f59967e1dd26e85dac1fe920"


def _fake_ctx(preset, mode, manifest_extra=None):
    manifest = {"preset": preset, "repo_url": "", "ref": ""}
    manifest.update(manifest_extra or {})
    calls = []

    def run_cmd(cmd, cwd=".", env_name=None, timeout=600):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return SimpleNamespace(manifest=manifest, mode=mode, state={},
                           run_cmd=run_cmd,
                           emit=lambda *a, **k: None,
                           set_manifest=manifest.update), calls


def test_setup_clones_missing_target(tmp_path, monkeypatch):
    monkeypatch.setattr(stages_mod, "ROOT", tmp_path)
    ref = scenarios.get("nibabel-numpy2").ref
    monkeypatch.setattr(stages_mod, "_git_commit", lambda cwd: ref)
    ctx, calls = _fake_ctx("nibabel-numpy2", "live")
    stages_mod.stage_setup(ctx)
    assert calls[0][:3] == ["git", "clone",
                            "https://github.com/nipy/nibabel"]
    assert calls[1][:2] == ["git", "-C"]
    assert calls[1][-1] == "67ea99725d7e40bb6738fc55f8360a609212e72c"
    assert ctx.manifest["resolved_commit"] == ref
    assert ctx.manifest["repo_url"].endswith("/nibabel")


def test_setup_rejects_wrong_commit(tmp_path, monkeypatch):
    (tmp_path / "targets" / "nibabel").mkdir(parents=True)
    monkeypatch.setattr(stages_mod, "ROOT", tmp_path)
    monkeypatch.setattr(stages_mod, "_git_commit", lambda cwd: "b" * 40)
    ctx, _ = _fake_ctx("nibabel-numpy2", "live")
    with pytest.raises(CommitMismatch, match="pins"):
        stages_mod.stage_setup(ctx)


def test_setup_replay_needs_source_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(stages_mod, "ROOT", tmp_path)
    ctx, calls = _fake_ctx("nibabel-numpy2", "replay")
    with pytest.raises(RuntimeError, match="fetch_targets"):
        stages_mod.stage_setup(ctx)
    assert calls == []  # replay never clones


def test_fetch_script_clones_and_verifies(tmp_path, monkeypatch):
    nib = scenarios.get("nibabel-numpy2")
    calls = []

    def run(cmd, cwd):
        calls.append(cmd)
        if cmd[:2] == ["git", "clone"]:
            (tmp_path / "targets" / "nibabel").mkdir(parents=True)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[1:3] == ["-C", "targets/nibabel"] and "rev-parse" in cmd:
            return SimpleNamespace(returncode=0, stdout=nib.ref + "\n",
                                   stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fetch_targets, "ROOT", tmp_path)
    assert fetch_targets.ensure_target(nib, run) == nib.ref
    assert calls[0][:2] == ["git", "clone"]
    assert any("checkout" in c for c in calls)


def test_fetch_script_rejects_wrong_commit(tmp_path, monkeypatch):
    (tmp_path / "targets" / "nibabel").mkdir(parents=True)
    monkeypatch.setattr(fetch_targets, "ROOT", tmp_path)

    def run(cmd, cwd):
        return SimpleNamespace(returncode=0, stdout="c" * 40 + "\n",
                               stderr="")

    with pytest.raises(SystemExit, match="need"):
        fetch_targets.ensure_target(scenarios.get("nibabel-numpy2"), run)


def test_pinned_scenario_uses_stub_despite_key(monkeypatch):
    monkeypatch.setattr("chainreaction.llm.has_key", lambda: True)
    scen = scenarios.get("sponsorfit-meta")
    assert scen.live_interpret is False
    manifest = {"preset": scen.id}
    saved = {}

    def boom(*a, **k):
        raise AssertionError("model must not be called for a pinned set")

    ctx = SimpleNamespace(manifest=manifest, mode="live",
                          state={"scenario": scen}, llm=boom, fetch=boom,
                          emit=lambda *a, **k: None,
                          set_manifest=manifest.update,
                          save_artifact=lambda n, o: saved.update({n: o}))
    stages_mod.stage_interpret(ctx)
    assert manifest["changeset_source"] == "hand-recorded"
    assert [i["symbol"] for i in saved["changeset.json"]["items"]] == [
        i.symbol for i in scen.stub_items]
    assert scenarios.get("nibabel-numpy2").live_interpret is True


def test_record_preset_parses_fresh_flag():
    assert record_preset.parse_args(["prog", "x"]) == ("x", False)
    assert record_preset.parse_args(["prog", "x", "--fresh"]) == ("x", True)
    with pytest.raises(SystemExit):
        record_preset.parse_args(["prog"])
