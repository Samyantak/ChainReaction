"""Fetch preset targets: clone missing checkouts at their pins.

Usage:  python scripts/fetch_targets.py [preset-id ...]  (default: all)

Idempotent: existing checkouts are verified against the pin and fail
clearly on mismatch. Both preset repos are public; no credentials.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from chainreaction import scenarios  # noqa: E402


def _target_dir(scen) -> Path:
    path = Path(scen.target)
    return path if path.is_absolute() else ROOT / path


def ensure_target(scen, run) -> str:
    """Clone/fetch scen.target at scen.ref; return the verified SHA.

    run(cmd, cwd) executes a command and returns an object with
    .returncode/.stdout/.stderr. Raises SystemExit with a clear message
    when the checkout is missing and unfetchable or at the wrong commit.
    """
    target = _target_dir(scen)
    if not scen.repo_url or not scen.ref:
        raise SystemExit(f"{scen.id}: no repo_url/ref pin configured")
    if not target.is_dir():
        print(f"{scen.id}: cloning {scen.repo_url}")
        done = run(["git", "clone", scen.repo_url, scen.target], ROOT)
        if done.returncode != 0:
            raise SystemExit(f"{scen.id}: clone failed: {done.stderr}")
        done = run(["git", "-C", scen.target, "checkout", scen.ref], ROOT)
        if done.returncode != 0:
            raise SystemExit(f"{scen.id}: checkout failed: {done.stderr}")
    proc = run(["git", "-C", scen.target, "rev-parse", "HEAD"], ROOT)
    head = (proc.stdout or "").strip()
    if proc.returncode != 0 or not head:
        raise SystemExit(f"{scen.id}: {scen.target} is not a git checkout; "
                         f"remove it and re-run to clone {scen.ref[:12]}")
    if head != scen.ref:
        raise SystemExit(f"{scen.id}: {scen.target} is at {head}, need "
                         f"{scen.ref}; check out the pinned commit")
    print(f"{scen.id}: ok at {head[:12]}")
    return head


def _real_run(cmd, cwd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=1200,
                          cwd=str(cwd))


def main(argv) -> int:
    ids = argv[1:] or sorted(scenarios.SCENARIOS)
    for preset in ids:
        try:
            scen = scenarios.get(preset)
        except KeyError:
            print(f"unknown preset: {preset}")
            return 2
        if not scen.repo_url:
            print(f"{preset}: no repo_url configured, skipped")
            continue
        try:
            ensure_target(scen, _real_run)
        except SystemExit as exc:
            print(exc)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
