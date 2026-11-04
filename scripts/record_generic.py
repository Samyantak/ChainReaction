"""Run (and optionally record) a generic M2 run from the command line.

Usage:
  python scripts/record_generic.py --repo URL --decision D [--ref R]
      [--guide URL] [--package P] [--record-as ID] [--timeout S]

Live mode needs MODEL_API_KEY, network, git, and uv. --record-as copies
the finished run to runs/presets/<ID>/, scans for leaks, and verifies
replay produces a byte-identical report.json (like record_preset.py).
Prints DONE <run_id> or FAIL <reason> as the last line (for monitors).
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from chainreaction.runs import store, worker  # noqa: E402


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--decision", required=True)
    parser.add_argument("--ref", default="")
    parser.add_argument("--guide", default="")
    parser.add_argument("--package", default="")
    parser.add_argument("--record-as", default="")
    parser.add_argument("--timeout", type=int, default=5400)
    parser.add_argument("--fix-rounds", type=int, default=None)
    return parser.parse_args(argv)


def _wait(run_id: str, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        manifest = store.read_json(store.run_dir(run_id) / store.MANIFEST)
        if manifest.get("status") in store.TERMINAL:
            return manifest
        if time.monotonic() > deadline:
            raise SystemExit(f"timed out waiting for {run_id}")
        time.sleep(5)


def main(argv=None) -> int:
    args = parse_args(argv or sys.argv[1:])
    run_id = worker.create_run(worker.RunRequest(
        mode="live", repo_url=args.repo, ref=args.ref,
        decision=args.decision, guide_url=args.guide, package=args.package,
        allow_untrusted=True, fix_rounds=args.fix_rounds))
    print(f"live run: {run_id}", flush=True)
    worker.submit_run(run_id)
    try:
        manifest = _wait(run_id, args.timeout)
    except SystemExit as exc:
        print(f"FAIL {exc}", flush=True)
        return 1
    if manifest.get("status") != "done":
        print(f"FAIL live run {manifest['status']}: "
              f"{manifest.get('error')}", flush=True)
        return 1
    print(f"live done: {manifest.get('workspace')} "
          f"decision={manifest.get('decision_package')}=={manifest.get('decision_version')} "
          f"guide={manifest.get('guide_method')}", flush=True)
    if not args.record_as:
        print(f"DONE {run_id}", flush=True)
        return 0
    dest = store.preset_dir(args.record_as)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(store.run_dir(run_id), dest)
    print(f"copied to {dest}", flush=True)
    issues = store.scan_for_leaks(dest)
    if issues:
        print("leak scan FAILED:", flush=True)
        for issue in issues:
            print(f"  {issue}", flush=True)
        return 1
    print("leak scan clean (no key, no absolute paths, LF-only)", flush=True)
    for gzipped in store.compress_preset(dest):
        print(f"compressed {gzipped}", flush=True)
    replay = worker.create_run(
        worker.RunRequest(preset=args.record_as, mode="replay"))
    print(f"replay run: {replay}", flush=True)
    worker.submit_run(replay)
    manifest = _wait(replay, args.timeout)
    if manifest.get("status") != "done":
        print(f"FAIL replay {manifest['status']}: {manifest.get('error')}",
              flush=True)
        return 1
    for name in ("report.json", "scores.json", "prediction.json"):
        live_bytes = (dest / "artifacts" / name).read_bytes()
        replay_bytes = (store.run_dir(replay) / "artifacts"
                        / name).read_bytes()
        if live_bytes != replay_bytes:
            print(f"FAIL {name} DIFFERS between live and replay", flush=True)
            return 1
        print(f"byte-identical {name} ({len(live_bytes)} bytes)", flush=True)
    print(f"DONE {run_id}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
