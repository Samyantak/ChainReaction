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
    mapped (only fixture users cover it).
    """
    try:
        rel = Path(os.path.relpath(path, target))
    except ValueError:
        return None
    if rel.suffix != ".py" or rel.parts[0] != package:
        return None
    if "tests" in rel.parts or rel.name.startswith("test_"):
        return None
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
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


def failing_tests(python: str, target: str | Path, tests: str,
                  testmap: TestMap, timeout: int = 1800) -> set[str]:
    """Real failing test ids: FAILED plus tests in ERROR files.

    Runs with --noconftest: the target conftest itself imports the broken
    chain, which would collapse collection before any test runs.
    """
    proc = subprocess.run(
        [python, "-m", "pytest", tests, "-q", "--tb=no",
         "-p", "no:cacheprovider", "--noconftest",
         "--continue-on-collection-errors", "-rfE"],
        cwd=str(Path(target).resolve()), capture_output=True, text=True,
        timeout=timeout, check=False,
    )
    failed, errfiles = parse_failures(proc.stdout, target)
    actual = set(failed)
    for f in errfiles:
        prefix = f.replace("\\", "/") + "::"
        actual |= {t for t in testmap.tests
                   if t.replace("\\", "/").startswith(prefix)}
    return actual & set(testmap.tests)


def predicted_failing(testmap: TestMap, g, hard: set[str],
                      target: str | Path) -> set[str]:
    """Tests covering HARD modules, plus tests in files importing them.

    Imports run at collection (unattributed to any test context), so a
    test file importing a HARD module fails even when its covered code
    is healthy.
    """
    pred = set(testmap.covering(set(hard)))
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
