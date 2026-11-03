"""`python -m chainreaction serve`: API + prebuilt UI on localhost:8000.

Replay works with no key. The UI is served from the committed `ui/dist`
build; run `npm ci && npm run build` in `ui/` to rebuild it.
"""

import argparse
import sys
from pathlib import Path


def _dist_index() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "ui" / "dist" / "index.html"


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m chainreaction",
        description="ChainReaction: verifiable upgrade-impact investigator",
    )
    parser.add_argument(
        "command", nargs="?", default="serve",
        help="only 'serve' is supported",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.command != "serve":
        parser.print_usage(sys.stderr)
        print(f"error: unknown command {args.command!r} (only 'serve')",
              file=sys.stderr)
        return 2
    from chainreaction.web import app  # noqa: PLC0415 - deferred for --help speed

    if not _dist_index().is_file():
        print("warning: prebuilt UI not found at ui/dist — the API still "
              "works, but run `npm ci && npm run build` in ui/ for the app")
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '::1') else args.host}:{args.port}"
    print(f"ChainReaction: {url}")
    import uvicorn  # noqa: PLC0415

    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
