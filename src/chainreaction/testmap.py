"""Coverage-based test mapping: which target tests execute which modules.

Built by running the target suite under coverage with per-test dynamic
contexts, in the BEFORE env (where the suite passes). Consumers select
tests covering impacted modules (fix loop) or score predicted-vs-actual
failing tests.
"""

import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from coverage import Coverage

from chainreaction.fixloop import COUNT_RE
from chainreaction.graph.repo import relative_posix


@dataclass
class TestMap:
    target: str
    package: str
    built_with: str  # e.g. "numpy1 (numpy 1.26.4)"
    tests: dict[str, list[str]] = field(default_factory=dict)
    collected: int = 0  # tests collected at build (mapped <= collected)
    # Per-test executed break-candidate lines ("relpath:line"), repo-relative
    # posix matching graph edge file:line. Empty for old recordings (file
    # maps only) — prediction falls back to module covering then.
    line_hits: dict[str, list[str]] = field(default_factory=dict)

    def covering(self, modules: set[str]) -> list[str]:
        """Sorted ids of tests executing any of the given modules."""
        want = set(modules)
        return sorted(t for t, ms in self.tests.items() if want & set(ms))

    def hitting(self, lines: set[str]) -> list[str]:
        """Sorted ids of tests executing any of the given file:line hits."""
        want = set(lines)
        return sorted(t for t, hs in self.line_hits.items()
                      if want & set(hs))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2),
                              encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> "TestMap":
        p = Path(path)
        if not p.is_file():
            gzipped = p.with_name(p.name + ".gz")
            if gzipped.is_file():
                import gzip

                d = json.loads(
                    gzip.decompress(gzipped.read_bytes()).decode("utf-8"))
                return TestMap(d["target"], d["package"], d["built_with"],
                               d["tests"],
                               d.get("collected", len(d["tests"])),
                               d.get("line_hits", {}))
        d = json.loads(p.read_text(encoding="utf-8"))
        return TestMap(d["target"], d["package"], d["built_with"], d["tests"],
                       d.get("collected", len(d["tests"])),
                       d.get("line_hits", {}))


def _split_foreign(path_text: str) -> list[str]:
    """Path segments tolerant of foreign separators and drives.

    Recordings carry the live machine's absolute paths (Windows
    backslashes on a Linux replay, doubled by JSON escaping); splitting
    on both separators and dropping empties/drives recovers the tail.
    """
    text = str(path_text).replace("\\", "/")
    if "<ws>" in text:
        text = text.split("<ws>", 1)[1]
    segs = [s for s in text.split("/") if s not in ("", ".")]
    if segs and len(segs[0]) == 2 and segs[0][1] == ":":
        segs = segs[1:]
    return segs


def _foreign_package_parts(path_text: str, package: str) -> list[str] | None:
    """Package-relative parts from a foreign absolute path, else None.

    Finds ``/src/<package>/...`` or ``/<package>/...`` in the tail so a
    Windows recording replays on Linux (and vice versa).
    """
    segs = _split_foreign(path_text)
    for i, seg in enumerate(segs):
        if seg != package:
            continue
        # src/<package>/... (layout marker) or a top-level <package>/...
        # hit; the file suffix check happens in the caller.
        tail = segs[i:]
        if tail and tail[-1].endswith(".py"):
            # Skip hits inside site-packages (installed copy, not repo).
            if "site-packages" in segs[:i]:
                continue
            return tail
    return None


def file_to_module(path: str | Path, target: str | Path, package: str
                   ) -> str | None:
    """Map a measured file to its dotted module (None when out of package).

    Test files map to None: every test covers its own file, which would
    make covering() trivially select everything. conftest.py stays
    mapped (only fixture users cover it). src/ layouts are unwrapped.
    Foreign absolute paths (a Windows recording replayed on Linux)
    resolve by package-tail match, never by relpath.
    """
    text = str(path)
    # Placeholder and foreign forms first: relpath cannot span machines.
    if "<ws>" in text or "\\" in text:
        tail = _foreign_package_parts(text, package)
        if tail is None:
            return None
        parts: list[str] = list(tail)
    else:
        try:
            rel = Path(os.path.relpath(text, str(target)))
        except ValueError:
            return None
        # A relpath that escapes the target is either out of package or
        # a foreign absolute in disguise — try the tail match.
        if str(rel).startswith(".."):
            tail = _foreign_package_parts(text, package)
            if tail is None:
                return None
            parts = list(tail)
        else:
            parts = list(rel.parts)
            if parts and parts[0] == "src":
                parts = parts[1:]
    if not parts or parts[0] != package:
        return None
    if not parts[-1].endswith(".py"):
        return None
    if "tests" in parts or Path(parts[-1]).name.startswith("test_"):
        return None
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = Path(parts[-1]).stem
    return ".".join(parts)


def _strip_target_prefix(tid: str, target: str | Path) -> str:
    # pytest anchors node ids at the discovered rootdir, which can sit
    # above the target (this repo's own pyproject is an ancestor) —
    # relativize so ids resolve from the target dir.
    prefix = str(target).replace("\\", "/").rstrip("/") + "/"
    return tid[len(prefix):] if tid.startswith(prefix) else tid


def parse_coverage_full(data_file: str | Path, target: str | Path,
                        package: str
                        ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Invert coverage contexts: (test->modules, test->file:line hits)."""
    cov = Coverage(data_file=str(data_file))
    cov.load()
    data = cov.get_data()
    mods: dict[str, set[str]] = {}
    lines: dict[str, set[str]] = {}
    for fname in data.measured_files():
        mod = file_to_module(fname, target, package)
        rel = file_to_rel(fname, target, package)
        if mod is None and rel is None:
            continue
        for lineno, names in data.contexts_by_lineno(fname).items():
            for raw in names:
                tid = raw.split("|")[0]
                if not tid:  # "" is collection-time, unattributed to a test
                    continue
                tid = _strip_target_prefix(tid, target)
                if mod is not None:
                    mods.setdefault(tid, set()).add(mod)
                if rel is not None:
                    lines.setdefault(tid, set()).add(f"{rel}:{lineno}")
    return ({t: sorted(ms) for t, ms in mods.items()},
            {t: sorted(hs) for t, hs in lines.items()})


def parse_coverage(data_file: str | Path, target: str | Path, package: str
                   ) -> dict[str, list[str]]:
    """Invert coverage contexts: test node id -> sorted executed modules."""
    mods, _ = parse_coverage_full(data_file, target, package)
    return mods


def parse_failures(output: str, target: str | Path
                   ) -> tuple[set[str], set[str]]:
    """FAILED test ids and ERROR files from a -rfE short summary."""
    failed, errfiles = set(), set()
    for line in output.splitlines():
        if line.startswith("FAILED "):
            entry = _strip_target_prefix(line[7:].split(" - ", 1)[0].strip(),
                                         target)
            failed.add(entry)
        elif line.startswith("ERROR "):
            entry = _strip_target_prefix(line[6:].split(" - ", 1)[0].strip(),
                                         target)
            (failed if "::" in entry else errfiles).add(entry)
    return failed, errfiles


def parse_failure_messages(output: str, target: str | Path
                           ) -> dict[str, str]:
    """Test id -> short-summary error message ("" when the line has none).

    Reads FAILED and ERROR-with-test lines from a -rfE summary. Messages
    truncate at the terminal width, so suites run with COLUMNS=200 to
    keep them useful; grouping still tolerates truncation.
    """
    out: dict[str, str] = {}
    for line in (output or "").splitlines():
        if line.startswith("FAILED "):
            rest = line[7:]
        elif line.startswith("ERROR ") and "::" in line:
            rest = line[6:]
        else:
            continue
        tid, sep, msg = rest.partition(" - ")
        tid = _strip_target_prefix(tid.strip(), target)
        if not tid:
            continue
        out[tid] = msg.strip() if sep else ""
    return out


def parse_file_errors(output: str, target: str | Path) -> dict[str, str]:
    """ERROR file -> collection message for file-level ERROR lines."""
    out: dict[str, str] = {}
    for line in (output or "").splitlines():
        if not line.startswith("ERROR ") or "::" in line:
            continue
        path, sep, msg = line[6:].partition(" - ")
        path = _strip_target_prefix(path.strip(), target)
        if path:
            out[path] = msg.strip() if sep else ""
    return out


def _normalize_failure_message(msg: str) -> str:
    """Group key for a summary message: collapsed, capped, or "other"."""
    text = " ".join(str(msg or "").split())
    if not text:
        return "other"
    return text[:200]


def _merge_truncations(buckets: dict[str, list[str]]) -> dict[str, list[str]]:
    """Fold truncation fragments into their longest observed message.

    pytest cuts summary lines at the terminal width, so one error
    fragments into many prefix variants (...., .. D...). A message
    ending in dots that is a strict prefix (after dot-strip) of a
    longer observed message is its truncation: fold it. Messages
    without truncation evidence never merge, and "other" never merges.
    """
    merged: dict[str, list[str]] = {}
    for key in sorted(buckets, key=len, reverse=True):
        stem = key.rstrip(".").rstrip()
        target = None
        if key != "other" and key.rstrip().endswith(".") and stem:
            for kept in merged:
                if (kept != "other" and len(kept) > len(key)
                        and kept.startswith(stem)):
                    target = kept
                    break
        if target is None:
            merged.setdefault(key, []).extend(buckets[key])
        else:
            merged[target].extend(buckets[key])
    return merged


def _file_fallback_message(tid: str, file_errors: dict[str, str]) -> str:
    """ERROR message of tid's file ("", when its file did not ERROR)."""
    norm = tid.replace("\\", "/")
    for path, msg in file_errors.items():
        if norm == path.replace("\\", "/") or norm.startswith(
                path.replace("\\", "/") + "::"):
            return msg
    return ""


def failure_groups(new_failed: set[str], after_output: str,
                   target: str | Path) -> list[dict]:
    """New failures grouped by error message, largest first.

    Each group is {message, count, examples}: message is the longest
    observed -rfE message in the bucket (truncation fragments fold
    together), examples are up to three sorted test ids. Tests that
    never ran (their file ERRORed at collection) inherit the file's
    ERROR message; only truly messageless failures read "other". Tells
    the user what the decision actually caused versus collateral
    breakage.
    """
    messages = parse_failure_messages(after_output, target)
    file_errors = parse_file_errors(after_output, target)
    buckets: dict[str, list[str]] = {}
    for tid in set(new_failed or ()):
        if tid in messages:
            msg = messages[tid]
        else:
            msg = _file_fallback_message(tid, file_errors)
        buckets.setdefault(_normalize_failure_message(msg), []).append(tid)
    groups = [{"message": msg, "count": len(tids),
               "examples": sorted(tids)[:3]}
              for msg, tids in _merge_truncations(buckets).items()]
    groups.sort(key=lambda g: (-g["count"], g["message"]))
    return groups


@dataclass
class SuiteResult:
    counts: dict = field(default_factory=dict)  # passed/failed/errors/...
    failed: set[str] = field(default_factory=set)
    error_files: set[str] = field(default_factory=set)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def parse_suite_counts(output: str) -> dict:
    """Pass/fail/error counts from pytest's summary line (last wins)."""
    counts: dict[str, int] = {}
    for num, word in COUNT_RE.findall(output or ""):
        key = "errors" if word.startswith("error") else word
        counts[key] = int(num)
    return counts


def parse_suite(output: str, target: str | Path,
               testmap: "TestMap | None" = None) -> SuiteResult:
    """Counts + failing ids from a -rfE run; ERROR files expand via testmap.

    Without a testmap, ERROR files are reported but unattributed.
    """
    counts = parse_suite_counts(output)
    failed, errfiles = parse_failures(output, target)
    actual = set(failed)
    if testmap is not None:
        for f in errfiles:
            prefix = f.replace("\\", "/") + "::"
            actual |= {t for t in testmap.tests
                       if t.replace("\\", "/").startswith(prefix)}
        actual &= set(testmap.tests)
    return SuiteResult(counts, actual, errfiles)


def failing_tests(python: str, target: str | Path, tests: str,
                  testmap: TestMap, timeout: int = 1800,
                  run=None) -> set[str]:
    """Real failing test ids: FAILED plus tests in ERROR files.

    Runs with --noconftest: the target conftest itself imports the broken
    chain, which would collapse collection before any test runs.
    run(cmd, cwd) injects the subprocess layer (recorded runs pass a
    ctx.run_cmd adapter); it must return an object with .stdout.
    """
    cmd = [python, "-m", "pytest", tests, "-q", "--tb=no",
           "-p", "no:cacheprovider", "--noconftest",
           "--continue-on-collection-errors", "-rfE"]
    if run is None:
        proc = subprocess.run(
            cmd, cwd=str(Path(target).resolve()), capture_output=True,
            text=True, timeout=timeout, check=False)
        output = proc.stdout
    else:
        output = run(cmd, str(target)).stdout
    return parse_suite(output, target, testmap).failed


def _enclosing_module(g, node: str) -> str | None:
    """Nearest module ancestor of a node (itself when a module)."""
    parts = node.split(".")
    for i in range(len(parts), 0, -1):
        cand = ".".join(parts[:i])
        if cand in g.nodes and g.nodes[cand].get("kind") == "module":
            return cand
    return None


def break_lines(g, broken: set[str], target: str | Path
                ) -> set[str]:
    """Repo-relative file:line hits using a removed symbol.

    Every USES_EXTERNAL edge whose target is (or nests under) a broken
    symbol is a call-time failure site; tests executing one are predicted
    to fail. Files normalize against target so graph and coverage agree.
    """
    from chainreaction.propagate import matches_symbol

    out: set[str] = set()
    for _u, v, d in g.edges(data=True):
        if d.get("kind") != "USES_EXTERNAL":
            continue
        if matches_symbol(v, set(broken or ())) is None:
            continue
        try:
            rel = relative_posix(d.get("file", ""), target)
        except Exception:
            continue
        line = d.get("line")
        if rel and line:
            out.add(f"{rel}:{int(line)}")
    return out


def test_call_chain(testmap: TestMap, g, actual_new: set[str],
                    broken: set[str], target: str | Path
                    ) -> tuple[str, str, str, str, int] | None:
    """One failing-test call chain: (test, func, symbol, file, line).

    Picks the first sorted new failure whose line hits reach a removed
    symbol, like the SponsorFit call path. None without line data or
    when no failure hits a break site.
    """
    from chainreaction.propagate import matches_symbol

    hits = getattr(testmap, "line_hits", None) or {}
    if not hits or not actual_new or not broken:
        return None
    sites: dict[str, tuple[str, str, str, int]] = {}
    for u, v, d in g.edges(data=True):
        if d.get("kind") != "USES_EXTERNAL":
            continue
        sym = matches_symbol(v, set(broken))
        if sym is None:
            continue
        try:
            rel = relative_posix(d.get("file", ""), target)
        except Exception:
            continue
        line = d.get("line")
        if not rel or not line:
            continue
        key = f"{rel}:{int(line)}"
        cand = (str(u), str(v), rel, int(line))
        if key not in sites or cand < sites[key]:
            sites[key] = cand
    for tid in sorted(set(actual_new)):
        for hit in sorted(hits.get(tid, ())):
            if hit in sites:
                user, sym_used, rel, line = sites[hit]
                return (tid, user, sym_used, rel, line)
    return None


def predicted_failing(testmap: TestMap, g, hard: set[str],
                      target: str | Path, at_risk=None,
                      broken: set[str] | None = None) -> set[str]:
    """Tests hitting removed-symbol lines, plus tests importing HARD files.

    Call-time failures are line-precise: a test is predicted to fail when
    its coverage hits a line that uses a removed symbol. Without line
    data (old recordings) or without a broken set, fall back to covering
    HARD and AT_RISK modules. The import-file expansion stays HARD-only:
    importing a module whose breaks are all in-function does not fail at
    import. Imports run at collection (unattributed to any test context),
    so a test file importing a HARD module fails even when its covered
    code is healthy.
    """
    if broken is not None and getattr(testmap, "line_hits", None):
        pred = set(testmap.hitting(break_lines(g, set(broken), target)))
    else:
        modules = set(hard)
        for node in at_risk or ():
            mod = _enclosing_module(g, node)
            if mod is not None:
                modules.add(mod)
        pred = set(testmap.covering(modules))
    for u, v, d in g.edges(data=True):
        if d.get("kind") != "IMPORTS" or v not in hard:
            continue
        udata = g.nodes[u]
        if udata.get("kind") != "module":
            continue
        rel = relative_posix(udata["file"], target)
        pred |= {t for t in testmap.tests
                 if t.replace("\\", "/").startswith(rel + "::")}
    return pred


def build_map(python: str, target: str | Path, package: str,
              tests_rel: str = ".", timeout: int = 1800,
              built_with: str = "") -> TestMap:
    """Run the suite under coverage; return the test->modules map.

    The suite may fail — the map covers whatever ran. Raises RuntimeError
    when coverage produces no data at all.
    """
    target_p = Path(target).resolve()
    data_file = target_p / ".coverage-chainreaction"
    if data_file.exists():
        data_file.unlink()
    env = dict(os.environ)
    env["COVERAGE_FILE"] = str(data_file)
    env["COLUMNS"] = "200"
    proc = subprocess.run(
        [python, "-m", "pytest", tests_rel, "-q", "--tb=no",
         "-p", "no:cacheprovider", "--continue-on-collection-errors",
         f"--cov={package}", "--cov-context=test", "--cov-report="],
        cwd=str(target_p), capture_output=True, text=True,
        timeout=timeout, env=env, check=False,
    )
    try:
        if not data_file.exists():
            raise RuntimeError(f"coverage produced no data in {target}")
        tests, lines = parse_coverage_full(data_file, target, package)
    finally:
        for stale in target_p.glob(".coverage-chainreaction*"):
            stale.unlink(missing_ok=True)
    collected = sum(int(n) for n, _ in COUNT_RE.findall(proc.stdout))
    return TestMap(target=str(target), package=package,
                   built_with=built_with, tests=tests, collected=collected,
                   line_hits=lines)


COVERAGE_DATA_NAME = ".coverage-chainreaction"

# Coverage contexts dumped to stdout (recorded): full file-level tids plus
# per-line tids for focus files only:
# {"files": {file: [test ids]}, "lines": {file: {line: [test ids]}}}.
# Per-line detail for every file explodes (anndata: ~200MB — most lines
# are covered by thousands of tests), so callers pass the break-line
# files as argv[2] (JSON list of repo-relative posix paths, matched
# against relpath-to-cwd). The .coverage file itself never leaves the
# live machine, so replay rebuilds the identical map from the recording.
# Old recordings carry {file: [test ids]} or {file: {line: [ids]}};
# parse_dump_full reads all three shapes.
_DUMP_CODE = (
    "import json, os, sys\n"
    "from coverage import Coverage\n"
    "cov = Coverage(data_file=sys.argv[1])\n"
    "cov.load()\n"
    "data = cov.get_data()\n"
    "focus = set(json.loads(sys.argv[2])) if len(sys.argv) > 2 else set()\n"
    "files = {}\n"
    "lines = {}\n"
    "for fname in data.measured_files():\n"
    "    by_line = data.contexts_by_lineno(fname)\n"
    "    tids = set()\n"
    "    for names in by_line.values():\n"
    "        for raw in names:\n"
    "            tid = raw.split('|')[0]\n"
    "            if tid:\n"
    "                tids.add(tid)\n"
    "    if not tids:\n"
    "        continue\n"
    "    files[fname] = sorted(tids)\n"
    "    try:\n"
    "        rel = os.path.relpath(fname).replace(os.sep, '/')\n"
    "    except ValueError:\n"
    "        continue\n"
    "    if rel not in focus:\n"
    "        continue\n"
    "    per_line = {}\n"
    "    for lineno, names in by_line.items():\n"
    "        ltids = set()\n"
    "        for raw in names:\n"
    "            tid = raw.split('|')[0]\n"
    "            if tid:\n"
    "                ltids.add(tid)\n"
    "        if ltids:\n"
    "            per_line[str(lineno)] = sorted(ltids)\n"
    "    if per_line:\n"
    "        lines[fname] = per_line\n"
    "print(json.dumps({'files': files, 'lines': lines}))\n"
)

# File-level dump (pre-line-hits recordings): replay falls back to this
# command when the recording predates per-line dumps.
_DUMP_CODE_V1 = (
    "import json, sys\n"
    "from coverage import Coverage\n"
    "cov = Coverage(data_file=sys.argv[1])\n"
    "cov.load()\n"
    "data = cov.get_data()\n"
    "out = {}\n"
    "for fname in data.measured_files():\n"
    "    tids = set()\n"
    "    for names in data.contexts_by_lineno(fname).values():\n"
    "        for raw in names:\n"
    "            tid = raw.split('|')[0]\n"
    "            if tid:\n"
    "                tids.add(tid)\n"
    "    if tids:\n"
    "        out[fname] = sorted(tids)\n"
    "print(json.dumps(out))\n"
)


def _tail_rel(path_text: str, package: str) -> str | None:
    """Repo-relative path by package-tail match (foreign absolutes)."""
    segs = _split_foreign(path_text)
    for i, seg in enumerate(segs):
        if seg != package:
            continue
        if "site-packages" in segs[:i]:
            continue
        tail = segs[i:]
        if not tail or not tail[-1].endswith(".py"):
            continue
        # Preserve a src/ layout marker when present.
        if i > 0 and segs[i - 1] == "src":
            tail = ["src", *tail]
        return "/".join(tail)
    return None


def file_to_rel(path_text: str | Path, target: str | Path, package: str
                ) -> str | None:
    """Repo-relative posix path for a measured file, else None.

    Matches graph edge file form (``src/pkg/x.py`` or ``pkg/x.py``) on
    every machine: local paths relativize, foreign absolutes and <ws>
    forms match by package tail.
    """
    text = str(path_text)
    if "<ws>" in text or "\\" in text:
        return _tail_rel(text, package)
    try:
        rel = relative_posix(text, target)
    except Exception:
        return _tail_rel(text, package)
    if rel.startswith(".."):
        # A relpath escaping the target is a foreign absolute in
        # disguise (forward-slashed drive path) — match by tail.
        return _tail_rel(text, package)
    if not rel.endswith(".py"):
        return None
    head = rel.split("/")[0]
    if head not in (package, "src"):
        if len(head) == 2 and head[1] == ":":
            # Forward-slashed drive path on a POSIX box (E:/...): not
            # absolute there, so relpath passed it through — match by
            # package tail instead of dropping the line hit.
            return _tail_rel(text, package)
        return None
    return rel


def _strip_tid(tid: str, strip_prefix: str) -> str:
    if strip_prefix and tid.startswith(strip_prefix):
        return tid[len(strip_prefix):]
    return tid


def parse_dump_full(text: str, target: str | Path, package: str,
                    strip_prefix: str = ""
                    ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Invert a coverage dump: (test->modules, test->file:line hits).

    Reads focused dumps ({"files": ..., "lines": ...}), per-line dumps
    ({file: {line: [ids]}}), and old file dumps ({file: [ids]}); dumps
    without per-line detail yield empty line hits.
    """
    try:
        raw = json.loads(text)
    except ValueError:
        return {}, {}
    if not isinstance(raw, dict):
        return {}, {}
    if isinstance(raw.get("files"), dict) and isinstance(raw.get("lines"),
                                                         dict):
        return (_parse_focused_dump(raw, target, package, strip_prefix),
                _parse_focused_lines(raw, target, package, strip_prefix))
    mods: dict[str, set[str]] = {}
    lines: dict[str, set[str]] = {}
    for fname, payload in raw.items():
        mod = file_to_module(fname, target, package)
        rel = file_to_rel(fname, target, package)
        if isinstance(payload, dict):
            if mod is None and rel is None:
                continue
            for lineno, tids in payload.items():
                try:
                    line = int(lineno)
                except (TypeError, ValueError):
                    continue
                for tid in tids or []:
                    tid = _strip_tid(tid, strip_prefix)
                    if not tid:
                        continue
                    if mod is not None:
                        mods.setdefault(tid, set()).add(mod)
                    if rel is not None:
                        lines.setdefault(tid, set()).add(f"{rel}:{line}")
        else:
            if mod is None:
                continue
            for tid in payload or []:
                tid = _strip_tid(tid, strip_prefix)
                if not tid:
                    continue
                mods.setdefault(tid, set()).add(mod)
    return ({t: sorted(ms) for t, ms in mods.items()},
            {t: sorted(hs) for t, hs in lines.items()})


def _parse_focused_dump(raw: dict, target: str | Path, package: str,
                        strip_prefix: str) -> dict[str, list[str]]:
    """File-level half of a focused dump: test -> sorted modules."""
    out: dict[str, set[str]] = {}
    files = raw.get("files")
    if not isinstance(files, dict):
        return {}
    for fname, tids in files.items():
        mod = file_to_module(fname, target, package)
        if mod is None:
            continue
        for tid in tids or []:
            tid = _strip_tid(tid, strip_prefix)
            if tid:
                out.setdefault(tid, set()).add(mod)
    return {t: sorted(ms) for t, ms in out.items()}


def _parse_focused_lines(raw: dict, target: str | Path, package: str,
                         strip_prefix: str) -> dict[str, list[str]]:
    """Per-line half of a focused dump: test -> sorted file:line hits."""
    out: dict[str, set[str]] = {}
    lines = raw.get("lines")
    if not isinstance(lines, dict):
        return {}
    for fname, per_line in lines.items():
        rel = file_to_rel(fname, target, package)
        if rel is None or not isinstance(per_line, dict):
            continue
        for lineno, tids in per_line.items():
            try:
                line = int(lineno)
            except (TypeError, ValueError):
                continue
            for tid in tids or []:
                tid = _strip_tid(tid, strip_prefix)
                if tid:
                    out.setdefault(tid, set()).add(f"{rel}:{line}")
    return {t: sorted(hs) for t, hs in out.items()}


def parse_dump(text: str, target: str | Path, package: str,
               strip_prefix: str = "") -> dict[str, list[str]]:
    """Invert a coverage dump: test node id -> sorted executed modules."""
    mods, _ = parse_dump_full(text, target, package, strip_prefix)
    return mods


def build_map_with(run, python: str, target: str | Path, package: str,
                   tests_rel: str = ".", timeout: int = 1800,
                   built_with: str = "",
                   data_name: str = COVERAGE_DATA_NAME,
                   id_prefix: str = "",
                   focus_files: set[str] | frozenset[str] | None = None
                   ) -> tuple[TestMap, str]:
    """Coverage test map over an injected runner; returns (map, output).

    run(cmd, cwd, extra_env) executes (recorded runs pass a ctx.run_cmd
    adapter: outputs come back unscrubbed, the dump is recorded, and
    replay rebuilds the map from the recording). The suite output is
    returned so the caller can parse baseline counts from the same run.
    focus_files are repo-relative posix paths getting per-line detail;
    every other measured file contributes file-level tids only (full
    per-line detail explodes to ~200MB on real suites). Raises
    RuntimeError when coverage produces no mappable data.
    """
    cwd = str(target)
    data_path = Path(cwd) / data_name
    data_path.unlink(missing_ok=True)
    proc = run(
        [python, "-m", "pytest", tests_rel, "-q", "--tb=no",
         "-p", "no:cacheprovider", "--continue-on-collection-errors",
         "-rfE", f"--cov={package}", "--cov-context=test",
         "--cov-report="],
        cwd, extra_env={"COVERAGE_FILE": data_name, "COLUMNS": "200"})
    if getattr(proc, "timed_out", False):
        raise RuntimeError(f"test suite timed out in {target}")
    output = proc.stdout
    focus_arg = json.dumps(sorted(focus_files or ()))
    try:
        try:
            dumped = run([python, "-c", _DUMP_CODE, data_name, focus_arg],
                         cwd)
        except Exception as exc:
            # Replay of a pre-line-hits recording: the recorded dump
            # command carries the old file-level code.
            from chainreaction.runs.context import ReplayMiss

            if not isinstance(exc, ReplayMiss):
                raise
            dumped = run([python, "-c", _DUMP_CODE_V1, data_name], cwd)
        if dumped.returncode != 0:
            raise RuntimeError(
                f"coverage dump failed in {target}: "
                f"{(dumped.stderr or '').strip().splitlines()[-1:]}")
        tests, lines = parse_dump_full(dumped.stdout, target, package,
                                       id_prefix)
    finally:
        data_path.unlink(missing_ok=True)
    if not tests:
        raise RuntimeError(f"coverage produced no data in {target}")
    collected = sum(int(n) for n, _ in COUNT_RE.findall(output))
    return (TestMap(target=str(target), package=package,
                    built_with=built_with, tests=tests,
                    collected=collected, line_hits=lines),
            output)
