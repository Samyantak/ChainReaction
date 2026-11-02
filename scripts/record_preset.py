"""Record a committed preset: live run -> runs/presets/<id>/ -> verify replay.

Usage:  python scripts/record_preset.py nibabel-numpy2

Needs MODEL_API_KEY (two real model calls: interpret + narrative), the
target on disk, and the target envs built. Verifies the honest-replay
proof before finishing: replaying the preset produces a byte-identical
report.json.
"""

import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from chainreaction.runs import store, worker

TIMEOUT_S = 1800


def _wait(run_id: str) -> dict:
    deadline = time.monotonic() + TIMEOUT_S
    while True:
        manifest = store.read_json(store.run_dir(run_id) / store.MANIFEST)
        if manifest.get("status") in store.TERMINAL:
            return manifest
        if time.monotonic() > deadline:
            raise SystemExit(f"timed out waiting for {run_id}")
        time.sleep(2)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/record_preset.py <preset>")
        return 2
    preset = sys.argv[1]
    live = worker.create_run(worker.RunRequest(preset=preset, mode="live"))
    print(f"live run: {live}")
    worker.submit_run(live)
    manifest = _wait(live)
    if manifest.get("status") != "done":
        print(f"live run {manifest['status']}: {manifest.get('error')}")
        return 1
    print(f"live done: changeset={manifest.get('changeset_source')} "
          f"narrative={manifest.get('narrative_source')}")
    if not manifest.get("resolved_commit"):
        print("WARNING: target has no git provenance; replay cannot "
              "verify the commit")
    dest = store.preset_dir(preset)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(store.run_dir(live), dest)
    print(f"copied to {dest}")
    issues = store.scan_for_leaks(dest)
    if issues:
        print("leak scan FAILED:")
        for issue in issues:
            print(f"  {issue}")
        return 1
    print("leak scan clean (no key, no absolute paths, LF-only)")
    replay = worker.create_run(
        worker.RunRequest(preset=preset, mode="replay"))
    print(f"replay run: {replay}")
    worker.submit_run(replay)
    manifest = _wait(replay)
    if manifest.get("status") != "done":
        print(f"replay {manifest['status']}: {manifest.get('error')}")
        return 1
    live_report = (dest / "artifacts" / "report.json").read_bytes()
    replay_report = (store.run_dir(replay) / "artifacts"
                     / "report.json").read_bytes()
    if live_report != replay_report:
        print("report.json DIFFERS between live and replay")
        return 1
    print(f"byte-identical report.json ({len(live_report)} bytes)")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
