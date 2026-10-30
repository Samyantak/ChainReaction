"""Coverage test map: build (before env) and failing-test score (after env).

Default: run the target suite under coverage with the numpy1 interpreter
and save reports/testmap-<scenario>.json.
--score: predict failing tests (covering HARD modules) and compare against
the real numpy2 suite result.
"""

import subprocess
import sys
from pathlib import Path

from chainreaction import scenarios
from chainreaction.pipeline import predict
from chainreaction.scorer import precision_recall
from chainreaction.testmap import (
    TestMap,
    build_map,
    failing_tests,
    predicted_failing,
)
from chainreaction.verifier import target_python


def _opt(prefix: str, default: str | None = None) -> str | None:
    return next((a.split("=", 1)[1] for a in sys.argv if a.startswith(prefix)),
                default)


def _map_path(scenario_id: str) -> Path:
    return Path("reports") / f"testmap-{scenario_id}.json"


def _numpy_version(python: str) -> str:
    out = subprocess.run(
        [python, "-c", "import numpy; print(numpy.__version__)"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return out


def build(scenario_id: str, tests: str, timeout: int) -> int:
    scen = scenarios.get(scenario_id)
    python = target_python("numpy1")
    tag = f"numpy1 (numpy {_numpy_version(python)})"
    print(f"target={scen.target} tests={tests} env={tag}")
    testmap = build_map(python, scen.target, scen.package, tests,
                        timeout=timeout, built_with=tag)
    out = _map_path(scenario_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    testmap.save(out)
    mods = {m for ms in testmap.tests.values() for m in ms}
    print(f"mapped {len(testmap.tests)} tests -> {len(mods)} modules: {out}")
    return 0


def score(scenario_id: str, tests: str, timeout: int) -> int:
    path = _map_path(scenario_id)
    if not path.exists():
        print(f"no map at {path}; build it first (map_tests.py without --score)")
        return 2
    testmap = TestMap.load(path)
    scen, _, _, g, impacts = predict(scenario_id)
    hard = {n for n, i in impacts.items() if i.status == "HARD"}
    predicted = predicted_failing(testmap, g, hard, scen.target)
    actual = failing_tests(target_python("numpy2"), scen.target,
                           tests, testmap, timeout)
    s = precision_recall(predicted, actual)
    print(f"universe: {len(testmap.tests)} mapped / "
          f"{testmap.collected} collected")
    print(f"predicted failing: {len(predicted)}, actual failing: {len(actual)}")
    if actual == set(testmap.tests):
        print("(total break: every mapped test fails — "
              "P/R discriminates on partial breaks)")
    print(f"test P/R: {s.precision:.2f}/{s.recall:.2f}")
    missed = sorted(s.actual - s.predicted)
    print(f"missed ({len(missed)}): {missed[:10]}"
          + (" ..." if len(missed) > 10 else ""))
    extra = sorted(s.predicted - s.actual)
    print(f"extra ({len(extra)}): {extra[:10]}"
          + (" ..." if len(extra) > 10 else ""))
    return 0


def main() -> int:
    scenario_id = _opt("--scenario=", "nibabel-numpy2")
    tests = _opt("--tests=", ".")
    timeout = int(_opt("--timeout=", "1800"))
    if "--score" in sys.argv:
        return score(scenario_id, tests, timeout)
    return build(scenario_id, tests, timeout)


if __name__ == "__main__":
    raise SystemExit(main())
