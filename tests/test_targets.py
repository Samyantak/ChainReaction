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


def test_live_interpret_drops_out_of_package_symbols(monkeypatch):
    import json as json_mod

    from chainreaction.interpret import FetchedText

    monkeypatch.setattr("chainreaction.llm.has_key", lambda: True)
    scen = scenarios.get("sponsorfit-meta")
    manifest = {"preset": scen.id}
    saved, events = {}, []
    guide = ("Set the base URL to https://api.meta.ai/v1 and export "
             "MODEL_API_KEY before calling.")
    # Code-aware reply: both items ground in the real llm.py:14 line
    # (verification passes); the sanitizer then drops the out-of-package
    # symbol and keeps the in-package one.
    code_quote = 'Provider = Literal["anthropic", "openai", "ollama"]'
    reply = json_mod.dumps({"items": [
        {"symbol": "MODEL_API_KEY", "kind": "missing_param",
         "quote": "", "replacement": None,
         "code_quote": code_quote, "code_file": "src/sponsorfit/llm.py",
         "code_line": 14,
         "detail": {"expected": "x", "actual": "y", "note": None}},
        {"symbol": "sponsorfit.llm.Provider", "kind": "rejected_value",
         "quote": "", "replacement": None,
         "code_quote": code_quote, "code_file": "src/sponsorfit/llm.py",
         "code_line": 14,
         "detail": {"expected": "x", "actual": "y", "note": None}},
    ]})
    ctx = SimpleNamespace(
        manifest=manifest, mode="live", state={"scenario": scen},
        llm=lambda *a, **k: reply, fetch=lambda url: FetchedText(guide),
        emit=lambda kind, payload: events.append(payload.get("message", "")),
        set_manifest=manifest.update,
        save_artifact=lambda n, o: saved.update({n: o}))
    stages_mod.stage_interpret(ctx)
    assert manifest["changeset_source"] == "muse-spark"
    assert [i["symbol"] for i in saved["changeset.json"]["items"]] == [
        "sponsorfit.llm.Provider"]
    assert saved["changeset.json"]["dropped_symbols"] == ["MODEL_API_KEY"]
    assert any("dropped 1 model symbols" in e and "MODEL_API_KEY" in e
               for e in events)


def test_fetch_workspace_clones_and_verifies(tmp_path, monkeypatch):
    import json as json_mod

    sha = "d" * 40
    preset = tmp_path / "runs" / "presets" / "demo-generic"
    preset.mkdir(parents=True)
    (preset / "manifest.json").write_text(json_mod.dumps({
        "preset": None, "repo_url": "https://github.com/acme/demo",
        "resolved_commit": sha}), encoding="utf-8")
    monkeypatch.setattr(fetch_targets, "ROOT", tmp_path)
    assert fetch_targets._generic_pin("demo-generic") == (
        "https://github.com/acme/demo", sha)
    assert fetch_targets._generic_pin("missing") is None
    calls = []

    def run(cmd, cwd):
        calls.append(cmd)
        if cmd[1] == "clone":
            (tmp_path / cmd[-1]).mkdir(parents=True)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "rev-parse" in cmd:
            return SimpleNamespace(returncode=0, stdout=sha + "\n",
                                   stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    assert fetch_targets.ensure_workspace(
        "https://github.com/acme/demo", sha, run) == sha
    assert calls[0][:3] == ["git", "clone", "--filter=blob:none"]
    assert calls[0][-1] == f"workspaces/acme__demo@{sha[:12]}"


def test_fetch_default_includes_generic_presets(tmp_path, monkeypatch):
    import json as json_mod

    sha = "e" * 40
    preset = tmp_path / "runs" / "presets" / "demo-generic"
    preset.mkdir(parents=True)
    (preset / "manifest.json").write_text(json_mod.dumps({
        "preset": None, "repo_url": "https://github.com/acme/demo",
        "resolved_commit": sha}), encoding="utf-8")
    monkeypatch.setattr(fetch_targets, "ROOT", tmp_path)
    # Default id list (no argv) must include the generic preset workspace.
    assert "demo-generic" in fetch_targets._default_ids()
    assert fetch_targets._generic_pin("demo-generic") == (
        "https://github.com/acme/demo", sha)


def test_record_preset_parses_fresh_flag():
    assert record_preset.parse_args(["prog", "x"]) == ("x", False)
    assert record_preset.parse_args(["prog", "x", "--fresh"]) == ("x", True)
    with pytest.raises(SystemExit):
        record_preset.parse_args(["prog"])
