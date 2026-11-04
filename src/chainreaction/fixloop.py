"""M5 fix loop: search/replace rounds -> probe/tests -> verified patch.

The model proposes search/replace edits (never unified diffs) against a
throwaway copy of the source tree (``fixwork/<run_id>/``); every probe
and suite run goes through the run context's recorded gateways, so
replay reproduces every round and a byte-identical ``final.patch``.

Honesty is structural: a fix only counts when the tests it targets go
from failing to passing with no new failures, guard violations mark
the result ``unverified`` even when tests pass, and the loop stops
with a recorded reason (green, rounds exhausted, no progress, timeout,
or error). Live and replay execute the same code path: replay copies
the source, applies the recorded edits, and serves test results from
the recording.
"""

import difflib
import json
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

MAX_ROUNDS = 5
VALIDATE_RETRIES = 2
WINDOW_RADIUS = 40
ROUND_TIMEOUT_S = 600
STAGE_TIMEOUT_S = 1800
SUITE_TIMEOUT_S = 1200
REASONING_EFFORT = "high"
FIXWORK_DIRNAME = "fixwork"

# Pytest summary counts ("3 failed, 2 passed, 1 skipped, 5 warnings in
# 12.34s"); shared with testmap.parse_suite_counts. The error branch
# covers both "1 error" and "2 errors".
COUNT_RE = re.compile(r"(\d+) (passed|failed|skipped|error|errors)\b")

FIX_JSON_SCHEMA = {
    "name": "fix_edits",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "search": {"type": "string"},
                        "replace": {"type": "string"},
                        "test_file": {"type": "boolean"},
                    },
                    "required": ["path", "search", "replace",
                                 "test_file"],
                    "additionalProperties": False,
                },
            },
            "rationale": {"type": "string"},
            "addresses": {"type": "array",
                          "items": {"type": "string"}},
        },
        "required": ["edits", "rationale", "addresses"],
        "additionalProperties": False,
    },
}

FIX_RESPONSE_FORMAT = {"type": "json_schema",
                       "json_schema": FIX_JSON_SCHEMA}

FIX_SYSTEM_PROMPT = """\
You are the ChainReaction fix proposer. Given one current failure, the
change items behind it, and numbered file windows, emit search/replace
edits that fix exactly that failure. Output a single JSON object:
{"edits": [{"path": "...", "search": "...", "replace": "...",
"test_file": false}], "rationale": "...", "addresses": ["symbol"]}.
Rules:
- path is repo-relative (e.g. "nibabel/casting.py"). Fix package
  sources only; set "test_file": true only when the addressed symbol
  is used solely in tests (flagged and reported either way).
- search is exact existing text of >=1 full line and must occur
  exactly once in the file; copy it from the numbered window.
- Minimal change; no new dependencies; never pin or edit dependency
  versions; never skip, delete, or xfail tests; never wrap a failure
  in try/except to silence it.
- Keep behavior equivalent on the before and after versions where
  possible; prefer the change item's documented replacement.
- rationale is <=3 sentences and cites the change item used."""


# -- workdir --

def fixwork_dir(root: str | Path, run_id: str) -> Path:
    """Throwaway tree for a run's fix loop (never the user's repo)."""
    return Path(root) / FIXWORK_DIRNAME / run_id


def copy_source_to_fixwork(source: str | Path, workdir: str | Path) -> Path:
    """Fresh work copy of the source tree (the original stays pristine).

    Version control carries over (setuptools_scm/hatch-vcs packages
    compute their version from it; without .git the work copy fails
    imports the real tree passes). Bytecode caches never do: stale
    .pyc would shadow patched sources.
    """
    workdir = Path(workdir)
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, workdir,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc", ".pytest_cache"),
                    ignore_dangling_symlinks=True)
    return workdir


def prune_fixwork(root: str | Path, keep: int = 3,
                  min_age_s: int = 3600) -> list[str]:
    """Delete old fixwork trees, keeping the newest few and fresh ones.

    Returns the removed run ids. Fresh trees (< min_age_s) are never
    touched, so concurrent runs cannot prune each other.
    """
    base = Path(root) / FIXWORK_DIRNAME
    try:
        entries = sorted(
            (p for p in base.iterdir()
             if p.is_dir() and not p.is_symlink()),
            key=lambda p: p.stat().st_mtime)
    except OSError:
        return []
    now = time.time()
    victims = entries[:-keep] if keep else list(entries)
    removed = []
    for entry in victims:
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if age < min_age_s:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed.append(entry.name)
    return removed


def _clear_pycache(workdir: str | Path) -> None:
    """Drop bytecode caches so the next run sees patched sources."""
    for cache in Path(workdir).rglob("__pycache__"):
        if cache.is_dir() and not cache.is_symlink():
            shutil.rmtree(cache, ignore_errors=True)


# -- edits --

@dataclass
class Edit:
    path: str  # repo-relative posix path
    search: str
    replace: str
    test_file: bool = False


def parse_edits(data) -> tuple[list[Edit], str | None]:
    """Model JSON -> edits, or (None-ish, error). Never raises."""
    if not isinstance(data, dict):
        return [], "response is not a JSON object"
    raw_edits = data.get("edits")
    if not isinstance(raw_edits, list) or not raw_edits:
        return [], "response has no edits array"
    edits = []
    for i, raw in enumerate(raw_edits):
        if not isinstance(raw, dict):
            return [], f"edit {i} is not an object"
        path = raw.get("path") or ""
        search = raw.get("search") or ""
        replace = raw.get("replace") or ""
        if not path or not search:
            return [], (f"edit {i} needs a path and a non-empty "
                         "search string")
        if "\x00" in search or "\x00" in replace:
            return [], f"edit {i} contains NUL bytes"
        edits.append(Edit(path=str(path).replace("\\", "/"),
                          search=search, replace=replace,
                          test_file=bool(raw.get("test_file", False))))
    return edits, None


_DEP_NAMES = ("pyproject.toml", "setup.py", "setup.cfg", "tox.ini",
              "pytest.ini", "requirements.txt", "constraints.txt",
              "Pipfile", "poetry.lock", "uv.lock")
_DEP_PREFIXES = ("requirements", ".github/", ".circleci/", ".ci/",
                 "azure-pipelines", ".travis.yml", "tox.ini")


def _is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    return parts[-1].startswith("test_") or "tests" in parts[:-1] or \
        parts[-1].endswith("_test.py")


def _is_packaging_path(rel: str) -> bool:
    base = rel.split("/")[-1]
    lowered = rel.lower()
    return base in _DEP_NAMES or lowered.startswith(_DEP_PREFIXES)


def validate_edits(edits: list[Edit], workdir: str | Path, package: str,
                   test_only_ok: bool = False) -> list[str]:
    """Rejection reasons ([] when every edit may apply).

    path must exist inside the workdir package tree (test files only
    when flagged test-only and the round allows them); search must
    occur exactly once (match counts are reported for retry).
    """
    from chainreaction.graph.repo import package_dir

    errors = []
    workdir = Path(workdir)
    try:
        pkgdir = package_dir(workdir, package).resolve()
    except OSError:
        return ["cannot resolve the package dir"]
    for edit in edits:
        rel = edit.path.strip().lstrip("/")
        if not rel or ".." in Path(rel).parts:
            errors.append(f"{edit.path or '(empty)'}: escapes the tree")
            continue
        target = workdir / rel
        try:
            resolved = target.resolve()
        except OSError:
            errors.append(f"{rel}: cannot resolve")
            continue
        if workdir.resolve() not in [*resolved.parents, resolved]:
            errors.append(f"{rel}: escapes the tree")
            continue
        if not resolved.is_file():
            errors.append(f"{rel}: no such file")
            continue
        if resolved.suffix != ".py":
            errors.append(f"{rel}: not a Python source file")
            continue
        if _is_packaging_path(rel):
            errors.append(f"{rel}: packaging/CI files are off limits")
            continue
        in_package = pkgdir in [*resolved.parents, resolved]
        is_test = _is_test_path(rel)
        if is_test and not (edit.test_file and test_only_ok):
            errors.append(
                f"{rel}: test files need test_file=true and a "
                "test-only failure")
            continue
        if not in_package and not is_test and \
                resolved.name != "conftest.py":
            errors.append(f"{rel}: outside the package tree")
            continue
        try:
            text = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            errors.append(f"{rel}: unreadable")
            continue
        count = text.count(edit.search)
        if count == 0 and "\r\n" in text:
            count = text.replace("\r\n", "\n").count(
                edit.search.replace("\r\n", "\n"))
        if count != 1:
            errors.append(
                f"{rel}: search occurs {count} times, need exactly 1")
    return errors


def apply_edits(workdir: str | Path,
                edits: list[Edit]) -> list[str]:
    """Splice validated edits into the work copy; return touched relpaths.

    Matching runs on LF-normalized text (checkouts may carry CRLF);
    the file keeps its original endings. Callers run validate_edits
    first; a disappeared unique match raises ValueError.
    """
    touched = []
    for edit in edits:
        rel = edit.path.strip().lstrip("/")
        target = Path(workdir) / rel
        raw = target.read_bytes()
        text = raw.decode("utf-8")
        ending = "\r\n" if text.count("\r\n") > text.count("\n") / 2 \
            else "\n"
        flat = text.replace("\r\n", "\n")
        want = edit.search.replace("\r\n", "\n")
        if flat.count(want) != 1:
            raise ValueError(
                f"{rel}: search no longer occurs exactly once")
        done = flat.replace(want, edit.replace.replace("\r\n", "\n"), 1)
        if ending == "\r\n":
            done = done.replace("\n", "\r\n")
        target.write_bytes(done.encode("utf-8"))
        touched.append(rel)
    return sorted(set(touched))


# -- diffs (computed, deterministic, git apply-able) --

def _diff_lines(relpath: str, old: list[str], new: list[str]) -> list[str]:
    """Unified diff hunks with fixed headers (no timestamps)."""
    return list(difflib.unified_diff(
        old, new, fromfile=f"a/{relpath}", tofile=f"b/{relpath}"))


def _split_diff_lines(text: str) -> list[str]:
    """File text -> diff lines (only a missing trailing NL stays bare)."""
    if text == "":
        return []
    chunks = text.split("\n")
    if chunks and chunks[-1] == "":
        return [ln + "\n" for ln in chunks[:-1]]
    return [ln + "\n" for ln in chunks[:-1]] + [chunks[-1]]


def unified_diff_for(relpath: str, old_text: str, new_text: str) -> str:
    """Deterministic unified diff of two file contents (LF, "" if same).

    Only a genuinely unterminated last line stays bare, and difflib's
    missing "\\ No newline" marker is re-added after it — otherwise
    git apply mangles the last line.
    """
    old = old_text.replace("\r\n", "\n")
    new = new_text.replace("\r\n", "\n")
    if old == new:
        return ""
    out = _diff_lines(relpath, _split_diff_lines(old),
                      _split_diff_lines(new))
    fixed: list[str] = []
    for line in out:
        fixed.append(line)
        if line.startswith(("---", "+++", "@@")):
            continue
        if not line.endswith("\n"):
            fixed.append("\n\\ No newline at end of file\n")
    text = "".join(fixed)
    if not text.endswith("\n"):
        text += "\n"
    return text


def workdir_diff(source_root: str | Path, workdir: str | Path,
                 paths: list[str]) -> str:
    """Combined diff of source vs work copy over touched paths (sorted)."""
    chunks = []
    for rel in sorted(paths):
        old_file = Path(source_root) / rel
        new_file = Path(workdir) / rel
        try:
            old_text = old_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            old_text = ""
        try:
            new_text = new_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            new_text = ""
        chunk = unified_diff_for(rel, old_text, new_text)
        if chunk:
            chunks.append(chunk)
    return "".join(chunks)


# -- prompt context --

def file_window(workdir: str | Path, relpath: str, line: int,
                radius: int = WINDOW_RADIUS) -> str:
    """Numbered ±radius line window ("" when unreadable)."""
    try:
        lines = (Path(workdir) / relpath).read_text(
            encoding="utf-8").replace("\r\n", "\n").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    if not lines:
        return ""
    anchor = min(max(line, 1), len(lines))
    lo = max(1, anchor - radius)
    hi = min(len(lines), anchor + radius)
    return "\n".join(f"{i}: {lines[i - 1]}" for i in range(lo, hi + 1))


def find_usages(graph, symbol: str) -> list[str]:
    """Sorted repo-relative file:line uses of a symbol (graph edges)."""
    from chainreaction.propagate import matches_symbol

    if graph is None:
        return []
    from chainreaction.graph.repo import relative_posix

    hits = set()
    for _u, v, d in graph.edges(data=True):
        if d.get("kind") != "USES_EXTERNAL":
            continue
        if matches_symbol(v, {symbol}) is None:
            continue
        try:
            rel = relative_posix(d.get("file", ""), ".")
        except Exception:
            continue
        lineno = d.get("line")
        if rel and isinstance(lineno, int):
            hits.add(f"{rel}:{lineno}")
    return sorted(hits)


def site_symbols(graph, relpath: str, line: int,
                 broken: set[str]) -> set[str]:
    """Broken symbols used at one file:line (graph edges)."""
    from chainreaction.propagate import matches_symbol

    if graph is None:
        return set()
    from chainreaction.graph.repo import relative_posix

    found = set()
    for _u, v, d in graph.edges(data=True):
        if d.get("kind") != "USES_EXTERNAL":
            continue
        try:
            rel = relative_posix(d.get("file", ""), ".")
        except Exception:
            continue
        if rel != relpath or d.get("line") != line:
            continue
        sym = matches_symbol(v, set(broken))
        if sym is not None:
            found.add(sym)
    return found


def in_tree_frames(frames, root: str | Path,
                   package: str) -> list[str]:
    """In-tree frames as repo-relative path:line, deepest first.

    Mirrors the report's failure-site rule: frames under the root
    relativize; frames in the env's installed copy map back through
    the package dir; foreign frames never match.
    """
    from chainreaction.graph.repo import package_dir, relative_posix
    from chainreaction.verifier import site_packages_subpath

    try:
        root_abs = Path(root).resolve()
    except OSError:
        return []
    out = []
    for frame in reversed(frames or []):
        if not str(frame.file).endswith(".py"):
            continue
        # pytest headers are cwd-relative (the work copy); probes are
        # absolute. Relative frames resolve against the root first.
        cand = Path(frame.file)
        if not cand.is_absolute():
            cand = root_abs / cand
        try:
            rel = cand.resolve().relative_to(root_abs)
        except (ValueError, OSError):
            rel = None
        if rel is not None:
            out.append(f"{rel.as_posix()}:{frame.line}")
            continue
        sub = site_packages_subpath(frame.file, package)
        if sub is not None:
            out.append(
                f"{relative_posix(package_dir(root, package) / sub, root)}"
                f":{frame.line}")
    return out


def failure_site_from_frames(frames, root: str | Path,
                             package: str) -> str | None:
    """Deepest in-tree frame as repo-relative "path:line" (None if none)."""
    hits = in_tree_frames(frames, root, package)
    return hits[0] if hits else None


def stub_caller_frames(stderr: str, root: str | Path,
                       package: str) -> list[str]:
    """Vendor-stub routing failures carry no traceback; the stub's
    recorded caller (a real stack frame) is ground truth instead."""
    from chainreaction.verifier import Frame

    match = re.search(r"caller=([^\s)]+\.py):(\d+)", stderr or "")
    if not match:
        return []
    stub_frame = [Frame(match.group(1), int(match.group(2)), "?")]
    return in_tree_frames(stub_frame, root, package)


def _resolve_test_import(test_rel: str, package: str, level: int,
                         module: str | None, name: str) -> str | None:
    """One imported name -> dotted module (None when outside package)."""
    if level == 0:
        dotted = module or name
    else:
        anchor = test_rel.replace("\\", "/").removesuffix(".py").split(
            "/")[:-level]
        if module:
            dotted = ".".join([*anchor, *module.split(".")])
        else:
            dotted = ".".join([*anchor, name])
    if dotted != package and not dotted.startswith(package + "."):
        return None
    return dotted


def test_import_windows(workdir: str | Path, test_id: str, package: str,
                        graph=None, broken: frozenset = frozenset(),
                        ) -> list[tuple[str, int]]:
    """Package files a failing test imports (for assert-style failures).

    Assert tracebacks name only the test file, so the fix context
    would miss the source under test; the test's own imports recover
    it. Each window centers on the first broken-symbol use in that
    file when the graph knows one, else the top.
    """
    import ast

    rel = test_id.split("::")[0]
    try:
        tree = ast.parse((Path(workdir) / rel).read_text(
            encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    dotted: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = _resolve_test_import(rel, package, 0, None,
                                           alias.name)
                if mod is not None:
                    dotted.append(mod)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                mod = _resolve_test_import(rel, package, node.level,
                                           node.module, alias.name)
                if mod is not None:
                    dotted.append(mod)
    out = []
    for mod in dict.fromkeys(dotted):
        tail = mod.replace(".", "/")
        cand = f"{tail}/__init__.py" if (Path(workdir) / tail).is_dir() \
            else f"{tail}.py"
        if not (Path(workdir) / cand).is_file():
            continue
        line = 1
        if graph is not None and broken:
            best = None
            for _u, v, d in graph.edges(data=True):
                if d.get("kind") != "USES_EXTERNAL":
                    continue
                try:
                    from chainreaction.graph.repo import relative_posix
                    from chainreaction.propagate import matches_symbol

                    if relative_posix(d.get("file", ""),
                                      ".") != cand:
                        continue
                    if matches_symbol(v, set(broken)) is None:
                        continue
                    lineno = d.get("line")
                    if isinstance(lineno, int) and (
                            best is None or lineno < best):
                        best = lineno
                except Exception:
                    continue
            if best is not None:
                line = best
        out.append((cand, line))
        if len(out) >= 2:
            break
    return out


# -- honesty guards --

_SKIP_RE = re.compile(r"pytest\.skip|pytest\.mark\.skip|@pytest\.mark\.skip|"
                      r"pytest\.xfail|@pytest\.mark\.xfail|unittest\.skip|"
                      r"@skip\b|pytestmark.*skip|pytestmark.*xfail")
_REMOVED_TEST_RE = re.compile(r"^[\s]*def\s+test")
# Only the silencing shapes: bare `except:`, `except Exception`, and
# tuples naming Exception/BaseException. A specific exception with a
# pass fallback (except PackageNotFoundError: pass) is defensive
# code, not failure-silencing.
_EXCEPT_CLS = (r"(?:Exception|BaseException)\b\s*(?:as\s+\w+\s*)?"
               r"|\([^)]*\b(?:Exception|BaseException)\b[^)]*\)"
               r"\s*(?:as\s+\w+\s*)?")
_EXCEPT_PASS_RE = re.compile(rf"except\s*\*?\s*(?:{_EXCEPT_CLS})?:\s*pass\s*"
                             r"(#.*)?$")
_EXCEPT_BLOCK_RE = re.compile(rf"except\s*\*?\s*(?:{_EXCEPT_CLS})?:\s*"
                              r"(#.*)?$")


def guard_checks(touched: list[str], added: list[str],
                 removed: list[str]) -> list[str]:
    """Final-patch honesty violations ([] when clean).

    added/removed are patch body lines without their +/- prefix.
    Violations mark the fix unverified even when tests pass.
    """
    violations = []
    for rel in sorted(touched):
        if _is_packaging_path(rel):
            violations.append(f"{rel}: dependency/packaging file edited")
    for line in added:
        if _SKIP_RE.search(line):
            violations.append(f"added skip/xfail marker: {line.strip()[:80]}")
            break
    for line in removed:
        if _REMOVED_TEST_RE.match(line):
            violations.append(
                f"removed test function: {line.strip()[:80]}")
            break
    silenced = any(_EXCEPT_PASS_RE.search(line) for line in added)
    if not silenced:
        for i, line in enumerate(added):
            if _EXCEPT_BLOCK_RE.search(line.strip()):
                rest = [ln for ln in added[i + 1:i + 4]
                        if ln.strip() and not ln.strip().startswith("#")]
                if rest and rest[0].strip() == "pass":
                    silenced = True
                    break
    if silenced:
        violations.append("added except-pass silencer")
    return violations


def patch_body_lines(source_root: str | Path, workdir: str | Path,
                     touched: list[str]
                     ) -> tuple[list[str], list[str]]:
    """(added, removed) body lines across touched files (no prefixes)."""
    added: list[str] = []
    removed: list[str] = []
    for rel in sorted(touched):
        try:
            old = (Path(source_root) / rel).read_text(
                encoding="utf-8").replace("\r\n", "\n").splitlines()
        except (OSError, UnicodeDecodeError):
            old = []
        try:
            new = (Path(workdir) / rel).read_text(
                encoding="utf-8").replace("\r\n", "\n").splitlines()
        except (OSError, UnicodeDecodeError):
            new = []
        for line in difflib.unified_diff(old, new, lineterm=""):
            if line.startswith("+") and not line.startswith("+++"):
                added.append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                removed.append(line[1:])
    return added, removed


# -- vendor stub probe --

STUB_PREAMBLE = '''\
"""Fix-probe stub: the Meta call path completes without network.

A fake `openai` module answers chat completions in-process (inserted
into sys.modules before the probe body runs, so no installed SDK or
key is needed); real sockets refuse loudly so no fix can bill or
hang on a live request. A closing epilog prints
STUB_BASE_URL=<base_url> and fails when the client never routes
to Meta.
"""
import sys as _sys
import types as _types

_BLOCKED = OSError("network blocked in fix probe (stub only)")


def _no_network(*_a, **_k):
    raise _BLOCKED


import socket as _socket  # noqa: E402

_socket.socket = _no_network
_socket.create_connection = _no_network

_seen = {}


class _StubMessage:
    content = "stub reply"


class _StubChoice:
    message = _StubMessage()


class _StubUsage:
    prompt_tokens = 3
    completion_tokens = 2


class _StubResponse:
    choices = [_StubChoice()]
    usage = _StubUsage()


class _StubCompletions:
    def create(self, **kwargs):
        _seen["model"] = kwargs.get("model")
        return _StubResponse()


class _StubChat:
    completions = _StubCompletions()


class _StubOpenAI:
    def __init__(self, *args, **kwargs):
        _seen["base_url"] = kwargs.get("base_url")
        import traceback as _tb

        for frame in reversed(_tb.extract_stack()[:-1]):
            name = frame.filename
            if name.endswith(".py") and name != "<string>":
                _seen["caller"] = f"{name}:{frame.lineno}"
                break

    @property
    def chat(self):
        return _StubChat()


_fake_openai = _types.ModuleType("openai")
_fake_openai.OpenAI = _StubOpenAI
_sys.modules["openai"] = _fake_openai
'''

STUB_EPILOG = '''\
print(f"STUB_BASE_URL={_seen.get('base_url') or ''}")
print(f"STUB_CALLER={_seen.get('caller') or ''}")
if "meta" not in str(_seen.get("base_url") or "").lower():
    raise SystemExit(
        "stub: Meta call path never routed "
        f"(base_url={_seen.get('base_url')!r} "
        f"caller={_seen.get('caller') or '?'})")
'''


def stubbed_probe_code(probe_code: str) -> str:
    """Decision probe wrapped in the network stub (fix runs only).

    The epilog runs only when the probe body completes, so green
    means the Meta call path completed with network calls mocked;
    SystemExit anywhere else fails the probe loudly. (An atexit
    check would fail silently: SystemExit there never sets the
    exit code.)
    """
    return STUB_PREAMBLE + "\n" + probe_code + "\n" + STUB_EPILOG


# -- round prompt --

def _item_block(item) -> str:
    """One change item as prompt context (tolerates dicts or objects)."""
    get = (lambda k, d="": item.get(k, d))
    if not isinstance(item, dict):
        get = (lambda k, d="": getattr(item, k, d) or d)
    lines = [f"- symbol: {get('symbol')}", f"  kind: {get('kind')}"]
    if get("quote"):
        lines.append(f"  quote: {str(get('quote'))[:500]}")
    if get("replacement"):
        lines.append(f"  replacement: {str(get('replacement'))[:300]}")
    if get("source_url"):
        lines.append(f"  source: {get('source_url')}")
    if get("introduced_in"):
        lines.append(f"  introduced_in: {get('introduced_in')}")
    detail = get("detail")
    if detail is not None and not isinstance(detail, str):
        exp = getattr(detail, "expected", "") or ""
        act = getattr(detail, "actual", "") or ""
        if exp or act:
            lines.append(f"  decision needs: {exp} | code has: {act}")
    if get("code_file"):
        lines.append(f"  code site: {get('code_file')}:{get('code_line')}")
    if get("code_quote"):
        lines.append(f"  code quote: {str(get('code_quote'))[:300]}")
    return "\n".join(lines)


def build_round_prompt(*, round_no: int, max_rounds: int,
                       fix_order: list[str], failure_kind: str,
                       failure_site: str | None, error_line: str,
                       trace_excerpt: str, items: list,
                       windows: dict[str, str], usages: dict[str, list[str]],
                       applied_summaries: list[str],
                       feedback: str = "") -> str:
    """Deterministic per-round model prompt (no timestamps, sorted)."""
    lines = [
        f"Round {round_no} of {max_rounds}.",
        f"Fix order: {', '.join(fix_order) or '(empty)'}",
        f"Current failure ({failure_kind}):",
    ]
    if failure_site:
        lines.append(f"Site: {failure_site}")
    lines += [f"Error: {error_line[:400]}", "Traceback (newest last):",
              trace_excerpt[:3000] or "(no traceback)"]
    if items:
        lines += ["", "Change items involved:"]
        for item in items:
            lines.append(_item_block(item))
    if windows:
        lines += ["", "File windows (line-numbered):"]
        for path in sorted(windows):
            lines += [f"--- {path} ---", windows[path] or "(unreadable)"]
    if usages:
        lines += ["", "Other usages of the same symbols in the package:"]
        for symbol in sorted(usages):
            other = sorted(usages[symbol])[:20]
            lines.append(f"- {symbol}: {', '.join(other) or '(none)'}")
    if applied_summaries:
        lines += ["", "Previously applied edits:"]
        lines += [f"- {s}" for s in applied_summaries]
    if feedback:
        lines += ["", feedback[:2000]]
    lines += ["",
              "Propose search/replace edits fixing exactly this failure."]
    return "\n".join(lines) + "\n"


# -- targeted tests --

def modules_for_files(files: list[str], target: str | Path,
                      package: str) -> set[str]:
    """Dotted modules for edited files (unmappable files are skipped)."""
    from chainreaction.testmap import file_to_module

    mods = set()
    for rel in files:
        mod = file_to_module(rel, target, package)
        if mod is not None:
            mods.add(mod)
    return mods


def correspondent_tests(source_root: str | Path, package: str,
                        files: list[str]) -> list[str]:
    """Test files by path correspondence (runs without a coverage map).

    pkg/foo.py -> pkg/tests/test_foo.py, pkg/test_foo.py,
    tests/test_foo.py (existing files only, sorted, capped).
    """
    out = set()
    for rel in files:
        name = Path(rel).stem
        if name.startswith("test_") or name.endswith("_test"):
            if (Path(source_root) / rel).is_file():
                out.add(rel)
            continue
        cands = [f"{package}/tests/test_{name}.py",
                 f"{package}/test_{name}.py",
                 f"tests/test_{name}.py",
                 f"test_{name}.py"]
        for cand in cands:
            if (Path(source_root) / cand).is_file():
                out.add(cand)
    return sorted(out)[:10]


def select_targeted(testmap, edited_files: list[str], target: str | Path,
                    package: str, collected: int,
                    source_root: str | Path,
                    ) -> tuple[str, list[str]]:
    """(method, tests) for a round's targeted run (deterministic).

    Coverage map wins; file correspondence covers runs without one;
    a targeted set over half the suite becomes the full suite.
    """
    if testmap is not None and getattr(testmap, "tests", None):
        mods = modules_for_files(edited_files, target, package)
        tests = testmap.covering(mods) if mods else []
        if tests and collected and len(tests) > collected / 2:
            return "full-suite (targeted set too large)", []
        if tests:
            return "coverage", tests
        return "coverage (no covering tests; running full suite)", []
    if not edited_files:
        return "full-suite (no edited files)", []
    files = correspondent_tests(source_root, package, edited_files)
    if files:
        return "file-correspondence", files
    return "full-suite (no correspondent tests)", []


# -- recorded model call --

FIX_MAX_TOKENS = 12000


def call_fix_llm(ctx, system: str, user: str,
                 timeout_s: int = ROUND_TIMEOUT_S) -> str:
    """One structured fix proposal (reasoning high, bounded, recorded).

    Raises TimeoutError when the model call exceeds the round budget
    (the round aborts; the loop continues). Cancelled propagates.
    """
    from chainreaction.runs.context import Cancelled

    pool = ThreadPoolExecutor(max_workers=1,
                              thread_name_prefix="cr-fix-llm")
    future = pool.submit(ctx.llm, system, user, FIX_MAX_TOKENS,
                         FIX_RESPONSE_FORMAT, REASONING_EFFORT)
    try:
        try:
            return future.result(timeout=timeout_s)
        except Cancelled:
            raise
        except BaseException:
            if ctx.cancelled:
                from chainreaction.runs.context import Cancelled as C
                raise C("run cancelled")
            raise
    finally:
        # Never wait out a hung call: the worker is orphaned (the API
        # client's own timeouts bound it) and the round aborts.
        future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)


def fix_prompt_hash(ctx, system: str, user: str) -> str:
    """prompt_hash for a fix call (same inputs ctx.llm hashes)."""
    from chainreaction import llm as llm_mod

    model = ctx.manifest.get("model") or llm_mod.model_name()
    return llm_mod.cache_key_for(model, system, user, FIX_RESPONSE_FORMAT,
                                 REASONING_EFFORT)


def recorded_llm_ms(ctx, prompt_hash: str, start_at: int = 0
                    ) -> tuple[int, int]:
    """(total ms, next cursor) for a prompt's recordings from a cursor.

    Sums every matching record at or after the cursor (length retries
    record twice under one hash); the cursor keeps repeated prompts
    from double-counting across rounds. Missing reads as (0, cursor).
    """
    from chainreaction.runs import store as store_mod

    try:
        text = store_mod.read_text_record(ctx.dir / store_mod.LLM_CALLS)
    except (OSError, ValueError):
        return 0, start_at
    total, cursor, index = 0, start_at, 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("prompt_hash") != prompt_hash:
            continue
        if index >= start_at:
            total += rec.get("duration_ms", 0) or 0
            cursor = index + 1
        index += 1
    return total, cursor


# -- item + record helpers --

def _item_symbol(item) -> str:
    if isinstance(item, dict):
        return item.get("symbol") or ""
    return getattr(item, "symbol", "") or ""


def _item_field(item, name: str, default=""):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default) or default


def _normalize_error_text(text: str) -> str:
    """Error text with common aliases expanded for symbol matching."""
    text = str(text or "")
    text = re.sub(r"\bnp\.", "numpy.", text)
    text = re.sub(r"\bpd\.", "pandas.", text)
    return text


def _symbol_in_text(symbol: str, text: str) -> bool:
    """True when a dotted symbol (or its leaf) names the error text.

    Degenerate symbols (junk leaves like "0" from a misread
    changeset) never match: a one-character leaf would match any
    error mentioning that digit or letter.
    """
    if not symbol or not text:
        return False
    leaf = symbol.rsplit(".", 1)[-1]
    if len(leaf) < 2 or (not leaf[0].isalpha() and leaf[0] != "_"):
        return False
    if symbol in text:
        return True
    return bool(re.search(rf"(?<![\w.]){re.escape(leaf)}(?![\w])", text))


def _scrub_probe(ctx, probe: dict) -> dict:
    """Probe record with storage-safe (scrubbed) trace text."""
    scrub = ctx.scrubber.text
    return {"ok": probe["ok"], "site": probe["site"],
            "error": scrub(probe.get("error") or ""),
            "trace": scrub(probe.get("trace") or ""),
            "modules": list(probe.get("modules") or []),
            "frames": list(probe.get("frames") or [])}


def _scrub_suite(ctx, record: dict, _output: str) -> dict:
    """Full-suite record, JSON-safe (ids are target-relative already)."""
    return {"failed": sorted(record["failed"]),
            "error_files": sorted(record["error_files"]),
            "counts": dict(record["counts"]), "total": record["total"],
            "escalated": record["escalated"]}


def _snapshot_files(workdir: Path, rels: list[str]) -> dict[str, str]:
    """Current contents of work files ("" when missing).

    Keys use apply_edits' normalization so round diffs line up.
    """
    out = {}
    for rel in rels:
        key = rel.strip().lstrip("/")
        try:
            out[key] = (workdir / key).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            out[key] = ""
    return out


def _read_work_file(workdir: Path, rel: str) -> str:
    try:
        return (workdir / rel.strip().lstrip("/")).read_text(
            encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


# -- loop --

class _SuiteTimeout(Exception):
    """A suite run exceeded its budget (recorded, not a crash)."""


def _error_line(output: str) -> str:
    """Last error-ish line of a probe/test output ("" when none)."""
    cands = [ln.strip() for ln in (output or "").splitlines()
             if ln.strip()]
    for line in reversed(cands):
        if re.match(r"^\w*(Error|Exception|assert|E  )", line):
            return line[:400]
    return cands[-1][:400] if cands else ""


def _trace_excerpt(output: str, keep: int = 25) -> str:
    """Newest-last traceback lines, scrubbed by the caller for storage."""
    lines = [ln for ln in (output or "").splitlines() if ln.strip()]
    return "\n".join(lines[-keep:])


def _counts_text(counts: dict) -> str:
    bits = [f"{v} {k}" for k, v in sorted(counts.items())]
    return ", ".join(bits) or "no tests"


@dataclass
class _Plan:
    source_root: Path
    workdir: Path
    pythonpath: Path
    package: str
    target_rel: str
    after_env: str
    before_env: str  # "" when the run has none
    probe_kind: str  # "import" or "decision"
    probe_code: str  # "" for import probes
    has_suite: bool
    max_rounds: int
    fix_order: list[str] = field(default_factory=list)


def _fix_pythonpath(workdir: Path, source_root: Path,
                    package: str) -> Path:
    """sys.path entry so fix runs import the work copy, not env copies.

    Derived from the source layout (flat vs src/) so it resolves
    before the work copy exists.
    """
    from chainreaction.graph.repo import package_dir

    try:
        tail = package_dir(source_root, package).relative_to(source_root)
    except (OSError, ValueError):
        return workdir
    parent = workdir / tail.parent
    return parent


def _resolve_plan(ctx) -> _Plan:
    from chainreaction.runs.scrub import ROOT

    scen = ctx.state["scenario"]
    is_preset = ctx.manifest.get("preset") is not None
    if is_preset:
        source_root = ROOT / scen.target
        target_rel = scen.target
        has_suite = True  # preset suites run from the work copy
    else:
        ws_rel = ctx.state["workspace"]
        source_root = ROOT / ws_rel
        target_rel = ws_rel
        info = ctx.state.get("project")
        has_suite = bool(getattr(info, "has_suite", False))
    run_id = ctx.manifest.get("run_id") or "adhoc"
    workdir = fixwork_dir(ROOT, run_id)
    try:
        max_rounds = int(ctx.manifest.get("fix_rounds") or MAX_ROUNDS)
    except (TypeError, ValueError):
        max_rounds = MAX_ROUNDS
    max_rounds = min(max(max_rounds, 1), 10)
    return _Plan(
        source_root=source_root, workdir=workdir,
        pythonpath=_fix_pythonpath(workdir, source_root, scen.package),
        package=scen.package, target_rel=target_rel,
        after_env=scen.verify_env, before_env=scen.before_env or "",
        probe_kind="decision" if scen.probe_code else "import",
        probe_code=scen.probe_code or "", has_suite=has_suite,
        max_rounds=max_rounds)


class _StageTimeout(Exception):
    """The 30-minute stage budget ran out (graceful stop, not a crash)."""


def _prompt_fix_order(ctx, first_site: str | None) -> list[str]:
    """Fix order for round prompts: vendor code sites, else probe site."""
    scen = ctx.state["scenario"]
    if getattr(scen, "probe_code", ""):
        items = getattr(ctx.state.get("changeset"), "items", None) or []
        order = [f"{i.symbol}: {i.code_file}:{i.code_line}"
                 for i in items
                 if getattr(i, "source", "") == "code"
                 and getattr(i, "code_file", "")]
        if order:
            return order
    return [first_site] if first_site else []


def _fix_adapter(ctx, env_name: str, pythonpath: Path, timeout: int):
    """ctx.run_cmd adapter importing the work copy (recorded).

    PYTHONPATH pins the work tree ahead of installed/editable copies
    so probes and suites exercise the patched sources; it is recorded
    scrubbed and ignored for replay matching. Outputs come back
    unscrubbed for failure-site parsing; storage re-scrubs.
    """
    from types import SimpleNamespace

    def run(cmd, cwd, extra_env=None):
        merged = dict(extra_env or {})
        merged["PYTHONPATH"] = str(pythonpath)
        res = ctx.run_cmd(cmd, cwd=cwd, env_name=env_name,
                          timeout=timeout, extra_env=merged)
        return SimpleNamespace(
            returncode=res.returncode,
            stdout=ctx.scrubber.untext(res.stdout),
            stderr=ctx.scrubber.untext(res.stderr), missing=res.missing,
            timed_out=res.timed_out, duration_ms=res.duration_ms)

    return run


_SUITE_BASE = ["-m", "pytest", ".", "-q", "--tb=short",
               "-p", "no:cacheprovider", "--continue-on-collection-errors",
               "-rfE"]


def _suite_argv(python: str, tests: list[str]) -> list[str]:
    if tests:
        return [python, "-m", "pytest", *tests, "-q", "--tb=short",
                "-p", "no:cacheprovider", "--continue-on-collection-errors",
                "-rfE"]
    return [python, *_SUITE_BASE]


class _Loop:
    """One fix stage execution (live and replay share it)."""

    def __init__(self, ctx, plan: _Plan):
        self.ctx = ctx
        self.plan = plan
        self.rounds: list[dict] = []
        self.touched_all: list[str] = []
        self.applied_summaries: list[str] = []
        self.cmd_ms = 0
        self.llm_cursors: dict[str, int] = {}
        self.started = time.monotonic()
        self.no_progress = 0
        self.prev_signature = None
        self.stop_reason = ""
        # Baselines (verify's for generic runs, measured for presets).
        self.baseline_failed: set[str] = set()
        self.baseline_files: set[str] = set()
        self.baseline_measured = False
        self.red_failed: set[str] = set()
        self.red_files: set[str] = set()
        self.red_provenance = ""
        # Freshest after-env suite state on the work tree.
        self.suite_failed: set[str] = set()
        self.suite_files: set[str] = set()
        self.suite_counts: dict = {}
        self.suite_total = 0
        self.suite_output = ""
        self.suite_fresh = False
        self.suite_escalated = False

    def _deadline_hit(self) -> bool:
        return time.monotonic() - self.started > STAGE_TIMEOUT_S

    def _check_continue(self) -> None:
        self.ctx.check_cancelled()
        if self._deadline_hit():
            raise _StageTimeout()

    def _llm_ms(self, prompt_hash: str) -> int:
        total, cursor = recorded_llm_ms(
            self.ctx, prompt_hash,
            self.llm_cursors.get(prompt_hash, 0))
        self.llm_cursors[prompt_hash] = cursor
        return total

    def run_probe(self):
        """Fast check on the work tree (after env); returns a record."""
        from chainreaction.verifier import (
            run_import_probe,
            run_snippet_probe,
        )

        self._check_continue()
        run = _fix_adapter(self.ctx, self.plan.after_env,
                           self.plan.pythonpath, 300)
        python = self.ctx.env_python(self.plan.after_env)
        if self.plan.probe_kind == "decision":
            probe = run_snippet_probe(
                python, self.plan.workdir,
                stubbed_probe_code(self.plan.probe_code),
                self.plan.package, run=run)
        else:
            probe = run_import_probe(python, self.plan.workdir,
                                     self.plan.package, run=run)
        frames = in_tree_frames(probe.frames, self.plan.workdir,
                                self.plan.package)
        if not frames:
            frames = stub_caller_frames(probe.stderr or "",
                                        self.plan.workdir,
                                        self.plan.package)
        site = frames[0] if frames else None
        # Sites parse from live paths, but every stored and prompted
        # string is scrubbed: replay's workdir differs, so unscrubbed
        # text would fork prompt hashes and leak absolute paths.
        scrub = self.ctx.scrubber.text
        error = scrub(_error_line(probe.stderr))
        self.ctx.emit("log", {"message":
                             f"fix: probe {'ok' if probe.ok else 'FAILED'}"
                             + (f" at {site}" if site and not probe.ok
                                else "")})
        return {"ok": probe.ok, "site": site, "error": error,
                "trace": scrub(_trace_excerpt(probe.stderr)),
                "modules": list(probe.modules), "frames": frames}

    def run_suite(self, tests: list[str], env_name: str, timeout: int,
                  label: str):
        """One pytest run on the work tree; returns (record, raw output).

        Mirrors verify's after-suite shape (same flags, COLUMNS,
        --noconftest escalation on collection collapse) so results
        compare apples-to-apples.
        """
        from chainreaction.testmap import parse_suite

        self._check_continue()
        run = _fix_adapter(self.ctx, env_name, self.plan.pythonpath,
                           timeout)
        python = self.ctx.env_python(env_name)
        res = run(_suite_argv(python, tests), self.plan.workdir,
                  extra_env={"COLUMNS": "200"})
        self.cmd_ms += res.duration_ms or 0
        if res.timed_out:
            raise _SuiteTimeout(f"{label} timed out after {timeout}s")
        parsed = parse_suite(res.stdout, ".", None)
        escalated = False
        output = res.stdout
        if parsed.total == 0 and not tests:
            self.ctx.emit("log", {"message":
                                 f"fix: {label} collected nothing; "
                                 "retrying with --noconftest"})
            res = run([*_suite_argv(python, []), "--noconftest"],
                      self.plan.workdir, extra_env={"COLUMNS": "200"})
            self.cmd_ms += res.duration_ms or 0
            if res.timed_out:
                raise _SuiteTimeout(f"{label} timed out after {timeout}s")
            parsed = parse_suite(res.stdout, ".", None)
            output = res.stdout
            escalated = True
        self.ctx.emit("log", {"message":
                             f"fix: {label}: "
                             f"{_counts_text(parsed.counts)}"})
        return {"failed": set(parsed.failed),
                "error_files": set(parsed.error_files),
                "counts": dict(parsed.counts), "total": parsed.total,
                "escalated": escalated}, output

    def run_full_after(self, reason: str):
        """Fresh full after-env suite; becomes the suite state."""
        record, output = self.run_suite([], self.plan.after_env,
                                        SUITE_TIMEOUT_S,
                                        f"full suite ({reason})")
        self.suite_failed = set(record["failed"])
        self.suite_files = set(record["error_files"])
        self.suite_counts = dict(record["counts"])
        self.suite_total = record["total"] or sum(
            record["counts"].values())
        self.suite_output = output
        self.suite_fresh = True
        self.suite_escalated = record["escalated"]
        return record

    def trace_for_test(self, test_id: str):
        """Single-test run for a failure's traceback (deterministic id)."""
        from chainreaction.verifier import parse_traceback

        record, output = self.run_suite(
            [test_id], self.plan.after_env, 300, f"trace {test_id}")
        frames = parse_traceback(output)
        in_tree = in_tree_frames(frames, self.plan.workdir,
                                 self.plan.package)
        site = in_tree[0] if in_tree else None
        scrub = self.ctx.scrubber.text
        return {"site": site, "error": scrub(_error_line(output)),
                "trace": scrub(_trace_excerpt(output)), "test": test_id,
                "frames": in_tree}

    def measure_baseline(self):
        """Pristine-tree suite for baselines (preset runs only).

        Returns (record, output); the caller assigns baseline vs red:
        with a before env the run is the excluded baseline, without
        one its failures are the red set and the baseline excludes
        nothing. Generic runs reuse verify's baseline instead. An
        unmeasurable baseline caps the final status at partial:
        green is unprovable then.
        """
        if not self.plan.has_suite:
            self.baseline_measured = True
            return {"ran": False, "reason": "no test suite"}, ""
        env = self.plan.before_env or self.plan.after_env
        role = "before" if self.plan.before_env else (
            "after (no before env; pristine tree)")
        try:
            record, output = self.run_suite([], env, SUITE_TIMEOUT_S,
                                            f"baseline suite ({role})")
        except _SuiteTimeout as exc:
            self.ctx.emit("log", {"message": f"fix: {exc}; green capped"})
            return {"ran": False,
                    "reason": "baseline suite timed out"}, ""
        self.baseline_measured = True
        return {"ran": True, "env": env,
                "failed": sorted(record["failed"]),
                "error_files": sorted(record["error_files"]),
                "counts": dict(record["counts"]),
                "total": record["total"],
                "escalated": record["escalated"]}, output

    def red_proof(self):
        """After-env suite on the pristine tree (preset runs only)."""
        record = self.run_full_after("red proof")
        self.red_failed = set(record["failed"])
        self.red_files = set(record["error_files"])
        self.red_provenance = "measured (pristine work tree)"
        return record

    # -- failure derivation --

    def bad_tests(self) -> set[str]:
        return set(self.suite_failed) - self.baseline_failed

    def bad_files(self) -> set[str]:
        return set(self.suite_files) - self.baseline_files

    def current_failure(self, probe):
        """Top failure: probe, first bad test, or first bad file."""
        if not probe["ok"]:
            return {"kind": "probe", "site": probe["site"],
                    "error": probe["error"], "trace": probe["trace"],
                    "test": None, "frames": list(probe.get("frames", []))}
        bad = sorted(self.bad_tests())
        if bad:
            try:
                traced = self.trace_for_test(bad[0])
            except _SuiteTimeout:
                from chainreaction.testmap import parse_failure_messages

                msgs = parse_failure_messages(self.suite_output, ".")
                scrub = self.ctx.scrubber.text
                return {"kind": "test",
                        "site": None,
                        "error": scrub(msgs.get(bad[0], ""))
                        or f"{bad[0]} failed (trace timed out)",
                        "trace": "", "test": bad[0], "frames": []}
            return {"kind": "test", "site": traced["site"],
                    "error": traced["error"], "trace": traced["trace"],
                    "test": bad[0], "frames": traced["frames"]}
        files = sorted(self.bad_files())
        if files:
            from chainreaction.testmap import parse_collection_errors
            from chainreaction.verifier import parse_traceback

            sigs = parse_collection_errors(self.suite_output, ".")
            error = self.ctx.scrubber.text(sigs.get(files[0], ""))
            site = None
            for frame in reversed(
                    parse_traceback(self.suite_output)):
                if str(frame.file).replace("\\", "/").endswith(files[0]):
                    site = f"{files[0]}:{frame.line}"
                    break
            resolved = site or f"{files[0]}:1"
            return {"kind": "collection", "site": resolved,
                    "error": error or f"collection error in {files[0]}",
                    "trace": error, "test": None, "frames": [resolved]}
        return None

    def items_for_failure(self, failure) -> list:
        """Change items behind a failure (frames, then error text)."""
        changeset = self.ctx.state.get("changeset")
        items = list(getattr(changeset, "items", None) or [])
        if not items:
            return []
        broken = set(self.ctx.state.get("symbols") or ())
        graph = self.ctx.state.get("graph")
        for frame in failure.get("frames", []) or []:
            path, _, lineno = frame.rpartition(":")
            if not lineno.isdigit():
                continue
            hits = site_symbols(graph, path, int(lineno), broken)
            matched = [i for i in items if _item_symbol(i) in hits]
            if matched:
                return matched[:3]
        norm = _normalize_error_text(failure.get("error") or "")
        scored = []
        for item in items:
            sym = _item_symbol(item)
            if sym and _symbol_in_text(sym, norm):
                scored.append((len(sym), item))
        scored.sort(key=lambda pair: -pair[0])
        if scored:
            return [item for _, item in scored[:3]]
        if self.plan.probe_kind == "decision":
            code = [i for i in items
                    if _item_field(i, "source") == "code"]
            if code:
                return code[:3]
        return []

    def windows_for(self, failure, items) -> dict[str, str]:
        """Numbered windows: frames + test imports + item sites (capped)."""
        paths: list[tuple[str, int]] = []
        for frame in (failure.get("frames", []) or [])[:2]:
            path, _, lineno = frame.rpartition(":")
            if lineno.isdigit():
                paths.append((path, int(lineno)))
        if failure.get("kind") == "test" and failure.get("test"):
            broken = frozenset(self.ctx.state.get("symbols") or ())
            paths.extend(test_import_windows(
                self.plan.workdir, failure["test"], self.plan.package,
                self.ctx.state.get("graph"), broken))
        for item in items:
            code_file = _item_field(item, "code_file")
            code_line = _item_field(item, "code_line", 0)
            if code_file and code_line:
                paths.append((code_file, int(code_line)))
        windows = {}
        for path, lineno in paths:
            if path not in windows and len(windows) < 3:
                windows[path] = file_window(self.plan.workdir, path,
                                            lineno)
        return windows

    def usages_for(self, items) -> dict[str, list[str]]:
        """Other package usages per item symbol (graph edges)."""
        graph = self.ctx.state.get("graph")
        usages = {}
        for item in items[:3]:
            sym = _item_symbol(item)
            if sym and sym not in usages:
                usages[sym] = find_usages(graph, sym)
        return usages

    def test_only_ok(self, failure, items) -> bool:
        """True when the failure is confined to test files.

        An unlocated failure (no site) never qualifies: test edits
        without a known test context are ungrounded freelancing.
        """
        site = failure.get("site") or ""
        if not site:
            return False
        if not _is_test_path(site.rpartition(":")[0]):
            return False
        graph = self.ctx.state.get("graph")
        for item in items:
            for use in find_usages(graph, _item_symbol(item)):
                if not _is_test_path(use.rpartition(":")[0]):
                    return False
        return True

    def signature(self, probe, targeted=None) -> tuple:
        """Progress identity: probe site + targeted + suite shape.

        The shape is stable across fresh and stale suite states
        (stale reads as last-known), so only real movement counts
        as progress.
        """
        site = probe["site"] if not probe["ok"] else None
        t_fail = None
        if targeted and not targeted.get("ran_full"):
            t_fail = tuple(sorted(targeted.get("failed") or ()))
        bad = sorted(self.bad_tests())
        files = sorted(self.bad_files())
        return (site, t_fail, len(bad), len(files),
                bad[0] if bad else None, files[0] if files else None)

    # -- rounds --

    def run_round(self, round_no: int, probe, failure) -> dict:
        """One propose/apply/verify round; returns its record."""
        start = time.monotonic()
        round_cmd_ms = self.cmd_ms
        items = self.items_for_failure(failure)
        windows = self.windows_for(failure, items)
        usages = self.usages_for(items)
        test_only = self.test_only_ok(failure, items)
        base_user = build_round_prompt(
            round_no=round_no, max_rounds=self.plan.max_rounds,
            fix_order=_prompt_fix_order(
                self.ctx, failure.get("site")),
            failure_kind=failure["kind"],
            failure_site=failure.get("site"),
            error_line=failure.get("error") or "",
            trace_excerpt=failure.get("trace") or "", items=items,
            windows=windows, usages=usages,
            applied_summaries=list(self.applied_summaries))
        record: dict = {
            "n": round_no,
            "failure": {k: failure.get(k) for k in
                        ("kind", "site", "error", "test", "frames")},
            "items": sorted({_item_symbol(i) for i in items
                             if _item_symbol(i)}),
            "attempts": [], "edits": [], "probe": None,
            "targeted": None, "before": None, "full": None,
            "progress": False, "duration_ms": 0,
        }
        start_site = probe["site"] if not probe["ok"] else None
        applied_any = False
        for attempt in range(2):
            if time.monotonic() - start > ROUND_TIMEOUT_S:
                record["timeout"] = "round budget exceeded"
                break
            attempt_rec = self.propose_and_apply(
                record, base_user, items, test_only, attempt)
            if not attempt_rec["applied"]:
                break
            applied_any = True
            probe = self.run_probe()
            record["probe"] = _scrub_probe(self.ctx, probe)
            if probe["ok"]:
                break
            if probe["site"] != start_site or attempt == 1:
                break
            base_user += (
                "\nThe applied edits did not move the failure "
                f"(still failing at {probe['site']}):\n"
                f"Error: {(probe['error'] or '')[:400]}\n"
                f"{(probe['trace'] or '')[:2000]}\n"
                "Propose corrected edits (prior edits in this round "
                "stay applied).")
            record.setdefault("retried_same_site", True)
        if applied_any:
            self.verify_round(record, failure)
        record["duration_ms"] = (self.cmd_ms - round_cmd_ms
                                 + record.pop("_llm_ms", 0))
        return record

    def propose_and_apply(self, record, base_user, items, test_only,
                          attempt_no) -> dict:
        """Propose (with validation retries) and apply; one attempt."""
        from chainreaction.runs.context import Cancelled, ReplayMiss

        feedback = ""
        attempt_rec: dict = {"attempt": attempt_no, "applied": [],
                             "validation": [], "prompt_hash": None,
                             "rationale": ""}
        for _try in range(VALIDATE_RETRIES + 1):
            self._check_continue()
            user = base_user + feedback
            prompt_hash = fix_prompt_hash(self.ctx, FIX_SYSTEM_PROMPT,
                                          user)
            try:
                response = call_fix_llm(self.ctx, FIX_SYSTEM_PROMPT,
                                        user)
            except TimeoutError:
                attempt_rec["validation"].append(
                    "model call exceeded the round budget")
                record["attempts"].append(attempt_rec)
                return attempt_rec
            except (Cancelled, ReplayMiss):
                raise
            except Exception as exc:  # noqa: BLE001 — graceful, recorded
                # A failed model call (empty reply, rate limit, outage)
                # must degrade the round, never the run: note it and
                # retry with a fresh budget, like a validation error.
                note = f"model_error: {exc.__class__.__name__}: {exc}"
                attempt_rec["validation"].append(note[:300])
                feedback = ("\nThe previous model call failed "
                            f"({str(exc)[:200]}); reply with exactly "
                            "the schema object.")
                continue
            attempt_rec["prompt_hash"] = prompt_hash
            record["_llm_ms"] = record.get("_llm_ms", 0) + self._llm_ms(
                prompt_hash)
            try:
                data = json.loads(response)
            except ValueError:
                attempt_rec["validation"].append(
                    "response is not valid JSON")
                feedback = ("\nPrevious response was not valid JSON; "
                            "reply with exactly the schema object.")
                continue
            edits, error = parse_edits(data)
            if error is not None:
                attempt_rec["validation"].append(error)
                feedback = (f"\nPrevious response rejected: {error}; "
                            "reply with exactly the schema object.")
                continue
            errors = validate_edits(edits, self.plan.workdir,
                                    self.plan.package, test_only)
            attempt_rec["validation"].extend(errors)
            if errors:
                joined = "; ".join(errors)
                feedback = (f"\nPrevious edits rejected: {joined}. "
                            "Propose corrected edits (search must occur "
                            "exactly once; paths repo-relative).")
                continue
            before = _snapshot_files(self.plan.workdir,
                                     [e.path for e in edits])
            touched = apply_edits(self.plan.workdir, edits)
            _clear_pycache(self.plan.workdir)
            attempt_rec["applied"] = touched
            attempt_rec["rationale"] = str(data.get("rationale", ""))[:300]
            attempt_rec["addresses"] = list(data.get("addresses", []))[:8]
            self.touched_all = sorted(set(self.touched_all) | set(touched))
            self.applied_summaries.append(
                f"round {record['n']}: "
                f"{attempt_rec['rationale'][:120]} [{', '.join(touched)}]")
            diff = "".join(
                unified_diff_for(rel, before.get(rel, ""),
                                 _read_work_file(self.plan.workdir, rel))
                for rel in touched)
            if diff:
                self.ctx.save_artifact(f"fixes/round-{record['n']}.diff",
                                       diff)
                attempt_rec["diff_lines"] = len(diff.splitlines())
            self.suite_fresh = False
            break
        record["attempts"].append(attempt_rec)
        record["edits"] = sorted(set(record["edits"])
                                 | set(attempt_rec["applied"]))
        return attempt_rec

    def verify_round(self, record, failure):
        """Targeted (+ full when clean) and before-env verification."""
        probe = record.get("probe") or {}
        if not probe.get("ok", False):
            return
        testmap = self.ctx.state.get("testmap")
        collected = (self.ctx.state.get("scores") or {}).get(
            "testmap_collected") or self.suite_total or sum(
                self.suite_counts.values())
        method, tests = select_targeted(
            testmap, record["edits"], self.plan.target_rel,
            self.plan.package, collected or 0, self.plan.source_root)
        full_instead = not tests
        try:
            if full_instead:
                full = self.run_full_after(f"round {record['n']} "
                                           f"({method})")
                record["targeted"] = {"method": method, "ran_full": True,
                                      "counts": dict(full["counts"])}
                record["full"] = _scrub_suite(self.ctx, full,
                                              self.suite_output)
            else:
                targeted, _out = self.run_suite(
                    tests, self.plan.after_env, 600,
                    f"round {record['n']} targeted ({method})")
                record["targeted"] = {
                    "method": method, "tests": len(tests),
                    "failed": sorted(targeted["failed"]),
                    "error_files": sorted(targeted["error_files"]),
                    "counts": dict(targeted["counts"])}
                if not targeted["failed"] and not targeted["error_files"]:
                    full = self.run_full_after(f"round {record['n']} "
                                               "(targeted clean)")
                    record["full"] = _scrub_suite(self.ctx, full,
                                                  self.suite_output)
        except _SuiteTimeout as exc:
            record["timeout"] = str(exc)
            return
        if self.plan.before_env and record["edits"]:
            record["before"] = self.run_before_targeted(
                record["edits"], collected or 0)
        elif not self.plan.before_env:
            record["before"] = {"ran": False,
                                "reason": "no before env in this run"}

    def run_before_targeted(self, edited_files: list[str],
                              collected: int = 0):
        """Edited-area tests under the old version (must stay green).

        With no covering tests, small suites fall back to the full
        before suite (bounded); large ones report the gap instead of
        burning ten minutes per round.
        """
        testmap = self.ctx.state.get("testmap")
        if testmap is not None and getattr(testmap, "tests", None):
            mods = modules_for_files(edited_files, self.plan.target_rel,
                                     self.plan.package)
            tests = testmap.covering(mods) if mods else []
            method = "coverage"
        else:
            tests = correspondent_tests(self.plan.source_root,
                                        self.plan.package, edited_files)
            method = "file-correspondence"
        if not tests and 0 < collected <= 500:
            method = "full-suite (no covering tests)"
            tests = []
            full_fallback = True
        else:
            full_fallback = False
        if not tests and not full_fallback:
            return {"ran": False,
                    "reason": f"no {method} tests for edited files"}
        try:
            rec, _out = self.run_suite(tests, self.plan.before_env, 600,
                                       "before-env targeted")
        except _SuiteTimeout as exc:
            return {"ran": False, "reason": str(exc)}
        new_breaks = sorted(set(rec["failed"]) - self.baseline_failed)
        return {"ran": True, "method": method,
                "tests": rec["total"] if full_fallback else len(tests),
                "failed": sorted(rec["failed"]),
                "newly_broken": new_breaks,
                "counts": dict(rec["counts"])}

    def seed_from_verify(self):
        """Baselines + red proof from verify (generic) or fresh (preset)."""
        scores = self.ctx.state.get("scores")
        if isinstance(scores, dict) \
                and scores.get("actual_new") is not None \
                and scores.get("baseline_failed") is not None:
            self.baseline_failed = set(scores.get("baseline_failed")
                                       or [])
            self.baseline_measured = True
            self.red_failed = set(scores.get("actual_new") or [])
            self.red_files = set()
            self.red_provenance = "verify scores (reused)"
            self.suite_failed = set(self.baseline_failed
                                    ) | set(self.red_failed)
            self.suite_fresh = False
            return {"baseline": {"ran": True, "reused_from": "verify",
                                 "failed": sorted(self.baseline_failed)},
                    "red": {"reused_from": "verify",
                            "failed": sorted(self.red_failed)}}
        baseline, output = self.measure_baseline()
        if baseline.get("ran") and not self.plan.before_env:
            # No old version: the pristine failures are the red set
            # (the baseline excludes nothing), and the tree state is
            # already fresh — no second identical run.
            self.red_failed = set(baseline["failed"])
            self.red_files = set(baseline["error_files"])
            self.red_provenance = ("pristine tree "
                                   "(no before env)")
            self.suite_failed = set(baseline["failed"])
            self.suite_files = set(baseline["error_files"])
            self.suite_counts = dict(baseline["counts"])
            self.suite_total = baseline["total"] or sum(
                baseline["counts"].values())
            self.suite_output = output
            self.suite_fresh = True
            self.suite_escalated = baseline["escalated"]
            red = {"ran": True, "reused_baseline_run": True,
                   "failed": sorted(self.red_failed),
                   "counts": dict(self.suite_counts)}
            return {"baseline": baseline, "red": red}
        if baseline.get("ran"):
            self.baseline_failed = set(baseline["failed"])
            self.baseline_files = set(baseline["error_files"])
        red = None
        if self.plan.has_suite:
            try:
                rec = self.red_proof()
            except _SuiteTimeout as exc:
                self.ctx.emit("log", {"message": f"fix: {exc}"})
                red = {"ran": False, "reason": str(exc)}
            else:
                red = {"ran": True, "failed": sorted(rec["failed"]),
                       "counts": dict(rec["counts"])}
        return {"baseline": baseline, "red": red or {"ran": False,
                "reason": "no test suite"}}

    def execute(self) -> dict:
        """Baselines -> rounds -> final verdict; returns the fix record."""
        seeded = self.seed_from_verify()
        probe = self.run_probe()
        if probe["ok"] and self.plan.has_suite and not self.suite_fresh \
                and not self.red_provenance:
            # Red proof never measured (its run timed out): the suite
            # state is unknown, so prove it before judging clean.
            try:
                self.run_full_after("initial state")
            except _SuiteTimeout as exc:
                self.ctx.emit("log", {"message": f"fix: {exc}"})
                return self.finalize("suite-timeout", seeded, probe)
        first_failure = self.current_failure(probe) if (
            not probe["ok"] or self.plan.has_suite) else None
        if first_failure is None:
            return self.finalize("skipped-clean", seeded, probe)
        # Round 1 compares against the initial state (not a vacuous
        # first "progress").
        self.prev_signature = self.signature(probe, None)
        try:
            for round_no in range(1, self.plan.max_rounds + 1):
                self._check_continue()
                where = first_failure.get("site") or ""
                self.ctx.emit("log", {"message":
                                     f"fix: round {round_no} "
                                     f"({first_failure['kind']}"
                                     f"{' ' + where if where else ''})"})
                record = self.run_round(round_no, probe, first_failure)
                self.rounds.append(record)
                probe = self.run_probe()
                if self._confirm_full(record, probe):
                    try:
                        self.run_full_after(f"round {round_no} confirm")
                    except _SuiteTimeout as exc:
                        record["timeout"] = str(exc)
                signature = self.signature(probe, record.get("targeted"))
                record["progress"] = signature != self.prev_signature
                self.no_progress = (0 if record["progress"]
                                    else self.no_progress + 1)
                self.prev_signature = signature
                if self.is_green(probe):
                    self.stop_reason = "green"
                    break
                self.ctx.emit("log", {"message":
                                     f"fix: round {round_no} done "
                                     f"(probe={'ok' if probe['ok'] else 'FAILED'}"
                                     f"{' at ' + probe['site'] if probe['site'] and not probe['ok'] else ''}; "
                                     f"{len(self.bad_tests())} bad test(s); "
                                     f"progress={record['progress']})"})
                if self.no_progress >= 2:
                    if _round_failed_on_model(record):
                        self.stop_reason = "llm-error"
                        self.ctx.emit("log", {"message":
                                             "fix: stopping: the model "
                                             "kept failing its calls"})
                    else:
                        self.stop_reason = "no-progress-twice"
                    break
                first_failure = self.current_failure(probe)
                if first_failure is None:
                    self.stop_reason = "green"
                    break
            else:
                self.stop_reason = "max-rounds"
        except _StageTimeout:
            self.stop_reason = "stage-timeout"
            self.ctx.emit("log", {"message":
                                 "fix: stage budget (30 min) exhausted; "
                                 "stopping with a partial result"})
        except _SuiteTimeout as exc:
            self.stop_reason = "suite-timeout"
            self.ctx.emit("log", {"message": f"fix: {exc}"})
        if self.touched_all and self.plan.has_suite \
                and not self.suite_fresh and not self._deadline_hit():
            # Honest final counts need one fresh full suite.
            try:
                self.run_full_after("final verdict")
            except _SuiteTimeout as exc:
                self.ctx.emit("log", {"message": f"fix: {exc}"})
        return self.finalize(self.stop_reason or "max-rounds", seeded,
                             probe)

    def _confirm_full(self, record: dict, probe: dict) -> bool:
        """True when the round earned a full-suite confirmation."""
        if not probe["ok"] or not self.plan.has_suite:
            return False
        if self.suite_fresh:
            return False
        targeted = record.get("targeted") or {}
        if targeted.get("ran_full"):
            return False
        return not targeted.get("failed") and not targeted.get(
            "error_files")

    def is_green(self, probe) -> bool:
        """Green needs a passing probe, a fresh clean suite, a baseline."""
        if not probe["ok"]:
            return False
        if self.plan.has_suite:
            if not self.suite_fresh:
                return False
            if self.bad_tests() or self.bad_files():
                return False
        return self.baseline_measured

    def counts(self) -> dict:
        current = set(self.suite_failed)
        cur_files = set(self.suite_files)
        if not self.suite_fresh and self.red_provenance.startswith(
                "verify"):
            current = set(self.baseline_failed) | set(self.red_failed)
            cur_files = set(self.baseline_files) | set(self.red_files)
        # Error-to-failure transitions stay red: a test failing now in
        # a file that errored in the red proof was decision-caused
        # then (it errored) and is still failing now — not newly
        # broken. (Without a testmap the red proof cannot attribute
        # error files to test ids.) Red error entries can be
        # directories (a collapsed collection reports the root arg),
        # so anything under them counts as red too.
        red_error_files = {f.replace("\\", "/")
                           for f in self.red_files}
        base_error_files = {f.replace("\\", "/")
                            for f in self.baseline_files}
        red_via_files = {
            t for t in current - self.baseline_failed
            if _under_any(t.split("::")[0], red_error_files)
            and not _under_any(t.split("::")[0], base_error_files)}
        fixed = sorted(set(self.red_failed) - current)
        still = sorted((set(self.red_failed) & current) | red_via_files)
        newly = sorted(current - self.baseline_failed
                       - set(self.red_failed) - red_via_files)
        return {"baseline_failures": len(self.baseline_failed),
                "baseline_measured": self.baseline_measured,
                "suite_fresh": self.suite_fresh,
                "fixed": fixed, "still_failing": still,
                "newly_broken": newly,
                "error_files": {
                    "baseline": sorted(self.baseline_files),
                    "fixed": sorted(set(self.red_files)
                                    - self.baseline_files - cur_files),
                    "still_failing": sorted(
                        {f for f in cur_files
                         if _under_any(f, red_error_files)}),
                    "newly_broken": sorted(
                        {f for f in cur_files
                         if not _under_any(f, base_error_files)
                         and not _under_any(f, red_error_files)})}}

    def upstream_comparison(self):
        """Touched-vs-PR overlap + PR tests on the patched tree."""
        scen = self.ctx.state["scenario"]
        pr_files = list(getattr(scen, "pr_files", None) or [])
        if not pr_files:
            return {"ran": False, "reason": "no ground-truth PR files"}
        if not self.touched_all:
            return {"ran": False, "reason": "no edits applied"}
        touched = set(self.touched_all)
        wanted = set(pr_files)
        pr_tests = sorted(f for f in pr_files if _is_test_path(f))
        if not pr_tests:
            pr_tests = correspondent_tests(self.plan.source_root,
                                           self.plan.package, pr_files)
            method = ("file-correspondence (PR has no test files)"
                      if pr_tests else "none found")
        else:
            method = "PR test files"
        result: dict = {
            "ran": True, "pr_url": getattr(scen, "pr_url", ""),
            "overlap": sorted(touched & wanted),
            "missed": sorted(wanted - touched),
            "extra": sorted(touched - wanted),
            "pr_tests": {"method": method, "selection": pr_tests},
        }
        if pr_tests and self.plan.has_suite:
            try:
                rec, _out = self.run_suite(
                    pr_tests, self.plan.after_env, 600,
                    "upstream PR tests")
            except _SuiteTimeout as exc:
                result["pr_tests"]["error"] = str(exc)
            else:
                result["pr_tests"]["failed"] = sorted(rec["failed"])
                result["pr_tests"]["counts"] = dict(rec["counts"])
        return result

    def finalize(self, stop_reason: str, seeded: dict, probe) -> dict:
        """Guards + patch + verdict; saves fix.json and final.patch."""
        if self.touched_all:
            added, removed = patch_body_lines(
                self.plan.source_root, self.plan.workdir,
                self.touched_all)
            violations = guard_checks(self.touched_all, added, removed)
        else:
            violations = []
        patch = workdir_diff(self.plan.source_root, self.plan.workdir,
                             self.touched_all) if self.touched_all else ""
        patch_name = None
        if patch:
            self.ctx.save_artifact("fixes/final.patch", patch)
            patch_name = "fixes/final.patch"
        counts = self.counts()
        status = self.verdict(stop_reason, violations, counts)
        upstream = self.upstream_comparison() if (
            stop_reason not in ("skipped-clean",) and self.touched_all
            and getattr(self.ctx.state["scenario"], "pr_files", None)
        ) else {"ran": False,
                "reason": ("clean tree" if stop_reason == "skipped-clean"
                           else ("no ground-truth PR files"
                                 if not getattr(
                                     self.ctx.state["scenario"],
                                     "pr_files", None)
                                 else "no edits applied"))}
        record = {
            "status": status, "final_status": status,
            "stop_reason": stop_reason,
            "seeded": seeded,
            "probe_start": _scrub_probe(self.ctx, probe),
            "rounds": self.rounds,
            "counts": counts,
            "guards": {"ok": not violations, "violations": violations},
            "patch": patch_name,
            "touched": list(self.touched_all),
            "upstream": upstream,
            "total_cmd_ms": self.cmd_ms,
        }
        self.ctx.save_artifact("fix.json", record)
        self.ctx.emit("log", {"message":
                             f"fix: {status} ({stop_reason}; "
                             f"{len(counts['fixed'])} fixed, "
                             f"{len(counts['still_failing'])} still "
                             "failing, "
                             f"{len(counts['newly_broken'])} newly "
                             "broken)"})
        return record

    def verdict(self, stop_reason: str, violations: list[str],
                counts: dict) -> str:
        """green | partial | failed | unverified | skipped."""
        if stop_reason.startswith("skipped"):
            return "skipped"
        if violations:
            return "unverified"
        if stop_reason == "green":
            return "green"
        if counts["fixed"] or self.touched_all:
            return "partial"
        return "failed"


def _under_any(path: str, roots: set[str]) -> bool:
    """True when a path equals or sits under any root (posix-compared)."""
    path = str(path).replace("\\", "/")
    return any(path == root or path.startswith(root + "/")
               for root in roots)


def _round_failed_on_model(record: dict) -> bool:
    """True when a round applied nothing and only the model errored."""
    if record.get("edits"):
        return False
    notes = [note for attempt in record.get("attempts", [])
             for note in attempt.get("validation", [])]
    return bool(notes) and all("model_error:" in note or "budget" in note
                               for note in notes)


def _pin_fixwork_scrub(ctx, workdir: Path) -> None:
    """Map this run's workdir to <fixwork> on its scrubber (both modes).

    Recordings stay machine-independent: live maps fixwork/<live-id>,
    replay maps fixwork/<replay-id>, and the keys agree.
    """
    import dataclasses

    scrub = ctx.scrubber
    ctx.scrubber = dataclasses.replace(
        scrub, replacements=((str(workdir), "<fixwork>"),
                             *scrub.replacements))


def skipped_record(stop_reason: str, detail: str = "") -> dict:
    """Uniform skip record (live skips and their replays agree)."""
    return {
        "status": "skipped", "final_status": "skipped",
        "stop_reason": stop_reason, "detail": detail,
        "seeded": {"baseline": {"ran": False},
                   "red": {"ran": False}},
        "probe_start": {"ok": None, "site": None, "error": "",
                        "trace": "", "modules": []},
        "rounds": [],
        "counts": {"baseline_failures": 0, "baseline_measured": False,
                   "suite_fresh": False, "fixed": [],
                   "still_failing": [], "newly_broken": [],
                   "error_files": {"baseline": [], "fixed": [],
                                   "still_failing": [],
                                   "newly_broken": []}},
        "guards": {"ok": True, "violations": []},
        "patch": None, "touched": [],
        "upstream": {"ran": False, "reason": stop_reason},
        "total_cmd_ms": 0,
    }


def run_loop(ctx) -> dict:
    """Execute the fix loop for a run; returns the fix record.

    Copies the source to fixwork/<run_id>/ (both modes: replay
    applies the recorded edits to its own copy), runs rounds, saves
    per-round diffs, final.patch, and fix.json.
    """
    from chainreaction.runs.scrub import ROOT

    plan = _resolve_plan(ctx)
    prune_fixwork(ROOT)
    copy_source_to_fixwork(plan.source_root, plan.workdir)
    _pin_fixwork_scrub(ctx, plan.workdir)
    ctx.emit("log", {"message":
                     f"fix: work copy at "
                     f"{ctx.scrubber.text(str(plan.workdir))} "
                     f"(source untouched)"})
    loop = _Loop(ctx, plan)
    try:
        return loop.execute()
    except _StageTimeout:
        loop.stop_reason = "stage-timeout"
        return loop.finalize("stage-timeout", {"baseline": {},
                                              "red": {}},
                             {"ok": False, "site": None, "error": "",
                              "trace": "", "modules": []})


def main() -> int:  # `python -m chainreaction.fixloop --help`
    print("fixloop module: M5 search/replace rounds via run_loop(ctx)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
