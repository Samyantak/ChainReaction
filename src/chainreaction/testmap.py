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

    def covering(self, modules: set[str]) -> list[str]:
        """Sorted ids of tests executing any of the given modules."""
        want = set(modules)
        return sorted(t for t, ms in self.tests.items() if want & set(ms))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2),
                              encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> "TestMap":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return TestMap(d["target"], d["package"], d["built_with"], d["tests"],
                       d.get("collected", len(d["tests"])))


def file_to_module(path: str | Path, target: str | Path, package: str
                   ) -> str | None:
    """Map a measured file to its dotted module (None when out of package).

    Test files map to None: every test covers its own file, which would
    make covering() trivially select everything. conftest.py stays
    mapped (only fixture users cover it). src/ layouts are unwrapped.
    """
    try:
        rel = Path(os.path.relpath(path, target))
    except ValueError:
        return None
    parts = list(rel.parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if rel.suffix != ".py" or not parts or parts[0] != package:
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


def parse_coverage(data_file: str | Path, target: str | Path, package: str
                   ) -> dict[str, list[str]]:
    """Invert coverage contexts: test node id -> sorted executed modules."""
    cov = Coverage(data_file=str(data_file))
    cov.load()
    data = cov.get_data()
    out: dict[str, set[str]] = {}
    for fname in data.measured_files():
        mod = file_to_module(fname, target, package)
        if mod is None:
            continue
        ids = set()
        for names in data.contexts_by_lineno(fname).values():
            for raw in names:
                tid = raw.split("|")[0]
                if tid:  # "" is collection-time code, unattributed to a test
                    ids.add(_strip_target_prefix(tid, target))
        for tid in ids:
            out.setdefault(tid, set()).add(mod)
    return {t: sorted(ms) for t, ms in out.items()}


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


def predicted_failing(testmap: TestMap, g, hard: set[str],
                      target: str | Path, at_risk=None) -> set[str]:
    """Tests covering impacted modules, plus tests in files importing them.

    Coverage spans HARD and AT_RISK modules (in-function breaks only
    flag callers AT_RISK, but their tests still fail). The import-file
    expansion stays HARD-only: importing a module whose breaks are all
    in-function does not fail at import. Imports run at collection
    (unattributed to any test context), so a test file importing a
    HARD module fails even when its covered code is healthy.
    """
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
        tests = parse_coverage(data_file, target, package)
    finally:
        for stale in target_p.glob(".coverage-chainreaction*"):
            stale.unlink(missing_ok=True)
    collected = sum(int(n) for n, _ in COUNT_RE.findall(proc.stdout))
    return TestMap(target=str(target), package=package,
                   built_with=built_with, tests=tests, collected=collected)


COVERAGE_DATA_NAME = ".coverage-chainreaction"

# Coverage contexts dumped to stdout (recorded): {file: [test ids]}.
# The .coverage file itself never leaves the live machine, so replay
# rebuilds the identical map from the recording.
_DUMP_CODE = (
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


def parse_dump(text: str, target: str | Path, package: str,
               strip_prefix: str = "") -> dict[str, list[str]]:
    """Invert a coverage dump: test node id -> sorted executed modules."""
    try:
        raw = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, set[str]] = {}
    for fname, tids in raw.items():
        mod = file_to_module(fname, target, package)
        if mod is None:
            continue
        for tid in tids or []:
            if strip_prefix and tid.startswith(strip_prefix):
                tid = tid[len(strip_prefix):]
            out.setdefault(tid, set()).add(mod)
    return {t: sorted(ms) for t, ms in out.items()}


def build_map_with(run, python: str, target: str | Path, package: str,
                   tests_rel: str = ".", timeout: int = 1800,
                   built_with: str = "",
                   data_name: str = COVERAGE_DATA_NAME,
                   id_prefix: str = "") -> tuple[TestMap, str]:
    """Coverage test map over an injected runner; returns (map, output).

    run(cmd, cwd, extra_env) executes (recorded runs pass a ctx.run_cmd
    adapter: outputs come back unscrubbed, the dump is recorded, and
    replay rebuilds the map from the recording). The suite output is
    returned so the caller can parse baseline counts from the same run.
    Raises RuntimeError when coverage produces no mappable data.
    """
    cwd = str(target)
    data_path = Path(cwd) / data_name
    data_path.unlink(missing_ok=True)
    proc = run(
        [python, "-m", "pytest", tests_rel, "-q", "--tb=no",
         "-p", "no:cacheprovider", "--continue-on-collection-errors",
         "-rfE", f"--cov={package}", "--cov-context=test",
         "--cov-report="],
        cwd, extra_env={"COVERAGE_FILE": data_name})
    if getattr(proc, "timed_out", False):
        raise RuntimeError(f"test suite timed out in {target}")
    output = proc.stdout
    try:
        dumped = run([python, "-c", _DUMP_CODE, data_name], cwd)
        if dumped.returncode != 0:
            raise RuntimeError(
                f"coverage dump failed in {target}: "
                f"{(dumped.stderr or '').strip().splitlines()[-1:]}")
        tests = parse_dump(dumped.stdout, target, package, id_prefix)
    finally:
        data_path.unlink(missing_ok=True)
    if not tests:
        raise RuntimeError(f"coverage produced no data in {target}")
    collected = sum(int(n) for n, _ in COUNT_RE.findall(output))
    return (TestMap(target=str(target), package=package,
                    built_with=built_with, tests=tests,
                    collected=collected),
            output)
