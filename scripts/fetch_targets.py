"""Fetch preset targets: clone missing checkouts at their pins.

Usage:  python scripts/fetch_targets.py [preset-id ...]  (default: all)

Scenario presets resolve to targets/; committed generic runs
(runs/presets/<id>/ with a manifest but no scenario) resolve to their
cached workspaces/. Idempotent: existing checkouts are verified against
the pin and fail clearly on mismatch. All repos are public; no
credentials.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from chainreaction import scenarios  # noqa: E402
from chainreaction.generic import parse_repo_url, workspace_rel  # noqa: E402


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


def ensure_workspace(repo_url: str, sha: str, run) -> str:
    """Clone/fetch the cached workspace at sha; return the verified SHA.

    run(cmd, cwd) executes a command and returns an object with
    .returncode/.stdout/.stderr. Raises SystemExit with a clear message
    when the checkout is missing and unfetchable or at the wrong commit.
    """
    owner, repo = parse_repo_url(repo_url)
    ws_rel = workspace_rel(owner, repo, sha[:12])
    target = ROOT / ws_rel
    if not target.is_dir():
        print(f"{ws_rel}: cloning {repo_url}")
        done = run(["git", "clone", "--filter=blob:none", repo_url, ws_rel],
                   ROOT)
        if done.returncode != 0:
            raise SystemExit(f"{ws_rel}: clone failed: {done.stderr}")
        done = run(["git", "-C", ws_rel, "checkout", sha], ROOT)
        if done.returncode != 0:
            raise SystemExit(f"{ws_rel}: checkout failed: {done.stderr}")
    proc = run(["git", "-C", ws_rel, "rev-parse", "HEAD"], ROOT)
    head = (proc.stdout or "").strip()
    if proc.returncode != 0 or not head:
        raise SystemExit(f"{ws_rel} is not a git checkout; remove it and "
                         f"re-run to clone {sha[:12]}")
    if head != sha:
        raise SystemExit(f"{ws_rel} is at {head}, need {sha}; check out "
                         "the pinned commit")
    print(f"{ws_rel}: ok at {head[:12]}")
    return head


def _real_run(cmd, cwd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=1200,
                          cwd=str(cwd))


def _generic_pin(preset: str):
    """(repo_url, sha) for a committed generic preset, else None."""
    manifest = ROOT / "runs" / "presets" / preset / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if data.get("preset") or not data.get("repo_url"):
        return None
    sha = data.get("resolved_commit") or ""
    if len(sha) != 40:
        return None
    return data["repo_url"], sha


def main(argv) -> int:
    ids = argv[1:] or sorted(scenarios.SCENARIOS)
    for preset in ids:
        try:
            scen = scenarios.get(preset)
        except KeyError:
            pin = _generic_pin(preset)
            if pin is None:
                print(f"unknown preset: {preset}")
                return 2
            try:
                ensure_workspace(pin[0], pin[1], _real_run)
            except SystemExit as exc:
                print(exc)
                return 1
            continue
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
