"""Replay every committed preset offline; fail unless report.json is byte-identical.

Usage:  python scripts/check_replay.py [--timeout N]  (default 600s per preset)

Copies each preset to a temp runs dir, replays it with no key and no
network, and compares the replayed report.json bytes with the committed
ones. Clean: the worktree gains no run dirs. CI runs this after pytest.
"""

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from chainreaction.runs import store, worker  # noqa: E402

# Presets that cannot replay byte-identical right now, with owner + reason.
# Remove entries as soon as the re-record lands; never add one silently.
KNOWN_BAD = {
    # Session A is re-recording SponsorFit on main; the committed
    # recording predates the current vendor pipeline.
    "sponsorfit-meta": "Session A re-recording",
}


def _wait(run_id: str, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        manifest = store.read_json(store.run_dir(run_id) / store.MANIFEST)
        if manifest.get("status") in store.TERMINAL:
            return manifest
        if time.monotonic() > deadline:
            raise TimeoutError(f"replay {run_id} timed out after {timeout}s")
        time.sleep(1)


def _presets() -> list:
    root = ROOT / "runs" / "presets"
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and (p / store.MANIFEST).is_file())


def _checkout_ok(preset: str) -> str | None:
    """None when the preset's checkout is on disk at its pin, else a skip reason."""
    import json

    manifest = json.loads(
        (ROOT / "runs" / "presets" / preset / store.MANIFEST).read_text())
    report_path = ROOT / "runs" / "presets" / preset / store.ARTIFACTS / "report.json"
    target = ""
    if report_path.is_file():
        try:
            target = json.loads(report_path.read_text()).get("target") or ""
        except ValueError:
            pass
    if not target:
        target = manifest.get("workspace") or ""
    if not target:
        return "no target recorded"
    checkout = ROOT / target
    if not (checkout / ".git").is_dir():
        return f"{target} not on disk (run fetch_targets.py)"
    want = manifest.get("resolved_commit") or ""
    try:
        proc = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30)
    except OSError:
        return "git unavailable"
    if want and proc.stdout.strip() != want:
        return f"{target} at {proc.stdout.strip()[:12]}, want {want[:12]}"
    return None


def main() -> int:
    import os

    timeout = 600
    args = sys.argv[1:]
    if args[:1] == ["--timeout"] and len(args) == 2:
        timeout = int(args[1])
    elif args:
        print("usage: python scripts/check_replay.py [--timeout N]")
        return 2
    if os.environ.get("MODEL_API_KEY"):
        print("warning: MODEL_API_KEY is set; replay must not need it "
              "(unsetting for this check)")
        del os.environ["MODEL_API_KEY"]

    presets = _presets()
    if not presets:
        print("no presets on disk")
        return 1
    failed, skipped = [], []
    with tempfile.TemporaryDirectory(prefix="cr-replay-") as tmp:
        os.environ["CHAINREACTION_RUNS_DIR"] = str(Path(tmp) / "runs")
        dest_root = Path(tmp) / "runs" / "presets"
        for preset in presets:
            if preset in KNOWN_BAD:
                print(f"{preset}: SKIP ({KNOWN_BAD[preset]})")
                skipped.append(preset)
                continue
            reason = _checkout_ok(preset)
            if reason is not None:
                print(f"{preset}: SKIP ({reason})")
                skipped.append(preset)
                continue
            shutil.copytree(ROOT / "runs" / "presets" / preset,
                            dest_root / preset)
            try:
                replay = worker.create_run(
                    worker.RunRequest(preset=preset, mode="replay"))
                worker.submit_run(replay)
                manifest = _wait(replay, timeout)
            except Exception as exc:  # noqa: BLE001 - report, keep going
                print(f"{preset}: FAIL ({exc})")
                failed.append(preset)
                continue
            if manifest.get("status") != "done":
                print(f"{preset}: FAIL (status {manifest.get('status')}: "
                      f"{manifest.get('error')})")
                failed.append(preset)
                continue
            committed = (ROOT / "runs" / "presets" / preset
                         / store.ARTIFACTS / "report.json").read_bytes()
            replayed = (store.run_dir(replay) / store.ARTIFACTS
                        / "report.json").read_bytes()
            if committed != replayed:
                print(f"{preset}: FAIL (report.json differs)")
                failed.append(preset)
            else:
                print(f"{preset}: ok (byte-identical)")
    print(f"\n{len(presets) - len(failed) - len(skipped)} passed, "
          f"{len(skipped)} skipped, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
