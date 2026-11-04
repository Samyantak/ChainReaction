"""M2 generic-path unit tests: pure helpers, synthetic repos only."""

import json
import sys

import pytest

from chainreaction.generic import (
    CloneFailed,
    NoPythonPackage,
    VersionNotFound,
    detect_project,
    detect_package,
    detect_test_command,
    detect_test_extras,
    guide_candidates_from_pypi,
    new_failures,
    parse_decision,
    parse_decision_with_llm,
    parse_repo_url,
    pick_python_version,
    resolve_target_version,
    test_precision_recall as score_tests,
    workspace_key,
    workspace_rel,
)


def test_parse_decision_grammar():
    assert parse_decision("upgrade numpy to 2.0").package == "numpy"
    assert parse_decision("upgrade numpy to 2.0").to_version == "2.0"
    assert parse_decision("upgrade numpy to 2.0").from_version == ""
    assert parse_decision("bump requests to 2.32").package == "requests"
    assert parse_decision("move scipy to 1.14").to_version == "1.14"
    assert parse_decision("numpy==2.0.2").to_version == "2.0.2"
    assert parse_decision("numpy 2.0").package == "numpy"
    assert parse_decision("please maybe upgrade") is None
    assert parse_decision("") is None
    pinned = parse_decision("upgrade werkzeug from 2.2 to 3.0")
    assert (pinned.package, pinned.from_version, pinned.to_version) == (
        "werkzeug", "2.2", "3.0")


def test_parse_decision_with_llm_fallback():
    def fake(system, user, max_tokens, response_format):
        assert "migrate" in user
        return json.dumps({"package": "pandas", "to_version": "3.0",
                           "from_version": ""})

    d = parse_decision_with_llm("migrate to the new pandas", fake)
    assert (d.package, d.to_version, d.from_version) == ("pandas", "3.0", "")

    def pinned(system, user, max_tokens, response_format):
        return json.dumps({"package": "sqlalchemy", "to_version": "2.0",
                           "from_version": "1.4"})

    d2 = parse_decision_with_llm("jump sqlalchemy 1.4 to 2.0", pinned)
    assert d2.from_version == "1.4"

    def empty(system, user, max_tokens, response_format):
        return json.dumps({"package": "", "to_version": ""})

    with pytest.raises(VersionNotFound, match="could not parse"):
        parse_decision_with_llm("do the thing", empty)


def test_repo_url_and_workspace_key():
    assert parse_repo_url("https://github.com/nipy/nibabel") == (
        "nipy", "nibabel")
    assert parse_repo_url("https://github.com/a/b.git") == ("a", "b")
    with pytest.raises(CloneFailed, match="bad repo_url"):
        parse_repo_url("not-a-url")
    with pytest.raises(CloneFailed, match="bad repo_url"):
        parse_repo_url("https://github.com/onlyowner")
    assert workspace_key("nipy", "nibabel", "67ea99725d7e") == \
        "nipy__nibabel@67ea99725d7e"
    assert workspace_rel("nipy", "nibabel", "67ea99725d7e") == \
        "workspaces/nipy__nibabel@67ea99725d7e"


def _pkg(root, name, n_files=1, src_layout=False):
    base = root / ("src" if src_layout else ".") / name
    base.mkdir(parents=True)
    (base / "__init__.py").write_text("", encoding="utf-8")
    for i in range(n_files - 1):
        (base / f"m{i}.py").write_text("x = 1\n", encoding="utf-8")
    return base


def test_detect_package_from_pyproject(tmp_path):
    _pkg(tmp_path, "mypkg")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "mypkg"\nrequires-python = ">=3.10"\n',
        encoding="utf-8")
    package, others = detect_package(tmp_path)
    assert package == "mypkg"
    assert others == []


def test_detect_package_normalizes_distribution_name(tmp_path):
    _pkg(tmp_path, "my_pkg")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "my-pkg"\n', encoding="utf-8")
    assert detect_package(tmp_path)[0] == "my_pkg"


def test_detect_package_setup_cfg_fallback(tmp_path):
    _pkg(tmp_path, "cfgpkg")
    (tmp_path / "setup.cfg").write_text(
        "[metadata]\nname = cfgpkg\n", encoding="utf-8")
    assert detect_package(tmp_path)[0] == "cfgpkg"


def test_detect_package_setup_py_is_static_only(tmp_path):
    # Would explode if executed: detection must parse, never run.
    _pkg(tmp_path, "staticpkg")
    (tmp_path / "setup.py").write_text(
        "raise RuntimeError('must never execute')\n"
        "from setuptools import setup\n"
        "setup(name='staticpkg')\n",
        encoding="utf-8")
    assert detect_package(tmp_path)[0] == "staticpkg"


def test_detect_package_top_level_most_files_wins(tmp_path):
    _pkg(tmp_path, "small", n_files=1)
    _pkg(tmp_path, "big", n_files=4)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("", encoding="utf-8")
    package, others = detect_package(tmp_path)
    assert package == "big"
    assert others == ["small"]  # tests/ excluded


def test_detect_package_override_and_missing(tmp_path):
    _pkg(tmp_path, "real")
    assert detect_package(tmp_path, override="real")[0] == "real"
    with pytest.raises(NoPythonPackage, match="override"):
        detect_package(tmp_path, override="ghost")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(NoPythonPackage, match="no Python package"):
        detect_package(empty)


def test_pick_python_version():
    assert pick_python_version(">=3.10") == "3.12"
    assert pick_python_version(">=3.11,<3.13") == "3.12"
    assert pick_python_version("==3.10") == "3.10"
    assert pick_python_version(">=3.14") == "3.11"  # nothing fits: default
    assert pick_python_version("") == "3.12"
    # Commit-date era: newest interpreter released by then.
    assert pick_python_version("", "2020-11-23") == "3.9"
    assert pick_python_version(">=3.7", "2023-09-01") == "3.11"
    assert pick_python_version("", "2024-05-14") == "3.12"
    assert pick_python_version("", "2025-06-01") == "3.13"
    assert pick_python_version("", "2019-01-01") == "3.9"  # oldest we build
    assert pick_python_version("", "not-a-date") == "3.12"  # ignored
    assert pick_python_version(">=3.10", "2020-11-23") == "3.13"  # reqs win
    assert pick_python_version("==3.10", "2020-11-23") == "3.10"


def test_requires_python_readers(tmp_path):
    from chainreaction.generic import _read_requires_python, _setup_py_static

    assert _read_requires_python(tmp_path) == ""
    assert _setup_py_static(tmp_path) == (None, "")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.9"\n',
        encoding="utf-8")
    (tmp_path / "setup.cfg").write_text(
        "[options]\npython_requires = >=3.8\n", encoding="utf-8")
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup\n"
        'deps = ["a>=1"]\n'
        'setup(name="x", install_requires=deps, python_requires=">=3.7")\n',
        encoding="utf-8")
    assert _read_requires_python(tmp_path) == ">=3.9,>=3.8,>=3.7"
    assert _setup_py_static(tmp_path) == (["a>=1"], ">=3.7")
    (tmp_path / "setup.py").write_text("setup(\n", encoding="utf-8")
    assert _setup_py_static(tmp_path) == (None, "")
    (tmp_path / "setup.py").write_text(
        "setup(name='x', install_requires=get_deps())\n", encoding="utf-8")
    assert _setup_py_static(tmp_path) == (None, "")


def test_detect_test_command_and_extras(tmp_path):
    cmd, has_suite = detect_test_command(tmp_path)
    assert (cmd, has_suite) == ([], False)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_x(): pass\n",
                                                 encoding="utf-8")
    cmd, has_suite = detect_test_command(tmp_path)
    assert has_suite and cmd[0] == "pytest"
    (tmp_path / "pyproject.toml").write_text(
        '[project.optional-dependencies]\ndev = ["pytest"]\n',
        encoding="utf-8")
    (tmp_path / "requirements-test.txt").write_text("pytest\n",
                                                   encoding="utf-8")
    extras, reqs = detect_test_extras(tmp_path)
    assert extras == ["dev"]
    assert reqs == ["requirements-test.txt"]


def test_detect_test_extras_tox_and_requirements_dir(tmp_path):
    from chainreaction.generic import _install_args, _tox_test_deps

    (tmp_path / "requirements").mkdir()
    (tmp_path / "requirements" / "tests.txt").write_text(
        "pytest==7.4.0\n", encoding="utf-8")
    (tmp_path / "requirements" / "tests.in").write_text(
        "pytest\n", encoding="utf-8")
    (tmp_path / "tox.ini").write_text(
        "[testenv]\ndeps =\n"
        "    -r requirements/tests.txt\n"
        "    pytest-asyncio\n"
        "    min: -r requirements/tests-min.txt\n"
        "    -e .\n",
        encoding="utf-8")
    extras, reqs = detect_test_extras(tmp_path)
    assert extras == []
    # Compiled .txt wins over .in; factor lines and -e never surface.
    assert reqs == ["requirements/tests.txt"]
    specs, refs = _tox_test_deps(tmp_path)
    assert specs == ["pytest-asyncio"]
    assert refs == ["requirements/tests.txt"]
    _pkg(tmp_path, "demo")
    info = detect_project(tmp_path)
    assert info.test_specs == ["pytest-asyncio"]
    assert "pytest-asyncio" in _install_args(info)
    # Missing -r targets drop silently.
    (tmp_path / "tox.ini").write_text(
        "[testenv]\ndeps = -r requirements/gone.txt\n", encoding="utf-8")
    assert detect_test_extras(tmp_path)[1] == ["requirements/tests.txt"]


def test_addopts_cov_keeps_pytest_cov_in_after_env():
    from chainreaction.generic import (
        ProjectInfo, TEST_TOOLING, after_tooling)

    info = ProjectInfo(package="demo", pytest_addopts="--cov=demo")
    assert after_tooling(info) == TEST_TOOLING
    plain = ProjectInfo(package="demo")
    assert after_tooling(plain) == ["pytest"]


def test_strip_spec_version_keeps_name_extras_markers():
    from chainreaction.generic import _strip_spec_version

    assert _strip_spec_version("pytest==6.1.2") == "pytest"
    assert _strip_spec_version("alabaster>=0.6.2") == "alabaster"
    assert _strip_spec_version("pytest") == "pytest"
    assert _strip_spec_version("pkg[extra]==1.0") == "pkg[extra]"
    assert _strip_spec_version(
        "mypy==0.790; implementation_name=='cpython'") == \
        "mypy; implementation_name=='cpython'"
    # Paths and direct references pass through unstripped.
    assert _strip_spec_version("./subdir") == "./subdir"
    assert _strip_spec_version("pkg @ https://x/pkg.zip") == \
        "pkg @ https://x/pkg.zip"
    assert _strip_spec_version("@invalid") is None


def test_after_install_args_floats_test_deps_to_freeze(tmp_path):
    from chainreaction.generic import (
        ProjectInfo, _after_install_args)

    (tmp_path / "requirements-dev.txt").write_text(
        "# comment\n\npytest==6.1.2\npytest-cov==2.10.1\n"
        "mypy==0.790; implementation_name=='cpython'\n"
        "pkg[extra]==1.0\njinja2==2.11.2\nghostpkg==9.9\n",
        encoding="utf-8")
    info = ProjectInfo(
        package="demopkg",
        test_requirements=["requirements-dev.txt"],
        test_specs=["pytest-asyncio", "flake8==3.8.4"])
    freeze = {"pytest": "3.5.1", "pytest-cov": "2.6.0", "mypy": "0.790",
              "pkg": "1.0", "pytest-asyncio": "0.15", "flake8": "3.8.4",
              "jinja2": "2.11.2", "demopkg": "1.4.2", "sphinx": "3.3.1"}
    tooling = ["pytest", "coverage>=7", "pytest-cov"]
    args = _after_install_args(tmp_path, info, tooling, freeze,
                               "jinja2", "demopkg")
    assert args[0] == "."
    assert "-r" not in args
    names = args[1:-len(tooling)]
    assert "pytest" in names and "pytest-cov" in names
    assert "mypy; implementation_name=='cpython'" in names
    assert "pkg[extra]" in names
    assert "pytest-asyncio" in names and "flake8" in names
    # Decided, repo, unknown-to-freeze, and non-test-dep packages
    # are never named; tooling passes through verbatim.
    for absent in ("jinja2", "demopkg", "ghostpkg", "sphinx"):
        assert absent not in names
    assert all("==" not in spec.partition(";")[0] for spec in names)
    assert args[-len(tooling):] == tooling


def test_detect_project_end_to_end(tmp_path):
    _pkg(tmp_path, "full")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "full"\nrequires-python = ">=3.11"\n',
        encoding="utf-8")
    (tmp_path / "tests").mkdir()
    info = detect_project(tmp_path)
    assert info.package == "full"
    assert info.python_version == "3.12"
    assert info.has_suite is True


def test_resolve_target_version():
    pypi = {"releases": {"1.9.0": [], "2.0.0": [], "2.0.2": []},
            "info": {"version": "2.0.2"}}
    assert resolve_target_version(pypi, "2.0") == "2.0.2"
    assert resolve_target_version(pypi, "2.0.0") == "2.0.0"
    with pytest.raises(VersionNotFound, match="not on PyPI"):
        resolve_target_version(pypi, "9.9")
    with pytest.raises(VersionNotFound, match="no releases"):
        resolve_target_version({"releases": {}}, "2.0")


def test_guide_candidates_from_pypi():
    pypi = {"info": {"project_urls": {
        "Homepage": "https://example.com",
        "Changelog": "https://example.com/changes",
        "Bug tracker": "https://example.com/bugs"}}}
    cands = guide_candidates_from_pypi(pypi)
    assert cands[0] == "https://example.com/changes"
    assert "https://example.com/bugs" not in cands
    assert guide_candidates_from_pypi({"info": {}}) == []


def test_baseline_exclusion_and_test_scores():
    before = {"a.py::t1", "a.py::t2"}
    after = {"a.py::t2", "a.py::t3"}
    assert new_failures(before, after) == {"a.py::t3"}  # t2 never blamed
    assert new_failures(set(), after) == after
    p, r = score_tests({"a.py::t3"}, {"a.py::t3"})
    assert (p, r) == (1.0, 1.0)
    p, r = score_tests({"a.py::t3", "a.py::t9"}, {"a.py::t3"})
    assert (p, r) == (0.5, 1.0)
    assert score_tests(set(), set()) == (1.0, 1.0)


@pytest.fixture()
def tmp_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAINREACTION_RUNS_DIR", str(tmp_path / "runs"))
    return tmp_path / "runs"


def test_target_probe_cannot_see_model_api_key(tmp_runs, monkeypatch):
    from chainreaction.runs import store
    from chainreaction.runs.context import RunContext

    monkeypatch.setenv("MODEL_API_KEY", "sk-sentinel-secret-value")
    monkeypatch.setenv("SOME_TOKEN", "tok-sentinel")
    manifest = {"run_id": "iso1", "mode": "live", "status": "running"}
    store.write_json(store.run_dir("iso1") / store.MANIFEST, manifest)
    ctx = RunContext(run_id="iso1", manifest=manifest, mode="live")
    res = ctx.run_cmd(
        [sys.executable, "-c",
         "import os; print('KEY=' + os.environ.get('MODEL_API_KEY', 'ABSENT')); "
         "print('TOK=' + os.environ.get('SOME_TOKEN', 'ABSENT')); "
         "print('HAS_PATH=' + str('PATH' in os.environ))"],
        cwd=".", env_name=None, timeout=60)
    assert res.returncode == 0
    # The scrubber would mask a leak as <redacted-key>; absence of both
    # the sentinel and the mask proves the key never reached the child.
    assert "KEY=ABSENT" in res.stdout
    assert "TOK=ABSENT" in res.stdout
    assert "HAS_PATH=True" in res.stdout
    assert "sk-sentinel-secret-value" not in res.stdout
    assert "<redacted-key>" not in res.stdout


def test_create_run_generic_gate(tmp_runs):
    from chainreaction.runs import store, worker

    with pytest.raises(ValueError, match="allow_untrusted"):
        worker.create_run(worker.RunRequest(
            repo_url="https://github.com/acme/demo",
            decision="upgrade numpy to 2.0", mode="live"))
    with pytest.raises(ValueError, match="bad repo_url"):
        worker.create_run(worker.RunRequest(
            repo_url="not-a-url", decision="upgrade numpy to 2.0",
            mode="live", allow_untrusted=True))
    with pytest.raises(ValueError, match="preset or"):
        worker.create_run(worker.RunRequest(
            repo_url="https://github.com/acme/demo", mode="live",
            allow_untrusted=True))
    run_id = worker.create_run(worker.RunRequest(
        repo_url="https://github.com/acme/demo", decision="upgrade x to 1",
        mode="live", allow_untrusted=True, package="demo"))
    manifest = store.read_json(store.run_dir(run_id) / store.MANIFEST)
    assert manifest["preset"] is None
    assert manifest["allow_untrusted"] is True
    assert manifest["package"] == "demo"
    assert manifest["status"] == "queued"


def _generic_ctx(manifest, run_cmd, fetch=None, llm=None):
    from types import SimpleNamespace

    saved = {}
    if fetch is None:
        def fetch(url, timeout=60):
            return json.dumps({
                "releases": {"1.26.4": [], "2.0.2": []},
                "info": {"version": "2.0.2", "project_urls": {}}})
    if llm is None:
        def llm(*a, **k):
            raise AssertionError("grammar path needs no LLM")
    return SimpleNamespace(
        manifest=manifest, mode=manifest.get("mode", "live"),
        run_id="r1", state={}, run_cmd=run_cmd, fetch=fetch, llm=llm,
        env_python=lambda name: f"<envpy:{name}>",
        emit=lambda *a, **k: None, set_manifest=manifest.update,
        save_artifact=lambda n, o: saved.update({n: o})), saved


SHA = "a" * 40

BEFORE_FREEZE = "numpy==1.26.4\npytest==7.4.0\ndemopkg==0.1\n"
AFTER_FREEZE = "numpy==2.0.2\npytest==7.4.0\ndemopkg==0.1\n"


def _uv_fake(tmp_path, calls, before_freeze=BEFORE_FREEZE,
             after_freeze=AFTER_FREEZE):
    """Extend a run_cmd fake with uv/venv/install/freeze handling."""
    import os as _os
    from types import SimpleNamespace

    def handle(cmd, env_name=None):
        if cmd[:2] == ["uv", "--version"]:
            return SimpleNamespace(returncode=0, stdout="uv 0.7.0\n",
                                   stderr="", missing=None)
        if cmd[:3] == ["uv", "python", "install"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if cmd[:2] == ["uv", "venv"]:
            exe = (tmp_path / cmd[-1]
                   / ("Scripts/python.exe" if _os.name == "nt"
                      else "bin/python"))
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.touch()
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if cmd[:3] == ["uv", "pip", "install"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if cmd[:3] == ["uv", "pip", "uninstall"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if len(cmd) > 2 and cmd[1] == "-c" and "importlib.metadata" \
                in cmd[2]:
            text = (after_freeze if (env_name or "").endswith("/after")
                    else before_freeze)
            return SimpleNamespace(returncode=0, stdout=text, stderr="",
                                   missing=None)
        if len(cmd) > 2 and cmd[1] == "-c" and "compileall" in cmd[2]:
            return SimpleNamespace(returncode=0, stdout="primed 1 ok\n",
                                   stderr="", missing=None)
        return None

    return handle


def _seed_repo(ws: "Path"):
    (ws / "demopkg").mkdir(parents=True)
    (ws / "demopkg" / "__init__.py").write_text("", encoding="utf-8")
    (ws / "pyproject.toml").write_text(
        '[project]\nname = "demopkg"\n', encoding="utf-8")
    (ws / "tests").mkdir(exist_ok=True)


def test_generic_setup_live_clones_and_detects(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.runs import stages as stages_mod
    from types import SimpleNamespace

    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    monkeypatch.setattr(stages_mod, "ROOT", tmp_path)
    calls = []
    uv = _uv_fake(tmp_path, calls)

    def run_cmd(cmd, cwd=".", env_name=None, timeout=600, tool=None):
        calls.append(cmd)
        hit = uv(cmd, env_name)
        if hit is not None:
            return hit
        if cmd[1] == "clone":
            _seed_repo(tmp_path / cmd[-1])
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[-2:] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout=SHA + "\n",
                                   stderr="")
        if "show" in cmd:
            return SimpleNamespace(
                returncode=0, stdout="2023-05-01T12:00:00+00:00\n",
                stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    manifest = {"preset": None, "repo_url": "https://github.com/acme/demo",
                "ref": "main", "decision": "upgrade numpy to 2.0",
                "guide_url": "", "package": ""}
    ctx, saved = _generic_ctx(manifest, run_cmd)
    stages_mod.stage_setup(ctx)
    assert calls[0][:4] == ["git", "clone", "--filter=blob:none",
                            "https://github.com/acme/demo"]
    ws_rel = f"workspaces/acme__demo@{SHA[:12]}"
    assert manifest["workspace"] == ws_rel
    assert manifest["resolved_commit"] == SHA
    assert manifest["commit_date"] == "2023-05-01"
    assert manifest["detected_package"] == "demopkg"
    assert manifest["decision_package"] == "numpy"
    assert manifest["decision_version"] == "2.0.2"
    assert (tmp_path / ws_rel / "demopkg").is_dir()  # staging renamed
    assert saved["project.json"]["package"] == "demopkg"
    assert saved["project.json"]["has_suite"] is True
    assert ctx.state["scenario"].target == ws_rel
    assert ctx.state["scenario"].verify_env == f"acme__demo@{SHA[:12]}/after"
    assert ctx.state["scenario"].run_ruff is True  # numpy decision
    envs = saved["envs.json"]
    assert envs["before"]["freeze"]["numpy"] == "1.26.4"
    assert envs["after"]["freeze"]["numpy"] == "2.0.2"
    assert envs["conflict"] is None
    assert manifest["env_provenance"] == {"before": "built",
                                          "after": "built"}
    installs = [c for c in calls if c[:3] == ["uv", "pip", "install"]]
    assert "--exclude-newer" in installs[0]  # before: world as of commit
    assert "2023-05-01" in installs[0]


def test_generic_setup_reuses_cached_workspace(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.runs import stages as stages_mod
    from types import SimpleNamespace

    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    monkeypatch.setattr(stages_mod, "ROOT", tmp_path)
    ws_rel = f"workspaces/acme__demo@{SHA[:12]}"
    _seed_repo(tmp_path / ws_rel)
    monkeypatch.setattr(generic_mod, "git_head", lambda p: SHA)
    calls = []
    uv = _uv_fake(tmp_path, calls)

    def run_cmd(cmd, cwd=".", env_name=None, timeout=600, tool=None):
        calls.append(cmd)
        hit = uv(cmd, env_name)
        if hit is not None:
            return hit
        return SimpleNamespace(
            returncode=0, stdout="2023-05-01T00:00:00+00:00\n", stderr="")

    def manifest():
        return {"preset": None, "repo_url": "https://github.com/acme/demo",
                "ref": SHA, "decision": "upgrade numpy to 2.0",
                "guide_url": "", "package": ""}

    ctx, _ = _generic_ctx(manifest(), run_cmd)
    stages_mod.stage_setup(ctx)
    assert all(c[1] != "clone" for c in calls)
    assert ctx.manifest["resolved_commit"] == SHA
    first_builds = [c for c in calls
                    if c[:2] == ["uv", "venv"] or c[:3] == ["uv", "pip",
                                                           "install"]]
    assert first_builds  # first run builds the envs
    # Second run reuses the workspace and both envs: no clone, no venv,
    # no installs — only the date lookup, uv probe, and freezes.
    del calls[:]
    ctx2, _ = _generic_ctx(manifest(), run_cmd)
    stages_mod.stage_setup(ctx2)
    assert all(c[1] != "clone" for c in calls)
    assert not [c for c in calls if c[:2] == ["uv", "venv"]]
    assert not [c for c in calls if c[:3] == ["uv", "pip", "install"]]
    assert ctx2.manifest["env_provenance"] == {"before": "reused",
                                               "after": "reused"}


def test_generic_setup_clone_failure_is_typed(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.generic import CloneFailed
    from chainreaction.runs import stages as stages_mod
    from types import SimpleNamespace

    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    monkeypatch.setattr(stages_mod, "ROOT", tmp_path)

    def run_cmd(cmd, cwd=".", env_name=None, timeout=600, tool=None):
        return SimpleNamespace(returncode=128, stdout="",
                               stderr="fatal: repository not found")

    manifest = {"preset": None, "repo_url": "https://github.com/acme/gone",
                "ref": "", "decision": "upgrade numpy to 2.0",
                "guide_url": "", "package": ""}
    ctx, _ = _generic_ctx(manifest, run_cmd)
    with pytest.raises(CloneFailed, match="clone failed"):
        stages_mod.stage_setup(ctx)


def test_generic_setup_replay_verifies_workspace(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.runs import stages as stages_mod
    from chainreaction.runs.context import CommitMismatch
    from types import SimpleNamespace

    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    monkeypatch.setattr(stages_mod, "ROOT", tmp_path)
    ws_rel = f"workspaces/acme__demo@{SHA[:12]}"
    _seed_repo(tmp_path / ws_rel)
    uv = _uv_fake(tmp_path, [])

    def replay_cmd(cmd, cwd=".", env_name=None, timeout=600, tool=None):
        # Replay serves the uv probe and freezes from the recording;
        # git and installs never run.
        if cmd[0] == "git":
            raise AssertionError("replay must not run git")
        if cmd[:3] == ["uv", "pip", "install"] or cmd[:2] == ["uv", "venv"]:
            raise AssertionError("reused envs must not rebuild")
        hit = uv(cmd, env_name)
        assert hit is not None, f"unexpected replay cmd: {cmd}"
        return hit

    base = {"preset": None, "repo_url": "https://github.com/acme/demo",
            "ref": SHA, "decision": "upgrade numpy to 2.0", "guide_url": "",
            "package": "", "mode": "replay", "resolved_commit": SHA,
            "commit_date": "2023-05-01",
            "env_provenance": {"before": "reused", "after": "reused"}}
    monkeypatch.setattr(generic_mod, "git_head", lambda p: SHA)
    ctx, saved = _generic_ctx(dict(base), replay_cmd)
    stages_mod.stage_setup(ctx)
    assert ctx.state["workspace"] == ws_rel
    assert saved["envs.json"]["before"]["freeze"]["numpy"] == "1.26.4"

    monkeypatch.setattr(generic_mod, "git_head", lambda p: "b" * 40)
    ctx, _ = _generic_ctx(dict(base), replay_cmd)
    with pytest.raises(CommitMismatch, match="recording needs"):
        stages_mod.stage_setup(ctx)

    import shutil
    shutil.rmtree(tmp_path / ws_rel)
    ctx, _ = _generic_ctx(dict(base), replay_cmd)
    with pytest.raises(RuntimeError, match="not on disk"):
        stages_mod.stage_setup(ctx)


def test_fetch_keeps_json_parseable_and_pypi_maps_404(tmp_runs):
    import urllib.error

    from chainreaction.generic import VersionNotFound, fetch_pypi_info
    from chainreaction.runs import store
    from chainreaction.runs.context import RunContext

    body = b'{"releases": {"2.0.2": []}, "info": {"version":  "2.0.2"}}'
    manifest = {"run_id": "j1", "mode": "live", "status": "running"}
    store.write_json(store.run_dir("j1") / store.MANIFEST, manifest)
    live = RunContext(run_id="j1", manifest=manifest, mode="live",
                      live_fetch=lambda url, timeout: (200, body))
    assert json.loads(str(live.fetch("https://pypi.org/pypi/x/json")))[
        "info"]["version"] == "2.0.2"
    assert fetch_pypi_info(live, "x")["info"]["version"] == "2.0.2"

    def missing(url, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    live2 = RunContext(run_id="j1", manifest=manifest, mode="live",
                       live_fetch=missing)
    with pytest.raises(VersionNotFound, match="not found on PyPI"):
        fetch_pypi_info(live2, "nope")


def test_resolve_decision_llm_fallback_and_bad_version():
    from chainreaction.generic import resolve_decision

    def fetch(url, timeout=60):
        return json.dumps({"releases": {"3.0.0": []},
                           "info": {"version": "3.0.0",
                                    "project_urls": {}}})

    def llm(system, user, max_tokens=500, response_format=None):
        return json.dumps({"package": "pandas", "to_version": "3.0"})

    ctx, _ = _generic_ctx({"preset": None}, lambda *a, **k: None,
                          fetch=fetch, llm=llm)
    decided = resolve_decision(ctx, "migrate to the new pandas")
    assert (decided.package, decided.to_version) == ("pandas", "3.0.0")

    ctx2, _ = _generic_ctx({"preset": None}, lambda *a, **k: None,
                           fetch=fetch)
    with pytest.raises(VersionNotFound, match="not on PyPI"):
        resolve_decision(ctx2, "upgrade pandas to 9.9")


def test_cmd_key_normalizes_env_python_args(tmp_runs):
    from chainreaction.runs import store
    from chainreaction.runs.context import RunContext

    manifest = {"run_id": "k1", "mode": "live", "status": "running"}
    store.write_json(store.run_dir("k1") / store.MANIFEST, manifest)
    ctx = RunContext(run_id="k1", manifest=manifest, mode="live")
    live = ctx._cmd_key(["uv", "pip", "install", "--python",
                         "<env:before>/bin/python", "numpy==2.0.2"],
                        None, "uv")
    replay = ctx._cmd_key(["uv", "pip", "install", "--python",
                           "<env-python:k/before>", "numpy==2.0.2"],
                          None, "uv")
    assert live == replay
    # Preset keys carry no env parts and are unchanged.
    probe = ctx._cmd_key(["/abs/python", "-c", "import nibabel"],
                         "numpy2", None)
    assert probe[0] == "<env-python>"
    assert probe[2] == "import nibabel"


def test_env_build_pure_helpers():
    from chainreaction.generic import (
        collateral_diff,
        constraints_for,
        find_conflicting_pin,
        freeze_dict,
        toolchain_hint,
        version_tuple,
    )

    assert version_tuple("1.26.4") < version_tuple("2.0")
    assert version_tuple("2.0") == version_tuple("2.0.0")
    freeze = freeze_dict("numpy==1.26.4\n-e . \nPyTest==7.4.0\n")
    assert freeze == {"numpy": "1.26.4", "pytest": "7.4.0"}
    assert constraints_for(freeze, "numpy") == ["pytest==7.4.0"]
    assert constraints_for(freeze, "numpy", "pytest") == []
    assert constraints_for(freeze, "numpy", "pytest") == \
        constraints_for(freeze, "numpy", "PyTest")
    assert collateral_diff({"a": "1", "n": "1"}, {"a": "2", "n": "9"},
                           "n") == {"a": {"before": "1", "after": "2"}}
    # The package under test (rebuild version-string drift) and our own
    # tooling (coverage dropped on purpose) are not collateral.
    assert collateral_diff(
        {"anndata": "0.11+gabc", "coverage": "7.5", "numba": "0.59"},
        {"anndata": "0.11+gabc1", "numba": "0.68"},
        "numpy", repo_package="anndata") == {
        "numba": {"before": "0.59", "after": "0.68"}}
    err = "Because demopkg depends on scipy==1.11 and scipy==1.11 fails"
    assert find_conflicting_pin(err, ["scipy", "pytest"]) == "scipy"
    assert find_conflicting_pin("mysterious failure", ["scipy"]) is None
    assert "toolchain" in toolchain_hint("error: Microsoft Visual C++")
    assert toolchain_hint("plain error") == ""


def _env_ctx(tmp_path, monkeypatch, run_cmd, decision=None):
    import chainreaction.generic as generic_mod
    from chainreaction.generic import Decision, ProjectInfo

    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    manifest: dict = {"preset": None, "mode": "live"}
    ctx, saved = _generic_ctx(manifest, run_cmd)
    info = ProjectInfo(package="demopkg", python_version="3.11",
                       test_command=["pytest", "-q"], has_suite=True)
    return (ctx, saved,
            decision or Decision("numpy", "2.0.2", "upgrade numpy to 2.0"),
            info)


def test_build_envs_constrains_before_and_relaxes_pins(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from types import SimpleNamespace

    freezes = {"before": ["numpy==2.1.0\nscipy==1.11.0\n",
                          "numpy==1.26.4\nscipy==1.11.0\n"],
               # After is frozen twice: the coverage-drop check sees no
               # coverage here, then the final freeze records the env.
               "after": ["numpy==2.0.2\nscipy==1.14.0\n",
                         "numpy==2.0.2\nscipy==1.14.0\n"]}
    installs = []
    cleans = []
    uv = _uv_fake(tmp_path, [])

    def run_cmd(cmd, cwd=".", env_name=None, timeout=900, tool=None):
        if cmd[0] == "git":
            cleans.append(list(cmd))
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if cmd[:3] == ["uv", "pip", "install"]:
            installs.append((env_name, list(cmd)))
            if (env_name is None and "--constraint" in cmd
                    and len([i for i in installs
                             if "--constraint" in i[1]]) == 1):
                return SimpleNamespace(
                    returncode=1, stdout="",
                    stderr="Because demopkg depends on scipy==1.11.0, "
                           "requirements are unsatisfiable",
                    missing=None)
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if len(cmd) > 2 and cmd[1] == "-c" and "importlib.metadata" \
                in cmd[2]:
            role = "after" if (env_name or "").endswith("/after") \
                else "before"
            return SimpleNamespace(returncode=0,
                                   stdout=freezes[role].pop(0), stderr="",
                                   missing=None)
        hit = uv(cmd, env_name)
        assert hit is not None, f"unexpected cmd: {cmd}"
        return hit

    ctx, saved, decision, info = _env_ctx(tmp_path, monkeypatch, run_cmd)
    messages = []
    ctx.emit = lambda t, d: messages.append(d["message"])
    record = generic_mod.build_envs(
        ctx, key="acme__demo@aaaaaaaaaaaa", ws_rel="workspaces/w",
        info=info, decision=decision, commit_date="2023-05-01", sha=SHA)
    assert record["before"]["freeze"]["numpy"] == "1.26.4"
    assert "constrained to <2.0.2" in record["before"]["note"]
    assert any("numpy<2.0.2" in c for _, c in installs)
    assert record["after"]["freeze"]["scipy"] == "1.14.0"
    assert record["collateral"]["scipy"] == {"before": "1.11.0",
                                             "after": "1.14.0"}
    assert record["conflict"] is None
    assert cleans == [["git", "-C", "workspaces/w", "clean", "-fdx"]]
    # The blamed pin relaxed to a floor (never removed), then resolved.
    assert len([i for i in installs if "--constraint" in i[1]]) == 2
    assert (tmp_path / "workspaces" / "w"
            / ".chainreaction-after-constraints.txt").read_text(
                encoding="utf-8") == "scipy>=1.11.0\n"
    assert any("relax scipy to its before floor" in m for m in messages)


def test_build_envs_pins_before_line_on_from_version(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.generic import Decision
    from types import SimpleNamespace

    installs = []
    uv = _uv_fake(tmp_path, [])

    def run_cmd(cmd, cwd=".", env_name=None, timeout=900, tool=None):
        if cmd[0] == "git":
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if cmd[:3] == ["uv", "pip", "install"]:
            installs.append((env_name, list(cmd)))
        hit = uv(cmd, env_name)
        assert hit is not None, f"unexpected cmd: {cmd}"
        return hit

    ctx, saved, _, info = _env_ctx(tmp_path, monkeypatch, run_cmd)
    decision = Decision("numpy", "2.0.2", "upgrade numpy from 1.26 to 2.0",
                        "1.26")
    record = generic_mod.build_envs(
        ctx, key="acme__demo@aaaaaaaaaaaa", ws_rel="workspaces/w",
        info=info, decision=decision, commit_date="2023-05-01", sha=SHA)
    # Installs carry env_name=None; the env rides in --python <envpy:...>.
    before_cmds = [c for _, c in installs if "/before>" in " ".join(c)]
    after_cmds = [c for _, c in installs if "/after>" in " ".join(c)]
    assert before_cmds and after_cmds
    assert any("numpy==1.26.*" in c for c in before_cmds)
    assert not any("numpy==1.26.*" in c for c in after_cmds)
    assert record["decision"]["from_version"] == "1.26"


def test_install_requires_specs_sources(tmp_path):
    from chainreaction.generic import install_requires_specs

    assert install_requires_specs(tmp_path) is None
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["a>=1"]\n', encoding="utf-8")
    assert install_requires_specs(tmp_path) == ["a>=1"]
    (tmp_path / "setup.cfg").write_text(
        "[options]\ninstall_requires =\n    b\n    c  # pinned\n",
        encoding="utf-8")
    # pyproject wins while present.
    assert install_requires_specs(tmp_path) == ["a>=1"]
    (tmp_path / "pyproject.toml").unlink()
    assert install_requires_specs(tmp_path) == ["b", "c"]
    (tmp_path / "setup.cfg").unlink()
    (tmp_path / "setup.py").write_text(
        "setup(name='x', install_requires=['d'])\n", encoding="utf-8")
    assert install_requires_specs(tmp_path) == ["d"]


def _dep_only_ctx(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.generic import ProjectInfo
    from types import SimpleNamespace

    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    ws_rel = "workspaces/w"
    (tmp_path / ws_rel).mkdir(parents=True)
    (tmp_path / ws_rel / "setup.py").write_text(
        "setup(name='w', install_requires=['dep>=2'])\n", encoding="utf-8")
    seen = []

    def run_cmd(cmd, cwd=".", env_name=None, timeout=900, tool=None):
        if cmd[:3] == ["uv", "pip", "install"]:
            seen.append(list(cmd))
            if "." in cmd:
                return SimpleNamespace(returncode=1, stdout="", stderr="boom",
                                       missing=None, timed_out=False)
        return SimpleNamespace(returncode=0, stdout="", stderr="",
                               missing=None, timed_out=False)

    ctx, _ = _generic_ctx({"preset": None, "mode": "live"}, run_cmd)
    info = ProjectInfo(package="demopkg", python_version="3.11",
                       test_command=["pytest", "-q"], has_suite=True)
    return ctx, info, seen, ws_rel


def test_best_effort_falls_back_to_dependencies_only(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod

    ctx, info, seen, ws_rel = _dep_only_ctx(tmp_path, monkeypatch)
    ok, skipped = generic_mod._install_best_effort(
        ctx, ws_rel, "w/before", info, [], ["."], [])
    assert (ok, info.editable) == (True, False)
    assert any("dep>=2" in c for c in seen)
    # Without static deps the lead stays fatal.
    (tmp_path / ws_rel / "setup.py").unlink()
    info.editable = True
    ok, _ = generic_mod._install_best_effort(
        ctx, ws_rel, "w/before", info, [], ["."], [])
    assert (ok, info.editable) == (False, True)


def test_best_effort_dep_only_keeps_anchor(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod

    ctx, info, seen, ws_rel = _dep_only_ctx(tmp_path, monkeypatch)
    ok, _ = generic_mod._install_best_effort(
        ctx, ws_rel, "w/after", info, [], ["numpy==2.0.2", "."],
        ["numpy==2.0.2"], before_freeze={},
        anchor=["numpy==2.0.2"])
    assert (ok, info.editable) == (True, False)
    # The decision pin lands on its own, without the repo build.
    assert any("numpy==2.0.2" in c and "." not in c for c in seen)


def test_detect_project_checkout_pythonpath(tmp_path):
    _pkg(tmp_path, "flat")
    assert detect_project(tmp_path).pythonpath == ""
    (tmp_path / "flat").rename(tmp_path / "flat-disabled")
    _pkg(tmp_path, "deep", src_layout=True)
    got = detect_project(tmp_path, package_override="deep")
    assert (got.package, got.pythonpath) == ("deep", "src")


def test_build_envs_drops_blocking_floored_pin(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    import json as json_mod
    from types import SimpleNamespace

    before_txt = "numpy==1.26.4\nscipy==1.11.0\n"
    after_txt = "numpy==2.0.2\nscipy==1.14.0\n"
    freezes = {"before": [before_txt] * 3, "after": [after_txt] * 4}
    installs = []
    uv = _uv_fake(tmp_path, [])

    def fail(stderr):
        return SimpleNamespace(returncode=1, stdout="", stderr=stderr,
                               missing=None)

    def run_cmd(cmd, cwd=".", env_name=None, timeout=900, tool=None):
        if cmd[0] == "git":
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if cmd[:3] == ["uv", "pip", "install"]:
            installs.append((env_name, list(cmd)))
            if "--constraint" in cmd:
                n = len([i for i in installs if "--constraint" in i[1]])
                if n == 1:
                    return fail("Because demopkg depends on scipy==1.11.0, "
                                "requirements are unsatisfiable")
                if n == 2:
                    return fail("Because demopkg depends on scipy>=1.11.0, "
                                "requirements are unsatisfiable")
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if len(cmd) > 2 and cmd[1] == "-c" and "importlib.metadata" \
                in cmd[2]:
            role = "after" if (env_name or "").endswith("/after") \
                else "before"
            return SimpleNamespace(returncode=0,
                                   stdout=freezes[role].pop(0), stderr="",
                                   missing=None)
        hit = uv(cmd, env_name)
        assert hit is not None, f"unexpected cmd: {cmd}"
        return hit

    ctx, saved, decision, info = _env_ctx(tmp_path, monkeypatch, run_cmd)
    messages = []
    ctx.emit = lambda t, d: messages.append(d["message"])
    record = generic_mod.build_envs(
        ctx, key="acme__demo@aaaaaaaaaaaa", ws_rel="workspaces/w",
        info=info, decision=decision, commit_date="2023-05-01", sha=SHA)
    assert record["conflict"] is None
    assert record["dropped_pins"] == ["scipy"]
    assert record["collateral"]["scipy"] == {"before": "1.11.0",
                                             "after": "1.14.0"}
    assert any("drop scipy" in m for m in messages)
    assert (tmp_path / "workspaces" / "w"
            / ".chainreaction-after-constraints.txt").read_text(
                encoding="utf-8") == ""
    marker = json_mod.loads(
        (tmp_path / "envs" / "acme__demo@aaaaaaaaaaaa" / "after"
         / ".chainreaction.json").read_text(encoding="utf-8"))
    assert marker["dropped_pins"] == ["scipy"]
    # Reuse carries the recorded drops.
    again = generic_mod.build_envs(
        ctx, key="acme__demo@aaaaaaaaaaaa", ws_rel="workspaces/w",
        info=info, decision=decision, commit_date="2023-05-01", sha=SHA)
    assert again["dropped_pins"] == ["scipy"]


def test_build_envs_conflict_is_a_finding(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from types import SimpleNamespace

    uv = _uv_fake(tmp_path, [])

    def run_cmd(cmd, cwd=".", env_name=None, timeout=900, tool=None):
        if cmd[0] == "git":
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if cmd[:3] == ["uv", "pip", "install"] and "--constraint" in cmd:
            return SimpleNamespace(returncode=1, stdout="",
                                   stderr="mysterious resolver failure",
                                   missing=None)
        hit = uv(cmd, env_name)
        assert hit is not None, f"unexpected cmd: {cmd}"
        return hit

    ctx, saved, decision, info = _env_ctx(tmp_path, monkeypatch, run_cmd)
    record = generic_mod.build_envs(
        ctx, key="acme__demo@aaaaaaaaaaaa", ws_rel="workspaces/w",
        info=info, decision=decision, commit_date="2023-05-01", sha=SHA)
    assert record["after"] is None
    assert record["conflict"]["package"] == "numpy"
    assert "mysterious" in record["conflict"]["detail"]
    assert ctx.manifest["env_provenance"]["after"] == "conflict"
    assert ctx.state["dependency_conflict"] == record["conflict"]


def test_build_envs_failure_modes_are_typed(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.generic import InstallFailed
    from types import SimpleNamespace

    def no_uv(cmd, cwd=".", env_name=None, timeout=900, tool=None):
        return SimpleNamespace(returncode=None, stdout="", stderr="",
                               missing="<tool:uv>")

    ctx, _, decision, info = _env_ctx(tmp_path, monkeypatch, no_uv)
    with pytest.raises(InstallFailed, match="uv.*not found"):
        generic_mod.build_envs(
            ctx, key="k", ws_rel="w", info=info, decision=decision,
            commit_date="2023-05-01", sha=SHA)

    uv = _uv_fake(tmp_path, [])

    def bad_build(cmd, cwd=".", env_name=None, timeout=900, tool=None):
        if cmd[:3] == ["uv", "pip", "install"]:
            return SimpleNamespace(
                returncode=1, stdout="",
                stderr="error: Microsoft Visual C++ 14.0 is required",
                missing=None)
        hit = uv(cmd, env_name)
        assert hit is not None
        return hit

    ctx2, _, _, _ = _env_ctx(tmp_path, monkeypatch, bad_build)
    with pytest.raises(InstallFailed, match="system toolchain"):
        generic_mod.build_envs(
            ctx2, key="k", ws_rel="w", info=info, decision=decision,
            commit_date="2023-05-01", sha=SHA)


def test_propose_guide_url_extracts_single_url():
    from chainreaction.generic import propose_guide_url

    assert propose_guide_url(
        lambda *a, **k: "See https://numpy.org/doc/migration.html (official).",
        "numpy", "2.0") == "https://numpy.org/doc/migration.html"
    assert propose_guide_url(lambda *a, **k: "I don't know", "x",
                             "1") == ""


def _fetched(text):
    from chainreaction.interpret import FetchedText

    return FetchedText(text)


def test_discover_guide_live_order():
    import urllib.error

    from chainreaction.generic import GuideNotFound, discover_guide

    # Request URL wins without any model call.
    ctx, _ = _generic_ctx(
        {"preset": None, "guide_url": "https://req/guide"},
        lambda *a, **k: None,
        fetch=lambda url, timeout=60: _fetched("whatever"),
        llm=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no proposal needed")))
    text, url, method = discover_guide(ctx, package="numpy",
                                       to_version="2.0.2")
    assert (url, method) == ("https://req/guide", "request")

    # Model proposal, fetch-verified.
    ctx, _ = _generic_ctx(
        {"preset": None, "guide_url": ""}, lambda *a, **k: None,
        fetch=lambda url, timeout=60: _fetched("numpy 2.0 migration"),
        llm=lambda *a, **k: "https://numpy.org/migration")
    _, url, method = discover_guide(ctx, package="numpy",
                                    to_version="2.0.2")
    assert (url, method) == ("https://numpy.org/migration", "model")
    assert ctx.manifest["guide_url"] == url

    # Dead proposal falls through to the PyPI links.
    def flaky(url, timeout=60):
        if "proposed" in url:
            raise urllib.error.URLError("dns")
        if "bad" in url:
            raise urllib.error.URLError("404")
        return _fetched("click changelog")

    ctx, _ = _generic_ctx(
        {"preset": None, "guide_url": ""}, lambda *a, **k: None,
        fetch=flaky, llm=lambda *a, **k: "https://proposed/guide")
    ctx.state["pypi_guide_urls"] = ["https://pypi/bad", "https://pypi/good"]
    _, url, method = discover_guide(ctx, package="click",
                                    to_version="8.2")
    assert (url, method) == ("https://pypi/good", "pypi")
    assert ctx.manifest["guide_proposal_fetched"] is False

    # Fetched-but-unrelated proposal still falls through (flagged).
    ctx, _ = _generic_ctx(
        {"preset": None, "guide_url": ""}, lambda *a, **k: None,
        fetch=lambda url, timeout=60: _fetched("unrelated page")
        if "proposed" in url else _fetched("click changelog"),
        llm=lambda *a, **k: "https://proposed/guide")
    ctx.state["pypi_guide_urls"] = ["https://pypi/good"]
    _, url, method = discover_guide(ctx, package="click",
                                    to_version="8.2")
    assert method == "pypi"
    assert ctx.manifest["guide_proposal_fetched"] is True

    # Nothing anywhere asks for a URL.
    ctx, _ = _generic_ctx(
        {"preset": None, "guide_url": ""}, lambda *a, **k: None,
        fetch=lambda url, timeout=60: (_ for _ in ()).throw(
            urllib.error.URLError("down")),
        llm=lambda *a, **k: "https://proposed/guide")
    ctx.state["pypi_guide_urls"] = []
    with pytest.raises(GuideNotFound, match="pass guide_url"):
        discover_guide(ctx, package="click", to_version="8.2")


def test_discover_guide_never_swallows_cancel():
    from chainreaction.generic import discover_guide
    from chainreaction.runs.context import Cancelled

    def cancel(url, timeout=60):
        raise Cancelled("stop")

    ctx, _ = _generic_ctx(
        {"preset": None, "guide_url": ""}, lambda *a, **k: None,
        fetch=cancel, llm=lambda *a, **k: "https://proposed/guide")
    with pytest.raises(Cancelled):
        discover_guide(ctx, package="x", to_version="1")


def test_discover_guide_replay_paths():
    from chainreaction.generic import discover_guide

    def boom_llm(*a, **k):
        raise AssertionError("request replays make no proposal")

    seen = []
    ctx, _ = _generic_ctx(
        {"preset": None, "mode": "replay", "guide_url": "https://final",
         "guide_method": "request"}, lambda *a, **k: None,
        fetch=lambda url, timeout=60: seen.append(url) or _fetched("t"),
        llm=boom_llm)
    _, url, method = discover_guide(ctx, package="x", to_version="1")
    assert (url, method, seen) == ("https://final", "request",
                                   ["https://final"])

    ctx, _ = _generic_ctx(
        {"preset": None, "mode": "replay",
         "guide_url": "https://proposed",
         "guide_method": "model"}, lambda *a, **k: None,
        fetch=lambda url, timeout=60: seen.append(url) or _fetched("t"),
        llm=lambda *a, **k: "https://proposed")
    del seen[:]
    _, url, method = discover_guide(ctx, package="x", to_version="1")
    assert (url, method, seen) == ("https://proposed", "model",
                                   ["https://proposed"])

    ctx, _ = _generic_ctx(
        {"preset": None, "mode": "replay", "guide_url": "https://pypi/good",
         "guide_method": "pypi",
         "guide_proposal_fetched": True}, lambda *a, **k: None,
        fetch=lambda url, timeout=60: seen.append(url) or _fetched("t"),
        llm=lambda *a, **k: "https://proposed")
    del seen[:]
    _, url, method = discover_guide(ctx, package="x", to_version="1")
    assert (url, method, seen) == ("https://pypi/good", "pypi",
                                   ["https://proposed", "https://pypi/good"])


def test_package_imported_anywhere_with_aliases(tmp_path):
    from chainreaction.generic import package_imported_anywhere

    (tmp_path / "a.py").write_text("import numpy as np\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from os import path\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("raise SyntaxError(\n", encoding="utf-8")
    assert package_imported_anywhere(tmp_path, "numpy") is True
    assert package_imported_anywhere(tmp_path, "scipy") is False
    (tmp_path / "d.py").write_text("from PIL import Image\n",
                                   encoding="utf-8")
    assert package_imported_anywhere(tmp_path, "Pillow") is True
    assert package_imported_anywhere(tmp_path, "scikit-learn") is False


def test_generic_interpret_needs_key_live(tmp_path, monkeypatch):
    from chainreaction.generic import Decision
    from chainreaction.runs import stages as stages_mod
    from chainreaction.scenarios import Scenario
    from types import SimpleNamespace

    monkeypatch.setattr("chainreaction.llm.has_key", lambda: False)
    ctx = SimpleNamespace(
        manifest={"preset": None, "decision": "upgrade numpy to 2.0"},
        mode="live",
        state={"scenario": Scenario(id="g", decision="d", target="t",
                                    package="p", guide_url=""),
               "decision_parsed": Decision("numpy", "2.0.2")},
        llm=lambda *a, **k: "", fetch=lambda *a, **k: "",
        emit=lambda *a, **k: None, set_manifest={}.update,
        save_artifact=lambda n, o: None)
    with pytest.raises(RuntimeError, match="MODEL_API_KEY"):
        stages_mod.stage_interpret(ctx)


def _m3_stage_ctx(guide, llm, manifest=None, emits=None):
    from chainreaction.generic import Decision
    from chainreaction.interpret import FetchedText
    from chainreaction.scenarios import Scenario
    from types import SimpleNamespace

    saved = {}
    manifest = {"preset": None, "decision": "upgrade numpy to 2.0",
                "guide_url": "https://guide",
                **(manifest or {})}

    def fetch(url, timeout=60, clean="text"):
        body = b"" if "pypi.org" in url else guide.encode()
        fetched = FetchedText(body.decode() if body else "")
        fetched.status = 404 if "pypi.org" in url else 200
        fetched.bytes = len(body)
        fetched.sha256 = "ab" * 32
        return fetched

    ctx = SimpleNamespace(
        manifest=manifest, mode="live",
        state={"scenario": Scenario(id="g", decision="d", target="t",
                                    package="demopkg", guide_url=""),
               "decision_parsed": Decision("numpy", "2.0.2")},
        llm=llm, fetch=fetch,
        emit=(lambda t, d: emits.append((t, d))
              if emits is not None else lambda *a, **k: None),
        set_manifest=manifest.update,
        save_artifact=lambda n, o: saved.update({n: o}))
    return ctx, saved


def test_generic_interpret_model_path(monkeypatch):
    import json as json_mod

    from chainreaction.generic import Decision
    from chainreaction.interpret import FetchedText
    from chainreaction.runs import stages as stages_mod
    from chainreaction.scenarios import Scenario
    from types import SimpleNamespace

    monkeypatch.setattr("chainreaction.llm.has_key", lambda: True)
    guide = ("numpy sctypes was removed in 2.0, use explicit dtypes. "
             + "padding prose. " * 20)
    calls = []
    saved = {}
    manifest = {"preset": None, "decision": "upgrade numpy to 2.0",
                "guide_url": "https://guide"}

    def llm(system, user, max_tokens=8000, response_format=None):
        calls.append(user)
        return json_mod.dumps({
            "decision": "upgrade numpy to 2.0",
            "source_url": "https://guide",
            "items": [{"symbol": "numpy.sctypes", "kind": "removed",
                       "quote": "sctypes was removed",
                       "replacement": None,
                       "source_url": "https://guide",
                       "introduced_in": "2.0.2",
                       "detail": {"expected": None, "actual": None,
                                  "note": None}}]})

    def fetch(url, timeout=60, clean="text"):
        body = b"" if "pypi.org" in url else guide.encode()
        fetched = FetchedText(body.decode() if body else "")
        fetched.status = 404 if "pypi.org" in url else 200
        fetched.bytes = len(body)
        fetched.sha256 = "ab" * 32
        return fetched

    ctx = SimpleNamespace(
        manifest=manifest, mode="live",
        state={"scenario": Scenario(id="g", decision="d", target="t",
                                    package="demopkg", guide_url=""),
               "decision_parsed": Decision("numpy", "2.0.2")},
        llm=llm, fetch=fetch,
        emit=lambda *a, **k: None, set_manifest=manifest.update,
        save_artifact=lambda n, o: saved.update({n: o}))
    stages_mod.stage_interpret(ctx)
    assert manifest["changeset_source"] == "muse-spark"
    assert manifest["interpret_flow"] == "m3"
    assert [i["symbol"] for i in
            saved["changeset.json"]["items"]] == ["numpy.sctypes"]
    assert saved["changeset.json"]["items"][0]["introduced_in"] == "2.0.2"
    assert saved["sources.json"]["package"] == "numpy"
    assert ctx.state["symbols"] == {"numpy.sctypes"}
    assert len(calls) == 1  # prose-only guide: single blind call


def test_generic_interpret_survival_gate_fails_run(monkeypatch):
    import json as json_mod

    import pytest

    from chainreaction.runs import stages as stages_mod

    monkeypatch.setattr("chainreaction.llm.has_key", lambda: True)
    guide = "release notes prose. " * 30

    def llm(system, user, max_tokens=8000, response_format=None):
        return json_mod.dumps({"items": [
            {"symbol": "numpy.a", "kind": "removed",
             "quote": "fabricated one", "replacement": None},
            {"symbol": "numpy.b", "kind": "removed",
             "quote": "fabricated two", "replacement": None},
        ]})

    ctx, _ = _m3_stage_ctx(guide, llm)
    with pytest.raises(RuntimeError, match="survival rate 0.00 below"):
        stages_mod.stage_interpret(ctx)


def test_generic_interpret_blind_recall_warns(monkeypatch):
    import json as json_mod

    from chainreaction.runs import stages as stages_mod

    monkeypatch.setattr("chainreaction.llm.has_key", lambda: True)
    guide = ("## 2.0.2\n\n### Removed\n\n- `gone` is gone.\n\n"
             + "padding prose. " * 20)

    def llm(system, user, max_tokens=8000, response_format=None):
        if "Deterministic extraction" in user:
            items = [{"symbol": "numpy.gone", "kind": "removed",
                      "quote": "`gone` is gone.", "replacement": None,
                      "source_url": "https://guide",
                      "introduced_in": "2.0.2"}]
        else:
            items = []
        return json_mod.dumps({"items": items})

    emits = []
    ctx, saved = _m3_stage_ctx(guide, llm, emits=emits)
    stages_mod.stage_interpret(ctx)  # warns, does not fail
    assert any("blind recall 0.00 below" in d.get("message", "")
               for _, d in emits)
    assert [i["symbol"] for i in
            saved["changeset.json"]["items"]] == ["numpy.gone"]


def test_generic_predict_untouched_and_touched(tmp_path, monkeypatch):
    from chainreaction.generic import Decision
    from chainreaction.runs import stages as stages_mod
    from chainreaction.scenarios import Scenario
    from types import SimpleNamespace

    monkeypatch.setattr(stages_mod, "ROOT", tmp_path)
    ws = tmp_path / "ws"
    _seed_repo(ws)

    def make_ctx():
        saved = {}
        state = {
            "scenario": Scenario(id="g", decision="d", target="ws",
                                 package="demopkg", guide_url=""),
            "symbols": {"numpy.sctypes"}, "config": [],
            "deprecated": set(), "changed": set(),
            "decision_parsed": Decision("numpy", "2.0.2")}
        return SimpleNamespace(
            manifest={"preset": None}, mode="live", state=state,
            emit=lambda *a, **k: None,
            save_artifact=lambda n, o: saved.update({n: o})), saved

    ctx, saved = make_ctx()
    stages_mod.stage_predict(ctx)
    assert ctx.state["untouched"] is True
    assert saved["prediction.json"]["hard"] == []
    assert "doesn't touch" in saved["prediction.json"]["untouched_warning"]

    (ws / "demopkg" / "uses.py").write_text("import numpy\n",
                                            encoding="utf-8")
    ctx, saved = make_ctx()
    stages_mod.stage_predict(ctx)
    assert ctx.state["untouched"] is False
    assert "hard" in saved["prediction.json"]


def _verify_ctx(tmp_path, monkeypatch, run_cmd, *, has_suite=True,
                conflict=None, graph=None, hard=frozenset()):
    from chainreaction.generic import Decision, ProjectInfo
    from chainreaction.runs import stages as stages_mod
    from chainreaction.runs.scrub import Scrubber
    from chainreaction.scenarios import Scenario
    from types import SimpleNamespace

    monkeypatch.setattr(stages_mod, "ROOT", tmp_path)
    saved: dict = {}
    manifest: dict = {"preset": None, "mode": "live"}
    state = {
        "scenario": Scenario(
            id="g", decision="upgrade numpy to 2.0", target="ws",
            package="demopkg", guide_url="",
            verify_env="k/after", before_env="k/before"),
        "project": ProjectInfo(package="demopkg", python_version="3.11",
                               test_command=["pytest", "-q"],
                               has_suite=has_suite),
        "workspace": "ws",
        "decision_parsed": Decision("numpy", "2.0.2"),
        "dependency_conflict": conflict,
        "untouched": False,
        "graph": graph,
        "hard": set(hard),
        "at_risk": [],
        "symbols": {"numpy.sctypes"},
    }
    ctx = SimpleNamespace(
        manifest=manifest, mode="live", state=state, run_cmd=run_cmd,
        env_python=lambda name: f"<envpy:{name}>",
        scrubber=Scrubber.default(),
        emit=lambda *a, **k: None, set_manifest=manifest.update,
        save_artifact=lambda n, o: saved.update({n: o}))
    return ctx, saved


def _suite_fake(cov_out, dump, after_out, uses_path, calls):
    from types import SimpleNamespace

    def run_cmd(cmd, cwd=".", env_name=None, timeout=1200, extra_env=None,
                tool=None):
        calls.append(cmd)
        if "--cov=demopkg" in cmd:
            return SimpleNamespace(returncode=1, stdout=cov_out, stderr="",
                                   missing=None, timed_out=False)
        if len(cmd) > 2 and cmd[1] == "-c" and "contexts_by_lineno" in cmd[2]:
            return SimpleNamespace(returncode=0, stdout=dump, stderr="",
                                   missing=None, timed_out=False)
        if "pytest" in cmd:
            # A pytest run without --cov is the after suite in the
            # after env (with the conftest, escalating on collapse)
            # or the plain before fallback in the before env.
            out = after_out if (env_name or "").endswith("/after") \
                else cov_out
            return SimpleNamespace(returncode=1, stdout=out, stderr="",
                                   missing=None, timed_out=False)
        if len(cmd) > 2 and cmd[1] == "-c" and "NUMPY=" in cmd[2]:
            after_env = (env_name or "").endswith("/after")
            err = (f'  File "{uses_path}", line 1, in <module>\n'
                   "ImportError: sctypes\n") if after_env else ""
            return SimpleNamespace(
                returncode=1 if after_env else 0,
                stdout=f"NUMPY={'2.0.2' if after_env else '1.26.4'}\n",
                stderr=err, missing=None, timed_out=False)
        raise AssertionError(f"unexpected cmd: {cmd}")

    return run_cmd


def test_generic_verify_scores_with_baseline_exclusion(tmp_path, monkeypatch):
    import json as json_mod

    from chainreaction.graph.repo import build_graph_from_repo
    from chainreaction.runs import stages as stages_mod

    ws = tmp_path / "ws"
    _seed_repo(ws)
    uses = ws / "demopkg" / "uses.py"
    uses.write_text("import numpy\n", encoding="utf-8")
    (ws / "demopkg" / "other.py").write_text("x = 1\n", encoding="utf-8")
    ids = [f"tests/test_a.py::t{i}" for i in range(6)]
    cov_out = (f"FAILED {ids[0]} - flaky\n"
               "1 failed, 5 passed in 0.5s\n")
    after_out = (f"FAILED {ids[0]} - flaky\nFAILED {ids[1]} - boom\n"
                 "2 failed, 4 passed in 0.5s\n")
    dump = json_mod.dumps({
        str(uses): [f"ws/{ids[1]}"],
        str(ws / "demopkg" / "other.py"):
            [f"ws/{i}" for i in ids if i != ids[1]],
    })
    calls = []
    run_cmd = _suite_fake(cov_out, dump, after_out, str(uses), calls)
    graph = build_graph_from_repo(ws, "demopkg")
    ctx, saved = _verify_ctx(tmp_path, monkeypatch, run_cmd, graph=graph,
                             hard={"demopkg.uses"})
    stages_mod.stage_verify(ctx)
    scores = saved["scores.json"]
    assert scores["suites"] == "ok"
    # t0 failed before the decision: never blamed on it.
    assert scores["baseline_failed"] == [ids[0]]
    assert scores["actual_new"] == [ids[1]]
    assert scores["predicted_failing"] == [ids[1]]
    assert (scores["test_precision"], scores["test_recall"]) == (1.0, 1.0)
    assert scores["unhealthy_baseline"] is False
    assert scores["testmap_tests"] == 6
    assert [n for n, _ in ctx.state["probes"]] == ["after-import",
                                                  "before-import"]
    assert ctx.state["probes"][0][1].modules == ["demopkg.uses"]
    assert "# counts: 2 failed, 4 passed" in saved["verify/after-suite.log"]
    assert saved["testmap.json"]["tests"][ids[1]] == ["demopkg.uses"]


def test_generic_verify_conflict_skips_after(tmp_path, monkeypatch):
    from chainreaction.runs import stages as stages_mod

    ws = tmp_path / "ws"
    _seed_repo(ws)
    cov_out = "2 passed in 0.5s\n"
    calls = []
    run_cmd = _suite_fake(cov_out, "{}", None, str(ws), calls)
    conflict = {"package": "numpy", "to_version": "2.0.2",
                "detail": "demopkg caps numpy<2"}
    ctx, saved = _verify_ctx(tmp_path, monkeypatch, run_cmd,
                             conflict=conflict)
    stages_mod.stage_verify(ctx)
    assert not [c for c in calls if "--noconftest" in c]
    assert [n for n, _ in ctx.state["probes"]] == ["after (conflict)",
                                                  "before-import"]
    scores = saved["scores.json"]
    assert scores["suites"] == "conflict-no-after"
    assert scores["conflict"]["package"] == "numpy"
    assert scores["test_precision"] is None


def test_generic_verify_no_suite_probes_only(tmp_path, monkeypatch):
    from chainreaction.runs import stages as stages_mod

    ws = tmp_path / "ws"
    _seed_repo(ws)
    calls = []
    run_cmd = _suite_fake("", "{}", None, str(ws), calls)
    ctx, saved = _verify_ctx(tmp_path, monkeypatch, run_cmd,
                             has_suite=False)
    stages_mod.stage_verify(ctx)
    assert all("pytest" not in c for c in calls)
    assert saved["scores.json"]["suites"] == "skipped-no-suite"


def test_generic_report_keeps_schema_and_adds_md(tmp_path, monkeypatch):
    from chainreaction.generic import Decision, ProjectInfo
    from chainreaction.interpret import ChangeItem, ChangeSet
    from chainreaction.runs import stages as stages_mod
    from chainreaction.scenarios import Scenario
    from chainreaction.verifier import ProbeResult
    from types import SimpleNamespace
    import networkx as nx

    monkeypatch.setattr(stages_mod, "ROOT", tmp_path)
    monkeypatch.setattr("chainreaction.llm.has_key", lambda: False)
    ws = tmp_path / "ws"
    _seed_repo(ws)
    changeset = ChangeSet("upgrade numpy to 2.0", "https://guide",
                          [ChangeItem("numpy.sctypes", "removed",
                                      "q", verified=True)])
    probes = [
        ("after-import", ProbeResult(False, "2.0.2", [], ["demopkg"],
                                     "boom")),
        ("before-import", ProbeResult(True, "1.26.4", [], [], "")),
    ]
    saved: dict = {}
    manifest: dict = {"preset": None, "mode": "live"}
    state = {
        "scenario": Scenario(id="g", decision="d", target="ws",
                             package="demopkg", guide_url="",
                             verify_env="k/after", before_env="k/before",
                             run_ruff=False),
        "changeset": changeset, "symbols": {"numpy.sctypes"},
        "graph": nx.DiGraph(), "impacts": {}, "hard": [], "at_risk": [],
        "probes": probes,
        "interpret_stats": {"recall": None, "missed": [], "table_size": 0,
                            "quote_misses": [], "blind_hits": 0,
                            "blind_recall": None},
        "scores": {"suites": "ok", "test_precision": 1.0,
                   "test_recall": 0.5, "predicted_failing": ["a::t"],
                   "actual_new": ["a::t", "a::t2"], "baseline_failed": [],
                   "baseline_counts": {"passed": 2}, "after_counts": {},
                   "unhealthy_baseline": False, "conflict": None},
        "envs": {"collateral": {}}, "dependency_conflict": None,
        "untouched": False,
        "project": ProjectInfo(package="demopkg"),
    }
    ctx = SimpleNamespace(
        manifest=manifest, mode="live", state=state,
        run_cmd=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no subprocess expected")),
        emit=lambda *a, **k: None, set_manifest=manifest.update,
        save_artifact=lambda n, o: saved.update({n: o}))
    stages_mod.stage_report(ctx)
    report = saved["report.json"]
    assert "test_precision" not in report  # scores stay out of report.json
    assert report["spine"] == []
    assert "## Test suites" in saved["report.md"]
    assert "test precision/recall: 1.00/0.50" in saved["report.md"]


def test_extra_specs_and_req_lines(tmp_path):
    from chainreaction.generic import ProjectInfo, extra_specs, req_lines

    info = ProjectInfo(package="p", test_extras=["test"],
                       test_requirements=["requirements-test.txt"])
    assert extra_specs(tmp_path, info) is None
    (tmp_path / "pyproject.toml").write_text(
        '[project.optional-dependencies]\ntest = ["a>=1", "b"]\n',
        encoding="utf-8")
    (tmp_path / "requirements-test.txt").write_text(
        "# comment\n\ngoodpkg\n--index-url x\nbadpkg\n", encoding="utf-8")
    assert extra_specs(tmp_path, info) == ["a>=1", "b"]
    assert req_lines(tmp_path, "requirements-test.txt") == ["goodpkg",
                                                             "badpkg"]
    assert req_lines(tmp_path, "missing.txt") == []


def test_build_envs_before_falls_back_per_dependency(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.generic import Decision, ProjectInfo
    from types import SimpleNamespace

    ws = tmp_path / "workspaces" / "w"
    ws.mkdir(parents=True)
    (ws / "pyproject.toml").write_text(
        '[project.optional-dependencies]\ntest = ["okdep", "memray"]\n',
        encoding="utf-8")
    (ws / "requirements-test.txt").write_text("goodpkg\nbadpkg\n",
                                              encoding="utf-8")
    uv = _uv_fake(tmp_path, [])

    def fail(stderr):
        return SimpleNamespace(returncode=1, stdout="", stderr=stderr,
                               missing=None)

    def run_cmd(cmd, cwd=".", env_name=None, timeout=900, tool=None):
        if cmd[0] == "git":
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if cmd[:3] == ["uv", "pip", "install"]:
            if ".[test]" in cmd:
                return fail("memray build failed")
            if "-r" in cmd:
                return fail("badpkg has no wheels")
            if "memray" in cmd:
                return fail("memray does not support this platform")
            if "badpkg" in cmd:
                return fail("badpkg not found")
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        hit = uv(cmd, env_name)
        assert hit is not None, f"unexpected cmd: {cmd}"
        return hit

    ctx, saved = _generic_ctx({"preset": None, "mode": "live"}, run_cmd)
    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    info = ProjectInfo(package="demopkg", python_version="3.11",
                       test_command=["pytest", "-q"], has_suite=True,
                       test_extras=["test"],
                       test_requirements=["requirements-test.txt"])
    record = generic_mod.build_envs(
        ctx, key="w", ws_rel="workspaces/w", info=info,
        decision=Decision("numpy", "2.0.2"), commit_date="2023-05-01",
        sha=SHA)
    assert record["before"]["freeze"]["numpy"] == "1.26.4"
    assert record["skipped"]["before"] == ["memray", "badpkg"]
    assert record["after"]["freeze"]["numpy"] == "2.0.2"


def test_build_envs_after_falls_back_before_conflict(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.generic import Decision, ProjectInfo
    from types import SimpleNamespace

    ws = tmp_path / "workspaces" / "w"
    ws.mkdir(parents=True)
    (ws / "pyproject.toml").write_text(
        '[project.optional-dependencies]\ntest = ["okdep", "memray"]\n',
        encoding="utf-8")
    uv = _uv_fake(tmp_path, [])

    units = []

    def run_cmd(cmd, cwd=".", env_name=None, timeout=900, tool=None):
        if cmd[0] == "git":
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if cmd[:3] == ["uv", "pip", "install"]:
            units.append(list(cmd))
            if "memray" in cmd and ".[test]" not in cmd:
                return SimpleNamespace(
                    returncode=1, stdout="",
                    stderr="memray does not support this platform",
                    missing=None)
            if "pytest==7.4.0" in cmd:
                return SimpleNamespace(
                    returncode=1, stdout="",
                    stderr="pytest==7.4.0 incompatible with numpy 2",
                    missing=None)
            if "--constraint" in cmd and ".[test]" in cmd:
                return SimpleNamespace(
                    returncode=1, stdout="",
                    stderr="memray build failed outside the pins",
                    missing=None)
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        hit = uv(cmd, env_name)
        assert hit is not None, f"unexpected cmd: {cmd}"
        return hit

    ctx, saved = _generic_ctx({"preset": None, "mode": "live"}, run_cmd)
    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    info = ProjectInfo(package="otherpkg", python_version="3.11",
                       test_command=["pytest", "-q"], has_suite=True,
                       test_extras=["test"])
    record = generic_mod.build_envs(
        ctx, key="w", ws_rel="workspaces/w", info=info,
        decision=Decision("numpy", "2.0.2"), commit_date="2023-05-01",
        sha=SHA)
    assert record["conflict"] is None
    assert record["after"]["freeze"]["numpy"] == "2.0.2"
    assert record["skipped"]["after"] == ["memray"]
    assert record["skipped"]["before"] == []  # before full install held
    # After units try the before pin first, then float once on failure;
    # every after unit carries the decision anchor, and floats run under
    # per-unit constraints (every pin except the floated unit's own: a
    # bare float re-resolves the whole env to latest).
    pinned = [c for c in units if "pytest==7.4.0" in c]
    assert len(pinned) == 1 and "--constraint" in pinned[0]
    assert pinned[0][-1] == "numpy==2.0.2"
    floats = [c for c in units
              if c[-2:] == ["pytest", "numpy==2.0.2"]]
    assert len(floats) == 1
    idx = floats[0].index("--constraint")
    assert floats[0][idx + 1] == ".chainreaction-float-pytest.txt"
    assert (ws / ".chainreaction-float-pytest.txt").read_text(
        encoding="utf-8") == "demopkg==0.1\n"


def test_scrubber_rewrites_workspace_and_role_envs():
    from chainreaction.runs.scrub import Scrubber

    scrub = Scrubber.default()
    win_ws = ("E:\\foreign\\co\\workspaces\\nipy__nibabel@67ea99725d7e\\"
              "nibabel\\casting.py")
    assert scrub.text(f'File "{win_ws}", line 717') == \
        'File "<ws>/nibabel/casting.py", line 717'
    nix_before = ("/home/judge/co/envs/nipy__nibabel@67ea99725d7e/before/lib/"
                  "python3.11/site-packages/numpy/__init__.py")
    assert scrub.text(nix_before) == \
        "<env:before>/site-packages/numpy/__init__.py"
    win_after = ("E:\\co\\envs\\a__b@c00000000000\\after\\Lib\\site-packages\\"
                 "pkg\\mod.py")
    assert scrub.text(win_after) == "<env:after>/site-packages/pkg/mod.py"
    # Preset scrubbing is untouched.
    assert scrub.text("E:\\co\\envs\\numpy2\\Lib\\site-packages\\n.py") == \
        "<env:numpy2>/site-packages/n.py"


def test_scrubber_rewrites_env_and_ws_paths_at_token_end():
    from chainreaction.runs.scrub import ROOT, Scrubber

    # A ctx scrubber built before the envs existed has no exact env
    # pairs; the regex catchers must still canonicalize role paths
    # that end the token (uv's "environment at: <path>" line ends
    # with a newline). Otherwise live stages see <root>/envs/...
    # while the record carries <env:role> and replay diverges.
    stale = Scrubber((((str(ROOT), "<root>"),)))
    line = ("Using Python 3.9.25 environment at: " + str(ROOT) +
            "\\envs\\a__b@c00000000000\\after\nerror: x")
    assert stale.text(line) == \
        "Using Python 3.9.25 environment at: <env:after>\nerror: x"
    ws_line = "cloning into '" + str(ROOT) + "\\workspaces\\k'\n"
    assert stale.text(ws_line) == "cloning into '<ws>'\n"
    assert stale.text("E:\\foreign\\co\\envs\\numpy2\n") == \
        "<env:numpy2>\n"


def _pkgdir(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    return tmp_path


def test_pytest_addopts_pyproject_list_and_str(tmp_path):
    _pkgdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'addopts = ["--doctest-modules", "--pyargs"]\n', encoding="utf-8")
    assert detect_project(tmp_path, "pkg").pytest_addopts == \
        "--doctest-modules --pyargs"
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = '--cov=pkg -q'\n",
        encoding="utf-8")
    assert detect_project(tmp_path, "pkg").pytest_addopts == "--cov=pkg -q"


def test_pytest_addopts_precedence(tmp_path):
    from chainreaction.generic import _pytest_addopts

    _pkgdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = '--pyproject'\n",
        encoding="utf-8")
    (tmp_path / "tox.ini").write_text("[pytest]\naddopts = --tox\n",
                                      encoding="utf-8")
    (tmp_path / "setup.cfg").write_text("[tool:pytest]\naddopts = --cfg\n",
                                        encoding="utf-8")
    assert _pytest_addopts(tmp_path) == "--pyproject"
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --ini\n",
                                         encoding="utf-8")
    assert _pytest_addopts(tmp_path) == "--ini"
    (tmp_path / "pytest.ini").unlink()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n",
                                             encoding="utf-8")
    assert _pytest_addopts(tmp_path) == "--tox"
    (tmp_path / "tox.ini").unlink()
    assert _pytest_addopts(tmp_path) == "--cfg"
    (tmp_path / "setup.cfg").unlink()
    assert _pytest_addopts(tmp_path) == ""


def test_needs_coverage_packages_and_after_tooling():
    from chainreaction.generic import (
        ProjectInfo, TEST_TOOLING, after_tooling,
        needs_coverage_packages,
    )

    plain = ProjectInfo(package="p", pytest_addopts="--doctest-modules")
    assert needs_coverage_packages(plain) is False
    assert after_tooling(plain) == ["pytest"]
    cov = ProjectInfo(package="p", pytest_addopts="--cov=p --cov-report=")
    assert needs_coverage_packages(cov) is True
    assert after_tooling(cov) == TEST_TOOLING
    for addopts, want in (("--no-cov -q", True), ("-p cov", True),
                          ("-q --cov-fail-under=90", True),
                          ("-p no:cov", False), ("-p no:cacheprovider", False),
                          ("tests/coverage/", False)):
        info = ProjectInfo(package="p", pytest_addopts=addopts)
        assert needs_coverage_packages(info) is want, addopts
    req = ProjectInfo(package="p",
                      pytest_required_plugins="pytest-cov pytest-xdist")
    assert needs_coverage_packages(req) is True


def test_pytest_required_plugins_detected(tmp_path):
    _pkgdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'required_plugins = ["pytest-cov"]\n', encoding="utf-8")
    info = detect_project(tmp_path, "pkg")
    assert info.pytest_required_plugins == "pytest-cov"
    assert info.pytest_addopts == ""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'required_plugins = "pytest-xdist"\n', encoding="utf-8")
    assert detect_project(tmp_path, "pkg").pytest_required_plugins == \
        "pytest-xdist"


def test_install_args_tooling_param():
    from chainreaction.generic import (
        ProjectInfo, TEST_TOOLING, _install_args,
    )

    info = ProjectInfo(package="p")
    assert _install_args(info)[-len(TEST_TOOLING):] == TEST_TOOLING
    assert _install_args(info, ["pytest"])[-1:] == ["pytest"]
    assert _install_args(info, ["pytest"])[0] == "."


def test_drop_after_coverage():
    from types import SimpleNamespace

    from chainreaction.generic import _drop_after_coverage

    def ctx_with(freeze_text, uninstall_rc=0):
        cmds, messages = [], []

        def run_cmd(cmd, cwd=".", env_name=None, timeout=900, tool=None):
            cmds.append(cmd)
            if len(cmd) > 2 and cmd[1] == "-c":
                return SimpleNamespace(returncode=0, stdout=freeze_text,
                                       stderr="", missing=None)
            return SimpleNamespace(returncode=uninstall_rc, stdout="",
                                   stderr="boom", missing=None)

        ctx = SimpleNamespace(
            run_cmd=run_cmd, env_python=lambda n: "<envpy>",
            emit=lambda t, d: messages.append(d["message"]))
        return ctx, cmds, messages

    cov_freeze = "coverage==7.5.1\npytest-cov==5.0.0\npytest==8.2.0\n"
    ctx, cmds, messages = ctx_with(cov_freeze)
    _drop_after_coverage(ctx, "ws", "k/after", "numpy")
    uninstalls = [c for c in cmds if c[:3] == ["uv", "pip", "uninstall"]]
    assert len(uninstalls) == 1
    assert uninstalls[0][-2:] == ["coverage", "pytest-cov"]
    assert any("drops coverage + pytest-cov" in m for m in messages)

    ctx, cmds, _ = ctx_with("pytest==8.2.0\n")
    _drop_after_coverage(ctx, "ws", "k/after", "numpy")
    assert not [c for c in cmds if c[:3] == ["uv", "pip", "uninstall"]]

    # The decided package itself is never dropped.
    ctx, cmds, _ = ctx_with(cov_freeze)
    _drop_after_coverage(ctx, "ws", "k/after", "coverage")
    uninstalls = [c for c in cmds if c[:3] == ["uv", "pip", "uninstall"]]
    assert uninstalls[0][-1:] == ["pytest-cov"]

    # A failed uninstall warns instead of failing the build.
    ctx, _, messages = ctx_with(cov_freeze, uninstall_rc=1)
    _drop_after_coverage(ctx, "ws", "k/after", "numpy")
    assert any("keeps coverage + pytest-cov" in m for m in messages)


def _envs_run_cmd(tmp_path, calls, after_freeze):
    from types import SimpleNamespace

    uv = _uv_fake(tmp_path, calls, after_freeze=after_freeze)

    def run_cmd(cmd, cwd=".", env_name=None, timeout=900, tool=None):
        calls.append(cmd)
        if cmd[0] == "git":
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        hit = uv(cmd, env_name)
        assert hit is not None, f"unexpected cmd: {cmd}"
        return hit

    return run_cmd


def test_build_envs_after_drops_coverage_unless_needed(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.generic import Decision, ProjectInfo

    after_freeze = AFTER_FREEZE + "coverage==7.5.1\npytest-cov==5.0.0\n"
    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    for addopts, want_drop in (("", True), ("--cov=pkg", False)):
        calls = []
        run_cmd = _envs_run_cmd(tmp_path, calls, after_freeze)
        ctx, _ = _generic_ctx({"preset": None, "mode": "live"}, run_cmd)
        info = ProjectInfo(package="demopkg", python_version="3.11",
                           pytest_addopts=addopts)
        generic_mod.build_envs(
            ctx, key=f"w{want_drop}", ws_rel="workspaces/w", info=info,
            decision=Decision("numpy", "2.0.2"),
            commit_date="2023-05-01", sha=SHA)
        uninstalls = [c for c in calls
                      if c[:3] == ["uv", "pip", "uninstall"]]
        assert (len(uninstalls) == 1) is want_drop, addopts


def test_build_envs_after_spec_tracks_cov_drop(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.generic import Decision, ProjectInfo

    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    markers = {}
    for key, addopts in (("w1", ""), ("w2", "--cov=pkg")):
        calls = []
        run_cmd = _envs_run_cmd(tmp_path, calls, AFTER_FREEZE)
        ctx, _ = _generic_ctx({"preset": None, "mode": "live"}, run_cmd)
        info = ProjectInfo(package="demopkg", python_version="3.11",
                           pytest_addopts=addopts)
        generic_mod.build_envs(
            ctx, key=key, ws_rel="workspaces/w", info=info,
            decision=Decision("numpy", "2.0.2"),
            commit_date="2023-05-01", sha=SHA)
        for role in ("before", "after"):
            marker = json.loads(
                (tmp_path / "envs" / key / role / ".chainreaction.json"
                 ).read_text(encoding="utf-8"))
            markers[(key, role)] = marker["spec_hash"]
    assert markers[("w1", "before")] == markers[("w2", "before")]
    assert markers[("w1", "after")] != markers[("w2", "after")]


def test_build_envs_float_relaxes_conflicting_pins(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.generic import Decision, ProjectInfo
    from types import SimpleNamespace

    ws = tmp_path / "workspaces" / "w"
    ws.mkdir(parents=True)
    (ws / "pyproject.toml").write_text(
        '[project.optional-dependencies]\ntest = ["okdep"]\n',
        encoding="utf-8")
    uv = _uv_fake(tmp_path, [])
    units = []
    state = {"floated": False}

    def fail(stderr):
        return SimpleNamespace(returncode=1, stdout="", stderr=stderr,
                               missing=None)

    def run_cmd(cmd, cwd=".", env_name=None, timeout=900, tool=None):
        if cmd[0] == "git":
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if cmd[:3] == ["uv", "pip", "install"]:
            units.append(list(cmd))
            if ".[test]" in cmd and "--constraint" in cmd:
                return fail("memray build failed outside the pins")
            if "pytest==7.4.0" in cmd:
                return fail("pytest==7.4.0 incompatible with numpy 2")
            if (".chainreaction-float-pytest.txt" in cmd
                    and not state["floated"]):
                state["floated"] = True
                return fail("demopkg==0.1 requires numpy<1.27: requirements "
                            "are unsatisfiable demopkg")
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        hit = uv(cmd, env_name)
        assert hit is not None, f"unexpected cmd: {cmd}"
        return hit

    ctx, _ = _generic_ctx({"preset": None, "mode": "live"}, run_cmd)
    messages = []
    ctx.emit = lambda t, d: messages.append(d["message"])
    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    info = ProjectInfo(package="otherpkg", python_version="3.11",
                       test_command=["pytest", "-q"], has_suite=True,
                       test_extras=["test"])
    record = generic_mod.build_envs(
        ctx, key="w", ws_rel="workspaces/w", info=info,
        decision=Decision("numpy", "2.0.2"), commit_date="2023-05-01",
        sha=SHA)
    assert record["conflict"] is None
    assert record["skipped"]["after"] == []
    floats = [c for c in units if c[-2:] == ["pytest", "numpy==2.0.2"]]
    assert len(floats) == 2
    for cmd in floats:
        assert cmd[cmd.index("--constraint") + 1] == \
            ".chainreaction-float-pytest.txt"
    assert (ws / ".chainreaction-float-pytest.txt").read_text(
        encoding="utf-8") == "demopkg>=0.1\n"
    # The blamed pin floored in place (never removed) and persisted.
    assert (ws / ".chainreaction-after-constraints.txt").read_text(
        encoding="utf-8") == "demopkg>=0.1\npytest==7.4.0\n"
    assert any("relaxed 1 pin(s): demopkg" in m for m in messages)


def test_rmtree_clears_readonly_files(tmp_path):
    import os as _os

    from chainreaction.generic import _rmtree

    target = tmp_path / "env"
    target.mkdir()
    locked = target / "f.dll"
    locked.write_text("x", encoding="utf-8")
    _os.chmod(locked, 0o444)
    _rmtree(target)
    assert not target.exists()


def test_pin_floor_helpers():
    from chainreaction.generic import (
        _floor_pin,
        _is_resolver_conflict,
        _pin_is_exact,
        _pin_name,
    )

    assert _pin_name("NumPy==1.26.4") == "numpy"
    assert _pin_name("a>=1") == "a"
    assert _pin_is_exact("a==1") is True
    assert _pin_is_exact("a>=1") is False
    pins = ["b==2", "a==1"]
    assert _floor_pin(pins, "a") is True
    assert pins == ["b==2", "a>=1"]
    assert _floor_pin(pins, "a") is False  # floors never relax
    assert _floor_pin(pins, "missing") is False
    assert _is_resolver_conflict("requirements are unsatisfiable")
    assert _is_resolver_conflict("ResolutionImpossible: x")
    assert not _is_resolver_conflict("Failed to build foo")


def test_build_envs_float_skips_build_failure_without_relaxing(
        tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.generic import Decision, ProjectInfo
    from types import SimpleNamespace

    ws = tmp_path / "workspaces" / "w"
    ws.mkdir(parents=True)
    (ws / "pyproject.toml").write_text(
        '[project.optional-dependencies]\ntest = ["okdep"]\n',
        encoding="utf-8")
    uv = _uv_fake(tmp_path, [])

    def run_cmd(cmd, cwd=".", env_name=None, timeout=900, tool=None):
        if cmd[0] == "git":
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if cmd[:3] == ["uv", "pip", "install"]:
            if ".[test]" in cmd and "--constraint" in cmd:
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="memray build failed",
                    missing=None)
            if "pytest==7.4.0" in cmd:
                return SimpleNamespace(
                    returncode=1, stdout="",
                    stderr="pytest==7.4.0 incompatible", missing=None)
            # A build failure names a pin but carries no conflict
            # signal: skip without relaxing anything.
            if ".chainreaction-float-pytest.txt" in cmd:
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="error: Failed to "
                    "build `demopkg==0.1` (setuptools failed)",
                    missing=None)
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        hit = uv(cmd, env_name)
        assert hit is not None, f"unexpected cmd: {cmd}"
        return hit

    ctx, _ = _generic_ctx({"preset": None, "mode": "live"}, run_cmd)
    messages = []
    ctx.emit = lambda t, d: messages.append(d["message"])
    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    info = ProjectInfo(package="otherpkg", python_version="3.11",
                       test_command=["pytest", "-q"], has_suite=True,
                       test_extras=["test"])
    record = generic_mod.build_envs(
        ctx, key="w", ws_rel="workspaces/w", info=info,
        decision=Decision("numpy", "2.0.2"), commit_date="2023-05-01",
        sha=SHA)
    assert record["skipped"]["after"] == ["pytest"]
    assert (ws / ".chainreaction-after-constraints.txt").read_text(
        encoding="utf-8") == "demopkg==0.1\npytest==7.4.0\n"
    assert not any("relaxed" in m for m in messages)


def test_build_envs_primes_bytecode_after_install(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.generic import Decision, ProjectInfo
    from types import SimpleNamespace

    ws = tmp_path / "workspaces" / "w"
    ws.mkdir(parents=True)
    (ws / "pyproject.toml").write_text(
        '[project]\nname = "demopkg"\n', encoding="utf-8")
    calls = []
    uv = _uv_fake(tmp_path, calls)

    def run_cmd(cmd, cwd=".", env_name=None, timeout=900, tool=None):
        calls.append((tuple(cmd), env_name))
        if cmd[0] == "git":
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        hit = uv(cmd, env_name)
        assert hit is not None, f"unexpected cmd: {cmd}"
        return hit

    ctx, _ = _generic_ctx({"preset": None, "mode": "live"}, run_cmd)
    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    info = ProjectInfo(package="demopkg", python_version="3.11",
                       test_command=["pytest", "-q"], has_suite=True)
    generic_mod.build_envs(
        ctx, key="w", ws_rel="workspaces/w", info=info,
        decision=Decision("numpy", "2.0.2"), commit_date="2023-05-01",
        sha=SHA)
    primes = [(cmd, env) for cmd, env in calls
              if len(cmd) > 2 and cmd[1] == "-c"
              and "compileall" in cmd[2]]
    assert sorted(env.split("/")[-1] for _, env in primes) == \
        ["after", "before"]
    for cmd, env in primes:
        marker = f"<envpy:{env}>"
        installs = [i for i, (c, _) in enumerate(calls)
                    if c[:3] == ("uv", "pip", "install")
                    and marker in c]
        prime_at = calls.index((cmd, env))
        assert installs and prime_at > installs[-1]


def test_build_envs_float_stops_at_terminal_floors(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.generic import Decision, ProjectInfo
    from types import SimpleNamespace

    ws = tmp_path / "workspaces" / "w"
    ws.mkdir(parents=True)
    (ws / "pyproject.toml").write_text(
        '[project.optional-dependencies]\ntest = ["okdep"]\n',
        encoding="utf-8")
    uv = _uv_fake(tmp_path, [])
    units = []

    def run_cmd(cmd, cwd=".", env_name=None, timeout=900, tool=None):
        if cmd[0] == "git":
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        if cmd[:3] == ["uv", "pip", "install"]:
            units.append(list(cmd))
            if ".[test]" in cmd and "--constraint" in cmd:
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="memray build failed",
                    missing=None)
            if "pytest==7.4.0" in cmd:
                return SimpleNamespace(
                    returncode=1, stdout="",
                    stderr="pytest==7.4.0 incompatible", missing=None)
            # Every float attempt blames only demopkg: the first
            # floors it, the second finds no exact pin left to relax.
            if ".chainreaction-float-pytest.txt" in cmd:
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="demopkg>=0.1 still "
                    "unsatisfiable demopkg", missing=None)
            return SimpleNamespace(returncode=0, stdout="", stderr="",
                                   missing=None)
        hit = uv(cmd, env_name)
        assert hit is not None, f"unexpected cmd: {cmd}"
        return hit

    ctx, _ = _generic_ctx({"preset": None, "mode": "live"}, run_cmd)
    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    info = ProjectInfo(package="otherpkg", python_version="3.11",
                       test_command=["pytest", "-q"], has_suite=True,
                       test_extras=["test"])
    record = generic_mod.build_envs(
        ctx, key="w", ws_rel="workspaces/w", info=info,
        decision=Decision("numpy", "2.0.2"), commit_date="2023-05-01",
        sha=SHA)
    assert record["skipped"]["after"] == ["pytest"]
    assert len([c for c in units
                if c[-2:] == ["pytest", "numpy==2.0.2"]]) == 2
    assert (ws / ".chainreaction-after-constraints.txt").read_text(
        encoding="utf-8") == "demopkg>=0.1\npytest==7.4.0\n"


def test_build_envs_replay_issues_no_install_cmds(tmp_path, monkeypatch):
    import chainreaction.generic as generic_mod
    from chainreaction.generic import Decision, ProjectInfo

    monkeypatch.setattr(generic_mod, "ROOT", tmp_path)
    calls = []
    run_cmd = _envs_run_cmd(tmp_path, calls, AFTER_FREEZE)
    ctx, _ = _generic_ctx(
        {"preset": None, "mode": "replay",
         "env_provenance": {"before": "reused", "after": "reused"}},
        run_cmd)
    info = ProjectInfo(package="demopkg", python_version="3.11")
    record = generic_mod.build_envs(
        ctx, key="w", ws_rel="workspaces/w", info=info,
        decision=Decision("numpy", "2.0.2"), commit_date="2023-05-01",
        sha=SHA)
    assert record["after"]["freeze"]["numpy"] == "2.0.2"
    assert [c for c in calls if c[0] == "uv"] == [["uv", "--version"]]
    assert ctx.manifest["env_provenance"] == {"before": "reused",
                                              "after": "reused"}
