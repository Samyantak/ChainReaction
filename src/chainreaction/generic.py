"""M2 generic path: any public Python repo through the same pipeline.

Pure helpers (no I/O) plus thin context adapters. All subprocess and
network I/O goes through RunContext gateways so generic runs record
and replay like presets.
"""

import ast
import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
from dataclasses import asdict, dataclass, field
from pathlib import Path

from chainreaction.runs.scrub import ROOT

# -- typed failures (M2 §8) --


class GenericError(Exception):
    """Base for typed generic-path failures (run status failed)."""


class CloneFailed(GenericError):
    pass


class NoPythonPackage(GenericError):
    pass


class InstallFailed(GenericError):
    pass


class VersionNotFound(GenericError):
    pass


class GuideNotFound(GenericError):
    pass


class WorkspaceTooLarge(GenericError):
    pass


WORKSPACE_SIZE_CAP_MB = 300

# -- repo URL / workspace key (M2 §1) --

_REPO_RE = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    """(owner, repo) from an https GitHub-style URL; raises CloneFailed."""
    m = _REPO_RE.match((repo_url or "").strip())
    if not m:
        raise CloneFailed(
            f"bad repo_url {repo_url!r}: want "
            "https://github.com/<owner>/<repo>")
    return m.group("owner"), m.group("repo")


def workspace_key(owner: str, repo: str, sha12: str) -> str:
    return f"{owner}__{repo}@{sha12}"


def workspace_rel(owner: str, repo: str, sha12: str) -> str:
    return f"workspaces/{workspace_key(owner, repo, sha12)}"


# -- project detection (M2 §2, deterministic, never executes repo code) --

_SKIP_TOP_DIRS = {"tests", "test", "docs", "examples", "benchmarks"}

_TEST_EXTRAS_ORDER = ("test", "tests", "testing", "dev")


@dataclass
class ProjectInfo:
    package: str
    package_candidates: list[str] = field(default_factory=list)
    python_version: str = "3.11"
    requires_python: str = ""
    test_command: list[str] = field(default_factory=list)
    has_suite: bool = True
    test_extras: list[str] = field(default_factory=list)
    test_requirements: list[str] = field(default_factory=list)
    pytest_addopts: str = ""
    pytest_required_plugins: str = ""


def _read_name_from_pyproject(root: Path) -> tuple[str, str]:
    """(name, requires-python) from pyproject.toml [project]; '' when absent."""
    path = root / "pyproject.toml"
    if not path.is_file():
        return "", ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "", ""
    try:
        import tomllib
    except ImportError:  # pragma: no cover - 3.11+ always has tomllib
        return "", ""
    try:
        data = tomllib.loads(text)
    except Exception:
        return "", ""
    proj = data.get("project", {})
    name = str(proj.get("name", "") or "")
    req = str(proj.get("requires-python", "") or "")
    return name, req


def _read_name_from_setup_cfg(root: Path) -> str:
    path = root / "setup.cfg"
    if not path.is_file():
        return ""
    cfg = configparser.ConfigParser()
    try:
        cfg.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return ""
    try:
        return cfg.get("metadata", "name", fallback="")
    except configparser.Error:
        return ""


def _read_name_from_setup_py(root: Path) -> str:
    """Package name from setup.py parsed statically (never executed)."""
    path = root / "setup.py"
    if not path.is_file():
        return ""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return ""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name != "setup":
            continue
        for kw in node.keywords:
            if kw.arg == "name":
                val = ast.literal_eval(kw.value) if isinstance(
                    kw.value, ast.Constant) else ""
                return str(val or "")
    return ""


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _package_from_name(root: Path, name: str) -> str:
    """Top-level import package for a distribution name, '' when missing."""
    if not name:
        return ""
    norm = _normalize(name)
    cands: list[str] = []
    for cand in {name, norm, norm.replace("-", "_"), norm.replace("-", "")}:
        for base in (root / cand, root / "src" / cand):
            if (base.is_dir() and (base / "__init__.py").is_file()
                    and cand not in cands):
                cands.append(cand)
    return cands[0] if cands else ""


def _top_level_candidates(root: Path) -> list[tuple[str, int]]:
    """(package, .py count) for top-level dirs with __init__.py."""
    out: list[tuple[str, int]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in _SKIP_TOP_DIRS:
            continue
        if child.name.startswith("."):
            continue
        if not (child / "__init__.py").is_file():
            continue
        count = sum(1 for _ in child.rglob("*.py"))
        out.append((child.name, count))
    src = root / "src"
    if src.is_dir():
        for child in sorted(src.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if not (child / "__init__.py").is_file():
                continue
            count = sum(1 for _ in child.rglob("*.py"))
            out.append((child.name, count))
    return out


def detect_package(root: str | Path, override: str = "") -> tuple[str, list[str]]:
    """(package, other_candidates); raises NoPythonPackage when none."""
    root = Path(root)
    if override:
        pkgdir = root / override
        srcdir = root / "src" / override
        if ((pkgdir.is_dir() and (pkgdir / "__init__.py").is_file())
                or (srcdir.is_dir()
                    and (srcdir / "__init__.py").is_file())):
            return override, []
        raise NoPythonPackage(
            f"package override {override!r} not found in repo")
    name, _ = _read_name_from_pyproject(root)
    pkg = _package_from_name(root, name)
    if not pkg:
        pkg = _package_from_name(root, _read_name_from_setup_cfg(root))
    if not pkg:
        pkg = _package_from_name(root, _read_name_from_setup_py(root))
    cands = _top_level_candidates(root)
    if pkg:
        others = sorted({c for c, _ in cands if c != pkg})
        return pkg, others
    if not cands:
        raise NoPythonPackage(
            "no Python package found (no pyproject/setup.cfg name with "
            "src/<name>/ or <name>/__init__.py, no top-level package dir)")
    cands.sort(key=lambda t: (-t[1], t[0]))
    return cands[0][0], sorted(c for c, _ in cands[1:])


def _satisfies(requires_python: str, version: str) -> bool:
    """Minimal requires-python check (>=, >, ==, ~=, comma ranges)."""
    req = (requires_python or "").strip()
    if not req:
        return True
    parts = [p.strip() for p in req.split(",") if p.strip()]
    want = tuple(int(x) for x in version.split("."))
    for part in parts:
        m = re.match(r"^(>=|<=|==|~=|>|<|!=)\s*([\d.]+)", part)
        if not m:
            continue
        op, ver = m.group(1), tuple(int(x) for x in m.group(2).split("."))
        if op == ">=" and not (want >= ver):
            return False
        if op == ">" and not (want > ver):
            return False
        if op == "<=" and not (want <= ver):
            return False
        if op == "<" and not (want < ver):
            return False
        if op == "==" and not (want[:len(ver)] == ver):
            return False
        if op == "!=" and want[:len(ver)] == ver:
            return False
        if op == "~=" and not (want >= ver and want[0] == ver[0]):
            return False
    return True


def pick_python_version(requires_python: str,
                        available: tuple[str, ...] = ("3.12", "3.11", "3.10")
                        ) -> str:
    """Newest available 3.10–3.12 satisfying requires-python, else 3.11."""
    for ver in available:
        if _satisfies(requires_python, ver):
            return ver
    return "3.11"


def detect_test_command(root: str | Path) -> tuple[list[str], bool]:
    """(pytest command, has_suite). No suite → ([], False)."""
    root = Path(root)
    has_pytest_cfg = False
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8")
            has_pytest_cfg = "[tool.pytest" in text
        except OSError:
            has_pytest_cfg = False
    has_tests_dir = (root / "tests").is_dir() or (root / "test").is_dir()
    has_test_files = any(root.glob("test_*.py")) or any(
        root.glob("*_test.py"))
    if not has_tests_dir and not has_test_files and not has_pytest_cfg:
        # Nested test files (tests/ under the package, test dirs elsewhere).
        has_test_files = any(root.rglob("test_*.py")) or any(
            root.rglob("*_test.py"))
    if has_tests_dir or has_test_files or has_pytest_cfg:
        return ["pytest", "-q"], True
    return [], False


def detect_test_extras(root: str | Path) -> tuple[list[str], list[str]]:
    """(extras subset, requirements files) for installing test deps."""
    root = Path(root)
    extras: list[str] = []
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        tomllib = None  # type: ignore
    if tomllib is not None:
        path = root / "pyproject.toml"
        if path.is_file():
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            opt = data.get("project", {}).get("optional-dependencies", {})
            for cand in _TEST_EXTRAS_ORDER:
                if cand in opt:
                    extras.append(cand)
                    break
    req_files: list[str] = []
    for pat in ("requirements-test*.txt", "requirements-dev*.txt",
                "requirements_test*.txt", "requirements_dev*.txt"):
        for hit in sorted(root.glob(pat)):
            req_files.append(hit.name)
    return extras, req_files


def _opt_text(value) -> str:
    if isinstance(value, list):
        return " ".join(str(a) for a in value)
    return value if isinstance(value, str) else ""


def _pytest_options(root: Path) -> tuple[str, str]:
    """(addopts, required_plugins) from the winning pytest config.

    Read statically; precedence mirrors pytest's: pytest.ini, then
    pyproject.toml with [tool.pytest.ini_options], then tox.ini
    [pytest], then setup.cfg [tool:pytest].
    """
    ini_file = root / "pytest.ini"
    if ini_file.is_file():
        return _ini_opts(ini_file, "pytest")
    path = root / "pyproject.toml"
    if path.is_file():
        try:
            import tomllib

            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        tool = data.get("tool") or {}
        ini = (tool.get("pytest") or {}).get("ini_options")
        if isinstance(ini, dict):
            return (_opt_text(ini.get("addopts", "")),
                    _opt_text(ini.get("required_plugins", "")))
    tox = root / "tox.ini"
    if tox.is_file() and _ini_has_section(tox, "pytest"):
        return _ini_opts(tox, "pytest")
    cfg = root / "setup.cfg"
    if cfg.is_file() and _ini_has_section(cfg, "tool:pytest"):
        return _ini_opts(cfg, "tool:pytest")
    return "", ""


def _pytest_addopts(root: Path) -> str:
    """The repo's configured pytest addopts, read statically."""
    return _pytest_options(root)[0]


def _pytest_required_plugins(root: Path) -> str:
    """The repo's required_plugins line, read statically (may be "")."""
    return _pytest_options(root)[1]


def _ini_has_section(path: Path, section: str) -> bool:
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error:
        return False
    return parser.has_section(section)


def _ini_opts(path: Path, section: str) -> tuple[str, str]:
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error:
        return "", ""
    return (parser.get(section, "addopts", fallback=""),
            parser.get(section, "required_plugins", fallback=""))


# A cov option in addopts (--cov*, --no-cov) or an explicit `-p cov`
# needs the plugin installed; `-p no:cov` disables it and needs nothing.
# pytest-cov owns the --cov* namespace, so any dashed option containing
# "cov" counts.
_COV_OPT_RE = re.compile(
    r"(?:^|\s)(?:-{1,2}[\w-]*cov[\w-]*|-p\s+cov)(?=\s|$|=)")


def needs_coverage_packages(info: ProjectInfo) -> bool:
    """True when the repo's own config needs coverage plugins installed."""
    if _COV_OPT_RE.search(info.pytest_addopts or ""):
        return True
    return "cov" in (info.pytest_required_plugins or "")


def detect_project(root: str | Path, package_override: str = "") -> ProjectInfo:
    """Full deterministic project detection (never executes repo code)."""
    root = Path(root)
    package, others = detect_package(root, package_override)
    _, requires = _read_name_from_pyproject(root)
    cmd, has_suite = detect_test_command(root)
    extras, req_files = detect_test_extras(root)
    addopts, required = _pytest_options(root)
    return ProjectInfo(
        package=package, package_candidates=others,
        python_version=pick_python_version(requires),
        requires_python=requires, test_command=cmd, has_suite=has_suite,
        test_extras=extras, test_requirements=req_files,
        pytest_addopts=addopts, pytest_required_plugins=required)


# -- decision parse (M2 §3) --

@dataclass
class Decision:
    package: str
    to_version: str
    raw: str = ""


_DECISION_RES = (
    re.compile(r"^\s*(?:upgrade|bump|move)\s+([A-Za-z0-9_.\-]+)\s+to\s+"
               r"([A-Za-z0-9_.\-]+)\s*$", re.I),
    re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([A-Za-z0-9_.\-]+)\s*$"),
    re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s+([A-Za-z0-9_.\-]+)\s*$"),
)


def parse_decision(text: str) -> Decision | None:
    """Deterministic grammar; None when no pattern matches (LLM fallback)."""
    for rx in _DECISION_RES:
        m = rx.match(text or "")
        if m:
            return Decision(package=m.group(1), to_version=m.group(2),
                            raw=text)
    return None


DECISION_JSON_SCHEMA = {
    "name": "decision_parse",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "package": {"type": "string"},
            "to_version": {"type": "string"},
        },
        "required": ["package", "to_version"],
        "additionalProperties": False,
    },
}

DECISION_SYSTEM_PROMPT = """\
You are the ChainReaction decision parser. Given a dependency upgrade
decision in plain words, emit the PyPI package name and the requested
target version as JSON. Output JSON only, matching the schema.
"""


def parse_decision_with_llm(decision: str, chat_fn) -> Decision:
    """LLM fallback for free-form decisions (structured output)."""
    import json

    from chainreaction.llm import extract_json

    user = (f"Decision: {decision}\n\nEmit the change set as a single "
            f"JSON object matching this schema:\n"
            f"{json.dumps(DECISION_JSON_SCHEMA['schema'])}")
    data = extract_json(chat_fn(
        DECISION_SYSTEM_PROMPT, user, max_tokens=2000,
        response_format={"type": "json_schema",
                         "json_schema": DECISION_JSON_SCHEMA}))
    package = str(data.get("package", "") or "").strip()
    to_version = str(data.get("to_version", "") or "").strip()
    if not package or not to_version:
        raise VersionNotFound(
            f"could not parse a package and version from {decision!r}")
    return Decision(package=package, to_version=to_version, raw=decision)


def resolve_target_version(pypi_json: dict, requested: str) -> str:
    """Resolve '2.0' → latest 2.0.x release; raises VersionNotFound."""
    releases = pypi_json.get("releases", {}) or {}
    if not releases:
        raise VersionNotFound("PyPI lists no releases for this package")
    if requested in releases:
        return requested
    prefix = requested + "."
    cands = sorted(r for r in releases if r.startswith(prefix))
    if cands:
        return cands[-1]
    # Fall back to the info version when it matches the prefix.
    info_ver = str((pypi_json.get("info") or {}).get("version", ""))
    if info_ver == requested or info_ver.startswith(prefix):
        return info_ver
    raise VersionNotFound(
        f"version {requested!r} not on PyPI "
        f"({len(releases)} releases checked)")


def guide_candidates_from_pypi(pypi_json: dict) -> list[str]:
    """Changelog / release-notes / docs links from PyPI project_urls."""
    info = pypi_json.get("info") or {}
    urls = info.get("project_urls") or {}
    if not isinstance(urls, dict):
        return []
    wanted = ("changelog", "change log", "release notes", "releases",
              "documentation", "docs", "homepage")
    out: list[str] = []
    for key, url in urls.items():
        ku = str(key or "").lower()
        if any(w in ku for w in wanted) and url:
            out.append(str(url))
    # Stable order: changelog first, then the rest alphabetically.
    out.sort(key=lambda u: (0 if "changelog" in u.lower() or "changes" in u.lower()
                            else 1, u))
    return out


# -- baseline exclusion + test-level scoring (M2 §4, §7) --

def new_failures(before_failed: set[str], after_failed: set[str]) -> set[str]:
    """After failures minus baseline failures (never blame the decision)."""
    return set(after_failed) - set(before_failed)


def test_precision_recall(predicted: set[str],
                          actual_new: set[str]) -> tuple[float, float]:
    """Precision/recall of predicted failing tests vs actual new failures."""
    predicted, actual_new = set(predicted), set(actual_new)
    tp = len(predicted & actual_new)
    precision = tp / len(predicted) if predicted else 1.0
    recall = tp / len(actual_new) if actual_new else 1.0
    return precision, recall


def install_spec_hash(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


# -- workspace resolve (M2 §1; git via ctx.run_cmd, recorded) --

def git_head(path: str | Path) -> str | None:
    """Unrecorded HEAD check (environment precondition, like _git_commit)."""
    path = Path(path)
    if not (path / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and sha else None


def dir_size_mb(path: str | Path) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total / (1024 * 1024)


def _is_full_sha(ref: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}", ref or ""))


def ensure_workspace(ctx, repo_url: str, ref: str
                     ) -> tuple[str, str, str]:
    """Clone/resolve the cached workspace; return (ws_rel, sha, date).

    Live: partial clone into workspaces/<owner>__<repo>@<sha12>/ (reused
    when already at the commit), all git through ctx.run_cmd. Replay:
    verify the workspace is on disk at the recorded commit; never clones.
    """
    owner, repo = parse_repo_url(repo_url)
    ref = ref or ""
    if ctx.mode == "replay":
        expected = (ctx.manifest.get("resolved_commit") or "")
        if not expected:
            raise RuntimeError("recording has no resolved_commit")
        ws_rel = workspace_rel(owner, repo, expected[:12])
        if not (ROOT / ws_rel).is_dir():
            raise RuntimeError(
                f"workspace not on disk: {ws_rel}; replay needs the "
                f"source at {expected[:12]} (clone {repo_url} there and "
                f"check out {expected})")
        head = git_head(ROOT / ws_rel)
        if head != expected:
            from chainreaction.runs.context import CommitMismatch

            raise CommitMismatch(
                f"workspace {ws_rel} is at {head}, but the recording "
                f"needs {expected}")
        ctx.emit("log", {"message":
                         f"workspace verified: {ws_rel}"})
        return ws_rel, expected, ctx.manifest.get("commit_date") or ""
    # Live: reuse a cached checkout already at the commit.
    if _is_full_sha(ref):
        ws_rel = workspace_rel(owner, repo, ref[:12])
        if (ROOT / ws_rel).is_dir() and git_head(ROOT / ws_rel) == ref:
            ctx.emit("log", {"message":
                             f"setup: reusing cached {ws_rel}"})
            shown = ctx.run_cmd(
                ["git", "-C", ws_rel, "show", "-s", "--format=%cI", "HEAD"],
                cwd=".", env_name=None, timeout=60)
            date = (shown.stdout.strip()[:10] if shown.returncode == 0
                    else "")
            ctx.set_manifest(resolved_commit=ref, commit_date=date,
                             repo_url=repo_url, ref=ref, workspace=ws_rel)
            return ws_rel, ref, date
        staging_rel = ws_rel
    else:
        staging_rel = f"workspaces/.staging-{ctx.run_id}"
    if (ROOT / staging_rel).exists():
        shutil.rmtree(ROOT / staging_rel, ignore_errors=True)
    ctx.emit("log", {"message":
                     f"setup: cloning {repo_url} at {(ref or 'HEAD')[:12]}"})
    cloned = ctx.run_cmd(
        ["git", "clone", "--filter=blob:none", repo_url, staging_rel],
        cwd=".", env_name=None, timeout=600)
    if cloned.returncode != 0:
        raise CloneFailed(
            f"clone failed for {repo_url}: "
            f"{log_tail(cloned.stderr, 1)}")
    if ref:
        checked = ctx.run_cmd(["git", "-C", staging_rel, "checkout", ref],
                              cwd=".", env_name=None, timeout=300)
        if checked.returncode != 0:
            raise CloneFailed(
                f"checkout {ref[:12]} failed: "
                f"{log_tail(checked.stderr, 1)}")
    parsed = ctx.run_cmd(["git", "-C", staging_rel, "rev-parse", "HEAD"],
                         cwd=".", env_name=None, timeout=60)
    sha = parsed.stdout.strip()
    if parsed.returncode != 0 or not _is_full_sha(sha):
        raise CloneFailed(f"could not resolve a commit for {repo_url}")
    shown = ctx.run_cmd(
        ["git", "-C", staging_rel, "show", "-s", "--format=%cI", "HEAD"],
        cwd=".", env_name=None, timeout=60)
    date = shown.stdout.strip()[:10] if shown.returncode == 0 else ""
    size = dir_size_mb(ROOT / staging_rel)
    if size > WORKSPACE_SIZE_CAP_MB:
        shutil.rmtree(ROOT / staging_rel, ignore_errors=True)
        raise WorkspaceTooLarge(
            f"workspace is {size:.0f} MB (cap {WORKSPACE_SIZE_CAP_MB} MB)")
    ws_rel = workspace_rel(owner, repo, sha[:12])
    if staging_rel != ws_rel:
        if (ROOT / ws_rel).is_dir():
            if git_head(ROOT / ws_rel) == sha:
                shutil.rmtree(ROOT / staging_rel, ignore_errors=True)
            else:
                shutil.rmtree(ROOT / ws_rel, ignore_errors=True)
                (ROOT / staging_rel).rename(ROOT / ws_rel)
        else:
            (ROOT / staging_rel).rename(ROOT / ws_rel)
    ctx.set_manifest(resolved_commit=sha, commit_date=date,
                     repo_url=repo_url, ref=ref, workspace=ws_rel)
    return ws_rel, sha, date


def project_record(info: ProjectInfo, *, repo_url: str, ref: str,
                   resolved_commit: str, commit_date: str,
                   workspace: str) -> dict:
    """artifacts/project.json body (M2 §2)."""
    record = asdict(info)
    record.update(repo_url=repo_url, ref=ref,
                  resolved_commit=resolved_commit, commit_date=commit_date,
                  workspace=workspace)
    return record


# -- PyPI validation + decision flow (M2 §3) --

def fetch_pypi_info(ctx, package: str) -> dict:
    """PyPI JSON metadata via ctx.fetch (recorded); VersionNotFound typed."""
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        text = ctx.fetch(url, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise VersionNotFound(
                f"package {package!r} not found on PyPI") from None
        raise VersionNotFound(
            f"PyPI lookup failed for {package!r}: {exc}") from None
    except urllib.error.URLError as exc:
        raise VersionNotFound(
            f"PyPI lookup failed for {package!r}: {exc}") from None
    try:
        data = json.loads(str(text))
    except ValueError:
        raise VersionNotFound(
            f"PyPI returned unreadable data for {package!r}") from None
    if not isinstance(data, dict) or "releases" not in data:
        raise VersionNotFound(
            f"package {package!r} not found on PyPI") from None
    return data


def resolve_decision(ctx, decision_text: str) -> Decision:
    """Grammar, else LLM fallback, then PyPI-validate and pin the version."""
    parsed = parse_decision(decision_text)
    if parsed is None:
        parsed = parse_decision_with_llm(decision_text, ctx.llm)
    pypi = fetch_pypi_info(ctx, parsed.package)
    to_version = resolve_target_version(pypi, parsed.to_version)
    ctx.state["pypi_guide_urls"] = guide_candidates_from_pypi(pypi)
    return Decision(package=parsed.package, to_version=to_version,
                    raw=decision_text)


# -- before/after environments (M2 §3) --

TEST_TOOLING = ["pytest", "coverage>=7", "pytest-cov"]

# Bump when the after-build procedure changes env contents for identical
# inputs (relaxation, drops): stale after envs must rebuild, and the
# spec hash is the only mechanism that notices.
AFTER_BUILD_REV = "floor1"


def after_tooling(info: ProjectInfo) -> list:
    """After-env tooling: pytest always, coverage only when addopts need it.

    Only the before coverage run uses coverage/pytest-cov; in the after
    env they are dead weight whose entry points can crash collection
    (anndata: numba 0.63 vs coverage 7.4+ at plugin import). Repos
    whose own addopts pass --cov still get them, else pytest would
    reject the option.
    """
    if needs_coverage_packages(info):
        return list(TEST_TOOLING)
    return ["pytest"]

_TOOLCHAIN_NEEDLES = ("Microsoft Visual C++", "command 'gcc'",
                      "Python.h", "Failed building wheel",
                      "build backend", "cargo", "rustc",
                      "Microsoft C++ Build Tools")


def version_tuple(value: str) -> tuple:
    """Numeric prefix of a version for >= comparisons (2.0 == 2.0.0)."""
    parts = []
    for chunk in str(value).split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def freeze_dict(text: str) -> dict:
    """pip freeze output -> {lowercase name: version}."""
    out = {}
    for line in str(text).splitlines():
        line = line.strip()
        if "==" in line and not line.startswith(("#", "-e", "--")):
            name, _, ver = line.partition("==")
            name, ver = name.strip().lower(), ver.strip()
            if name and ver:
                out[name] = ver
    return out


def constraints_for(freeze: dict, decided_package: str,
                    repo_package: str = "") -> list:
    """Before-freeze pins minus the decided and repo packages, sorted.

    The repo package is excluded too: it installs from source ("."),
    whose rebuilt version never equals the before-freeze pin, so the
    pin can only contradict the direct install.
    """
    decided = decided_package.lower()
    repo = (repo_package or "").lower()
    return sorted(f"{name}=={ver}" for name, ver in freeze.items()
                  if name != decided and name != repo)


def _pin_name(pin: str) -> str:
    """Package name of a pin spec (==, >=, or any other operator)."""
    return re.split(r"[<>=!~]+", pin, maxsplit=1)[0].strip().lower()


def _pin_is_exact(pin: str) -> bool:
    """True for == pins (floors never relax further)."""
    return "==" in pin.split(";")[0]


def _floor_pin(pins: list, name: str) -> bool:
    """Relax pins' == pin for name to a >= floor, in place.

    Floors (never removals): a removed pin lets the resolver travel
    backward to paleolithic versions whose loose metadata fits but
    which cannot build (numba 0.47 for llvmlite==0.42 + numpy 2);
    a floor keeps the before version as the sane minimum.
    """
    for i, pin in enumerate(pins):
        if _pin_name(pin) == name.lower() and _pin_is_exact(pin):
            head, _, _ver = pin.partition("==")
            pins[i] = f"{head.strip()}>={_ver.split(';')[0].strip()}"
            tail = _ver.partition(";")[2]
            if tail.strip():
                pins[i] += f";{tail}"
            return True
    return False


def _is_resolver_conflict(stderr: str) -> bool:
    """True when stderr reports unsatisfiable requirements (not a build)."""
    text = str(stderr).lower()
    return "unsatisfiable" in text or "resolutionimpossible" in text


def _tooling_names() -> set[str]:
    """Lowercase package names of ChainReaction's own test tooling."""
    return {_pin_name(spec) for spec in TEST_TOOLING}


def collateral_diff(before: dict, after: dict,
                    decided_package: str, repo_package: str = "") -> dict:
    """{name: {before, after}} for non-decision packages that moved.

    Skips the decided package, the repo package under test (its rebuilt
    version string always drifts), and ChainReaction's own tooling
    (coverage/pytest-cov are dropped from the after env on purpose).
    """
    skip = {_pin_name(decided_package), (repo_package or "").lower()}
    skip |= _tooling_names()
    skip.discard("")
    out = {}
    for name in sorted(set(before) | set(after)):
        if name.lower() in skip:
            continue
        old, new = before.get(name), after.get(name)
        if old != new:
            out[name] = {"before": old, "after": new}
    return out


def log_tail(text: str, lines: int = 10) -> str:
    keep = [ln for ln in str(text).splitlines() if ln.strip()]
    return "\n".join(keep[-lines:])


def toolchain_hint(stderr: str) -> str:
    if any(n in str(stderr) for n in _TOOLCHAIN_NEEDLES):
        return ("; this looks like a build-backend failure needing a "
                "system toolchain (out of scope for M2)")
    return ""


def find_conflicting_pin(stderr: str, pin_names: list) -> str | None:
    """Pin most-named in resolver stderr; None when it names no pin."""
    text = str(stderr).lower()
    best, hits = None, 0
    for name in pin_names:
        count = len(re.findall(r"\b" + re.escape(name.lower()) + r"\b",
                               text))
        if count > hits:
            best, hits = name, count
    return best


def _marker_path(env_dir: Path) -> Path:
    return env_dir / ".chainreaction.json"


def _read_marker(env_dir: Path):
    try:
        return json.loads(_marker_path(env_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _env_python_name() -> Path:
    return (Path("Scripts/python.exe") if os.name == "nt"
            else Path("bin/python"))


def _env_cached(ctx, role: str, env_dir: Path, spec_hash: str) -> bool:
    if ctx.mode == "replay":
        return (ctx.manifest.get("env_provenance") or {}).get(role) \
            == "reused"
    marker = _read_marker(env_dir)
    return bool(marker and marker.get("spec_hash") == spec_hash
                and (env_dir / _env_python_name()).exists())


def _rmtree(path: Path) -> None:
    """shutil.rmtree that retries readonly files (Windows venv bits)."""

    def on_error(func, target, _exc):
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, ignore_errors=False, onerror=on_error)


def _make_venv(ctx, env_rel: str, python_ver: str) -> None:
    if ctx.mode == "live" and (ROOT / env_rel).exists():
        _rmtree(ROOT / env_rel)
        if (ROOT / env_rel).exists():
            raise InstallFailed(
                f"cannot clear stale env {env_rel} (files locked, "
                "possibly by a stray process; kill it and re-run)")
    seeded = ctx.run_cmd(["uv", "python", "install", python_ver],
                         cwd=".", env_name=None, timeout=900, tool="uv")
    if seeded.returncode != 0:
        ctx.emit("log", {"message":
                         f"warning: uv python install {python_ver} "
                         f"exited {seeded.returncode}; trying venv anyway"})
    made = ctx.run_cmd(["uv", "venv", "-p", python_ver, env_rel],
                       cwd=".", env_name=None, timeout=900, tool="uv")
    if made.returncode != 0:
        raise InstallFailed(
            f"cannot create env {env_rel} (python {python_ver}):\n"
            f"{log_tail(made.stderr)}")


def _uv_install(ctx, cwd: str, env_name: str, args: list):
    py = ctx.env_python(env_name)
    return ctx.run_cmd(["uv", "pip", "install", "--python", py, *args],
                       cwd=cwd, env_name=None, timeout=900, tool="uv")


def _uv_uninstall(ctx, cwd: str, env_name: str, args: list):
    py = ctx.env_python(env_name)
    return ctx.run_cmd(["uv", "pip", "uninstall", "--python", py, *args],
                       cwd=cwd, env_name=None, timeout=900, tool="uv")


def _drop_after_coverage(ctx, ws_rel: str, env_name: str,
                         decided_package: str) -> None:
    """Uninstall coverage tooling from a built after env (live only).

    After suites never measure coverage, and leaving the plugins
    installed can crash collection outright (numba subclasses the
    removed coverage.types.Tracer at import; the repo's own test
    extras may pull them in). The decided package itself is never
    dropped. Replay skips this: its freeze is served from the
    recording, which already reflects the drop.
    """
    current = _freeze(ctx, env_name)
    decided = decided_package.lower()
    doomed = [n for n in ("coverage", "pytest-cov")
              if n in current and n != decided]
    if not doomed:
        return
    res = _uv_uninstall(ctx, ws_rel, env_name, doomed)
    if res.returncode != 0:
        ctx.emit("log", {"message":
                         f"warning: after env keeps "
                         f"{' + '.join(doomed)}: "
                         f"{log_tail(res.stderr, 2)}"})
        return
    ctx.emit("log", {"message":
                     f"setup: after env drops {' + '.join(doomed)} "
                     "(unused after the before run; breaks numba)"})


# uv venvs ship no pip, so `pip freeze` is empty there; read the
# installed distributions directly (works in every env, pip or not).
FREEZE_CODE = (
    "import importlib.metadata as _m\n"
    "rows = []\n"
    "for _d in _m.distributions():\n"
    "    _n = (_d.metadata['Name'] or '').strip()\n"
    "    if _n:\n"
    "        rows.append(f\"{_n}=={_d.version}\")\n"
    "print('\\n'.join(sorted(rows)))\n"
)


def _freeze(ctx, env_name: str) -> dict:
    py = ctx.env_python(env_name)
    res = ctx.run_cmd([py, "-c", FREEZE_CODE], cwd=".",
                      env_name=env_name, timeout=300)
    return freeze_dict(res.stdout)


def _install_args(info: ProjectInfo, tooling: list | None = None) -> list:
    if info.test_extras:
        args = [f".[{','.join(info.test_extras)}]"]
    else:
        args = ["."]
    for req in info.test_requirements:
        args += ["-r", req]
    return args + list(tooling if tooling is not None else TEST_TOOLING)


def extra_specs(root: str | Path, info: ProjectInfo) -> list | None:
    """Per-dependency specs for the test extras; None when unreadable."""
    if not info.test_extras:
        return []
    specs: list[str] = []
    found = False
    path = Path(root) / "pyproject.toml"
    if path.is_file():
        try:
            import tomllib

            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        opt = data.get("project", {}).get("optional-dependencies", {})
        for extra in info.test_extras:
            if extra in opt:
                found = True
                specs.extend(str(s) for s in opt[extra])
    cfg = Path(root) / "setup.cfg"
    if cfg.is_file():
        parser = configparser.ConfigParser()
        try:
            parser.read(cfg, encoding="utf-8")
            section = "options.extras_require"
            if parser.has_section(section):
                for extra in info.test_extras:
                    if parser.has_option(section, extra):
                        found = True
                        specs.extend(
                            ln.strip() for ln in
                            parser.get(section, extra).splitlines()
                            if ln.strip())
        except configparser.Error:
            pass
    return specs if found else None


def req_lines(root: str | Path, reqfile: str) -> list:
    """Installable lines of a requirements file (no comments/options)."""
    lines = []
    try:
        text = (Path(root) / reqfile).read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        lines.append(line)
    return lines


_REQ_HEAD_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*(\[[^\]]*\])?")


def _pin_spec(spec: str, version: str) -> str | None:
    """Rewrite a requirement pinned to ==version (extras kept)."""
    head, sep, markers = spec.partition(";")
    match = _REQ_HEAD_RE.match(head.strip())
    if not match:
        return None
    out = f"{match.group(1)}{match.group(2) or ''}=={version}"
    if markers.strip():
        out += f";{markers}"
    return out


def _float_constraint_name(unit_name: str) -> str:
    """Workspace-relative per-unit constraints file for a float."""
    slug = re.sub(r"[^a-z0-9]+", "-", unit_name.lower()).strip("-")
    return f".chainreaction-float-{slug or 'unit'}.txt"


def _write_constraints_file(ws_rel: str, cname: str, pins: list) -> None:
    """Write a constraints file into the workspace (caller gates replay)."""
    (ROOT / ws_rel).mkdir(parents=True, exist_ok=True)
    (ROOT / ws_rel / cname).write_text(
        "\n".join([*pins, ""]) if pins else "", encoding="utf-8")


def _install_best_effort(ctx, ws_rel: str, env_name: str, info: ProjectInfo,
                         flag_args: list, lead_args: list,
                         constraint_args: list,
                         before_freeze: dict | None = None,
                         anchor: list | None = None,
                         tooling: list | None = None,
                         float_pins: list | None = None,
                         constraint_file: str | None = None,
                         ) -> tuple[bool, list]:
    """Per-unit install; returns (ok, skipped specs).

    The repo itself (lead_args) is fatal: False means the caller should
    fail (InstallFailed before, dependency-conflict after). Test-only
    units that cannot build (memray on Windows) are skipped with a
    loud warning and recorded, so one hostile extra cannot sink a run.
    With before_freeze (after envs), each unit installs pinned to its
    before version first: direct specs override the constraints file,
    so unpinned units would float. Anchor (the decided pkg==ver)
    rides every after unit so no unit can move the decision itself; a
    pin that fails floats under per-unit constraints (every pin except
    the floated unit's own: a bare float re-resolves the whole env to
    latest), flooring the == pins the resolver names as conflicting
    (floors never go backward or relax further), before the unit is
    skipped. Floors persist: float_pins is shared with the caller and
    the constraints file is rewritten, so later units build on the
    relaxed set.
    """
    skipped: list[str] = []
    anchor = anchor or []
    tools = tooling if tooling is not None else TEST_TOOLING
    last_err = {"stderr": ""}

    def attempt(args: list, fatal: bool, label: str,
                constrained: bool = True,
                float_unit: str | None = None,
                float_keep: list | None = None,
                quiet: bool = False) -> tuple[bool, bool]:
        """(succeeded, fatal_failure); skips are recorded + warned."""
        argv = [*flag_args]
        if float_unit is not None and float_keep:
            fname = _float_constraint_name(float_unit)
            if ctx.mode == "live":
                _write_constraints_file(ws_rel, fname, float_keep)
            argv += ["--constraint", fname]
        elif constrained:
            argv += constraint_args
        argv += args
        res = _uv_install(ctx, ws_rel, env_name, argv)
        last_err["stderr"] = res.stderr
        if res.returncode == 0:
            return True, False
        if fatal:
            return False, True
        if quiet:
            return False, False
        skipped.append(label)
        ctx.emit("log", {"message":
                         f"warning: skipping {label}: "
                         f"{log_tail(res.stderr, 2)}"})
        return False, False

    def attempt_float(spec: str, unit_name: str | None) -> None:
        """Float one unit, flooring resolver-named pins; else skip it.

        Multi-package cascades (scanpy + pynndescent + numba moving
        together for numpy 2) cannot float one unit at a time under
        tight pins: each attempt floors the == pin the resolver
        blames and retries, so the float moves the minimal
        sufficient set, never backward. Build failures (no
        conflict signal) skip without relaxing.
        """
        relaxed: list[str] = []
        for _ in range(10):
            keep = [p for p in (float_pins or [])
                    if _pin_name(p) != (unit_name or "")]
            ok, _ = attempt([spec, *anchor], False, spec,
                            constrained=False, float_unit=unit_name,
                            float_keep=keep, quiet=True)
            if ok:
                if relaxed:
                    ctx.emit("log", {"message":
                                     f"warning: {spec} floated; relaxed "
                                     f"{len(relaxed)} pin(s): "
                                     + ", ".join(relaxed)})
                return
            culprit = None
            if _is_resolver_conflict(last_err["stderr"]):
                culprit = find_conflicting_pin(
                    last_err["stderr"],
                    [_pin_name(p) for p in keep if _pin_is_exact(p)])
            if culprit is None or float_pins is None:
                break
            _floor_pin(float_pins, culprit)
            relaxed.append(culprit)
            if constraint_file is not None and ctx.mode == "live":
                _write_constraints_file(ws_rel, constraint_file, float_pins)
        skipped.append(spec)
        ctx.emit("log", {"message":
                         f"warning: skipping {spec}: "
                         f"{log_tail(last_err['stderr'], 2)}"})

    def attempt_unit(spec: str) -> None:
        pinned = None
        unit_name = None
        if before_freeze is not None:
            head = spec.partition(";")[0]
            name = _REQ_HEAD_RE.match(head.strip())
            if name:
                unit_name = name.group(1).lower()
                ver = before_freeze.get(unit_name)
                if ver:
                    pinned = _pin_spec(spec, ver)
        if pinned is not None:
            ok, _ = attempt([pinned, *anchor], False, pinned)
            if ok:
                return
            skipped.pop()  # a failed pin floats, it is not skipped
            ctx.emit("log", {"message":
                             f"warning: {pinned} incompatible here; "
                             "floating"})
            attempt_float(spec, unit_name)
            return
        attempt([spec, *anchor], False, spec)

    _, fatal = attempt(lead_args, True, "")
    if fatal:
        return False, skipped
    for pkg in tools:
        if before_freeze is None:
            _, fatal = attempt([pkg], pkg == "pytest", pkg)
            if fatal:
                return False, skipped
        else:
            attempt_unit(pkg)
    specs = extra_specs(ROOT / ws_rel, info)
    if specs is None:
        if info.test_extras:
            attempt([f".[{','.join(info.test_extras)}]", *anchor], False,
                    f".[{','.join(info.test_extras)}]")
    else:
        for spec in specs:
            attempt_unit(spec)
    for req in info.test_requirements:
        if before_freeze is None:
            ok, _ = attempt(["-r", req], False, f"-r {req}")
            if ok:
                continue
            skipped.pop()  # replace the file skip with per-line skips
            for line in req_lines(ROOT / ws_rel, req):
                attempt([line], False, line)
        else:
            for line in req_lines(ROOT / ws_rel, req):
                attempt_unit(line)
    return True, skipped


def build_envs(ctx, *, key: str, ws_rel: str, info: ProjectInfo,
               decision: Decision, commit_date: str, sha: str) -> dict:
    """Build (or reuse cached) before/after envs; save artifacts/envs.json.

    Returns the envs record and stashes it plus any dependency_conflict
    in ctx.state. A conflict is a finding, never an exception.
    """
    before_name, after_name = f"{key}/before", f"{key}/after"
    before_rel, after_rel = f"envs/{key}/before", f"envs/{key}/after"
    uvv = ctx.run_cmd(["uv", "--version"], cwd=".", env_name=None,
                      timeout=60, tool="uv")
    if uvv.missing or (uvv.returncode or 0) != 0:
        raise InstallFailed(
            "`uv` not found (install: https://docs.astral.sh/uv/); "
            "M2 env builds need uv")
    repo_args = _install_args(info)
    after_tools = after_tooling(info)
    after_repo_args = _install_args(info, after_tools)
    spec_hash = install_spec_hash(sha, info.python_version,
                                  ",".join(repo_args), decision.package,
                                  decision.to_version, commit_date)
    # The after env has its own spec: its tooling differs (no coverage
    # unless addopts need it), so a tooling change rebuilds afters
    # without disturbing befores. The coverage drop is part of the
    # spec: dropping it changes the env's contents.
    drop_cov = not needs_coverage_packages(info)
    after_spec_hash = install_spec_hash(
        spec_hash, ",".join(after_repo_args),
        "nocov" if drop_cov else "keepcov", AFTER_BUILD_REV)
    provenance: dict = {}
    # Before: the world as of the commit.
    before_note = None
    before_skipped: list = []
    if _env_cached(ctx, "before", ROOT / before_rel, spec_hash):
        provenance["before"] = "reused"
        ctx.emit("log", {"message": f"setup: reusing cached {before_rel}"})
        before_freeze = _freeze(ctx, before_name)
        marker = _read_marker(ROOT / before_rel) or {}
        before_note = marker.get("note")
        before_skipped = marker.get("skipped", [])
    else:
        provenance["before"] = "built"
        _make_venv(ctx, before_rel, info.python_version)
        flag_args = (["--exclude-newer", commit_date] if commit_date else [])
        res = _uv_install(ctx, ws_rel, before_name,
                          [*flag_args, *repo_args])
        if res.returncode != 0:
            ctx.emit("log", {"message":
                             "setup: full install failed; retrying "
                             "per-dependency"})
            ok, before_skipped = _install_best_effort(
                ctx, ws_rel, before_name, info, flag_args, ["."], [])
            if not ok:
                raise InstallFailed(
                    f"before env install failed:\n{log_tail(res.stderr)}"
                    f"{toolchain_hint(res.stderr)}")
        before_freeze = _freeze(ctx, before_name)
        installed = before_freeze.get(decision.package.lower())
        if installed and version_tuple(installed) >= version_tuple(
                decision.to_version):
            res2 = _uv_install(
                ctx, ws_rel, before_name,
                [*flag_args, f"{decision.package}<{decision.to_version}"])
            if res2.returncode != 0:
                raise InstallFailed(
                    f"before env constrain failed:\n"
                    f"{log_tail(res2.stderr)}"
                    f"{toolchain_hint(res2.stderr)}")
            before_freeze = _freeze(ctx, before_name)
            before_note = (
                f"{decision.package} resolved to {installed} "
                f"(>= {decision.to_version}); constrained to "
                f"<{decision.to_version}")
        if ctx.mode == "live":
            _marker_path(ROOT / before_rel).parent.mkdir(
                parents=True, exist_ok=True)
            _marker_path(ROOT / before_rel).write_text(
                json.dumps({"spec_hash": spec_hash, "note": before_note,
                            "skipped": before_skipped}),
                encoding="utf-8")
    ctx.emit("log", {"message":
                     f"setup: before env "
                     f"{decision.package}="
                     f"{before_freeze.get(decision.package.lower(), '?')}"})
    # After: change only what the decision changes.
    conflict = None
    after_skipped: list = []
    if _env_cached(ctx, "after", ROOT / after_rel, after_spec_hash):
        provenance["after"] = "reused"
        ctx.emit("log", {"message": f"setup: reusing cached {after_rel}"})
        after_freeze = _freeze(ctx, after_name)
        after_skipped = (_read_marker(ROOT / after_rel) or {}).get(
            "skipped", [])
    else:
        _make_venv(ctx, after_rel, info.python_version)
        pins = constraints_for(before_freeze, decision.package,
                               info.package)
        # Constraints live in the workspace (install cwd): the relative
        # name is identical on every machine, so replay keys match.
        cname = ".chainreaction-after-constraints.txt"
        final_err = ""
        best_effort_tried = False
        for _attempt in range(10):
            if ctx.mode == "live":
                _write_constraints_file(ws_rel, cname, pins)
            argv = (["--constraint", cname] if pins else []) + [
                f"{decision.package}=={decision.to_version}",
                *after_repo_args]
            res = _uv_install(ctx, ws_rel, after_name, argv)
            if res.returncode == 0:
                break
            final_err = res.stderr
            culprit = None
            if _is_resolver_conflict(res.stderr):
                culprit = find_conflicting_pin(
                    res.stderr, [_pin_name(p) for p in pins
                                 if _pin_is_exact(p)])
            if culprit is not None and _floor_pin(pins, culprit):
                ctx.emit("log", {"message":
                                 f"setup: after constraints relax "
                                 f"{culprit} to its before floor"})
                continue
            if not best_effort_tried:
                # No pin named: a build failure, not a resolver conflict.
                best_effort_tried = True
                ctx.emit("log", {"message":
                                 "setup: after install failed outside "
                                 "the pins; retrying per-dependency"})
                ok, after_skipped = _install_best_effort(
                    ctx, ws_rel, after_name, info, [],
                    [f"{decision.package}=={decision.to_version}", "."],
                    ["--constraint", cname] if pins else [],
                    before_freeze,
                    [f"{decision.package}=={decision.to_version}"],
                    tooling=after_tools, float_pins=pins,
                    constraint_file=cname)
                if ok:
                    break
            conflict = {
                "package": decision.package,
                "to_version": decision.to_version,
                "detail": log_tail(res.stderr),
            }
            break
        else:  # pragma: no cover - loop always breaks first
            conflict = {"package": decision.package,
                        "to_version": decision.to_version,
                        "detail": log_tail(final_err)}
        if conflict is not None:
            provenance["after"] = "conflict"
            if ctx.mode == "live":
                shutil.rmtree(ROOT / after_rel, ignore_errors=True)
            after_freeze = {}
            ctx.emit("log", {"message":
                             "setup: after env does not resolve "
                             "(dependency conflict recorded as a finding)"})
        else:
            provenance["after"] = "built"
            if ctx.mode == "live" and drop_cov:
                _drop_after_coverage(ctx, ws_rel, after_name,
                                     decision.package)
            after_freeze = _freeze(ctx, after_name)
            if ctx.mode == "live":
                _marker_path(ROOT / after_rel).write_text(
                    json.dumps({"spec_hash": after_spec_hash, "note": None,
                                "skipped": after_skipped}),
                    encoding="utf-8")
    collateral = collateral_diff(before_freeze, after_freeze,
                                 decision.package, info.package)
    if collateral:
        ctx.emit("log", {"message":
                         f"setup: {len(collateral)} collateral upgrade(s): "
                         + ", ".join(sorted(collateral))})
    if ctx.mode == "live":
        # Installs build the repo in the workspace and can generate
        # source files there (setuptools_scm's _version.py): remove
        # every untracked file so graph/test phases see pristine source.
        # Runs even on cache reuse (a previous build may have polluted).
        ctx.run_cmd(["git", "-C", ws_rel, "clean", "-fdx"],
                    cwd=".", env_name=None, timeout=300)
    record = {
        "before": {"python": info.python_version, "freeze": before_freeze,
                   "spec_hash": spec_hash,
                   "provenance": provenance["before"], "note": before_note},
        "after": ({"python": info.python_version, "freeze": after_freeze,
                   "spec_hash": after_spec_hash,
                   "provenance": provenance["after"], "note": None}
                  if conflict is None else None),
        "collateral": collateral,
        "conflict": conflict,
        "skipped": {"before": before_skipped, "after": after_skipped},
        "decision": {"package": decision.package,
                     "to_version": decision.to_version},
    }
    ctx.save_artifact("envs.json", record)
    ctx.state["envs"] = record
    ctx.state["dependency_conflict"] = conflict
    ctx.set_manifest(env_provenance=provenance)
    return record


# -- migration-guide discovery (M2 §5) --

GUIDE_PROPOSAL_SYSTEM = """\
You name the official migration guide or changelog for a dependency
upgrade. Reply with the single most official URL only, nothing else.
"""

_FETCH_ERRORS = (urllib.error.URLError, OSError)


def propose_guide_url(chat_fn, package: str, to_version: str) -> str:
    """Model-proposed official guide URL ("" when it names none)."""
    reply = chat_fn(
        GUIDE_PROPOSAL_SYSTEM,
        f"Decision: upgrade {package} to {to_version}\n\n"
        "Official migration guide or changelog URL (single URL only):",
        max_tokens=2000)
    match = re.search(r"https?://\S+", str(reply))
    return match.group(0).rstrip(").,") if match else ""


def _guide_mentions(text, package: str) -> bool:
    return bool(text) and package.lower() in str(text).lower()


def discover_guide(ctx, *, package: str, to_version: str):
    """Find the migration guide; return (text, url, method).

    Order: request guide_url → model proposal (fetch-verified) → PyPI
    project links → GuideNotFound. Replay follows the recorded method
    so every gateway call replays in order. Only network errors fall
    through; Cancelled and ReplayMiss always propagate.
    """
    if ctx.mode == "replay":
        method = ctx.manifest.get("guide_method") or "request"
        if method in ("model", "pypi"):
            proposed = propose_guide_url(ctx.llm, package, to_version)
            if method == "model":
                return ctx.fetch(proposed), proposed, method
            if proposed and ctx.manifest.get("guide_proposal_fetched"):
                ctx.fetch(proposed)  # discard; mirrors the live attempt
        final = ctx.manifest.get("guide_url") or ""
        return ctx.fetch(final), final, method
    requested = ctx.manifest.get("guide_url") or ""
    if requested:
        ctx.set_manifest(guide_method="request",
                         guide_proposal_fetched=False)
        return ctx.fetch(requested), requested, "request"
    proposed = propose_guide_url(ctx.llm, package, to_version)
    fetched = False
    if proposed:
        try:
            text = ctx.fetch(proposed)
            fetched = True
        except _FETCH_ERRORS:
            text = None
        if _guide_mentions(text, package):
            ctx.set_manifest(guide_url=proposed, guide_method="model",
                             guide_proposal_fetched=True)
            return text, proposed, "model"
    for url in ctx.state.get("pypi_guide_urls") or []:
        try:
            text = ctx.fetch(url)
        except _FETCH_ERRORS:
            continue
        if _guide_mentions(text, package):
            ctx.set_manifest(guide_url=url, guide_method="pypi",
                             guide_proposal_fetched=fetched)
            return text, url, "pypi"
    raise GuideNotFound(
        f"no migration guide found for {package} {to_version} "
        "(model proposal and PyPI links failed); pass guide_url")


# -- unused-decision check (M2 §3: warn, don't fail) --

# PyPI names whose top-level import is a different word entirely.
KNOWN_IMPORT_ALIASES = {
    "pillow": {"pil"},
    "scikit-learn": {"sklearn"},
    "beautifulsoup4": {"bs4"},
    "pyyaml": {"yaml"},
}


def import_roots(package: str) -> set:
    """Plausible top-level import names for a PyPI package."""
    base = (package or "").strip()
    cands = {base, base.lower(), base.replace("-", "_"),
             base.replace("-", "_").lower(), base.replace("-", ""),
             base.replace("_", "")}
    cands |= KNOWN_IMPORT_ALIASES.get(base.lower(), set())
    return {c.lower() for c in cands if c}


def package_imported_anywhere(root: str | Path, package: str) -> bool:
    """True when any .py under root imports the package (static scan)."""
    want = import_roots(package)
    if not want:
        return False
    for path in Path(root).rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name or "" for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(n.split(".")[0].lower() in want for n in names if n):
                return True
    return False
