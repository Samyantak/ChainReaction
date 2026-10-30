"""Verifier: run target code for real, parse tracebacks into actual chains.

v1 runs import probes in a subprocess (the decision — e.g. "upgrade numpy
to 2.0" — is realized by the probe interpreter's installed numpy) and maps
traceback frames back to target modules. No LLM involved.
"""

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

FRAME_RE = re.compile(r'^\s*File "(.+)", line (\d+), in (\S+)\s*$')


@dataclass
class Frame:
    file: str
    line: int
    func: str


@dataclass
class ProbeResult:
    ok: bool
    numpy_version: str | None
    frames: list[Frame] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    stderr: str = ""


ROOT = Path(__file__).resolve().parent.parent.parent


def target_python(env: str) -> str:
    """Interpreter for a version-pinned target env (numpy1/numpy2).

    The target must never run under ChainReaction's own interpreter:
    what numpy it sees IS the decision under test.
    """
    exe = ROOT / "envs" / env / ("Scripts/python.exe"
                                 if sys.platform == "win32"
                                 else "bin/python")
    if not exe.exists():
        raise FileNotFoundError(
            f"target env {env!r} missing ({exe}); "
            "run: python scripts/make_target_envs.py")
    return str(exe)


def _run(python: str, cwd: str | Path, code: str, timeout: int = 300):
    return subprocess.run(
        [python, "-c", code],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_import_probe(
    python: str, cwd: str | Path, package: str, preamble: str = ""
) -> ProbeResult:
    """Run `import <package>` in a subprocess; capture numpy version + traceback."""
    code = (
        "try:\n"
        "    import numpy\n"
        "    print('NUMPY=' + numpy.__version__)\n"
        "except ImportError:\n"
        "    print('NUMPY=MISSING')\n"
        f"{preamble}\n"
        f"import {package}\n"
    )
    proc = _run(python, cwd, code)
    numpy_version = None
    for line in proc.stdout.splitlines():
        if line.startswith("NUMPY="):
            numpy_version = line.removeprefix("NUMPY=")
    frames = parse_traceback(proc.stderr)
    modules = frames_to_modules(frames, Path(cwd), package)
    return ProbeResult(
        ok=proc.returncode == 0,
        numpy_version=numpy_version,
        frames=frames,
        modules=modules,
        stderr=proc.stderr,
    )


def run_snippet_probe(python: str, cwd: str | Path, code: str,
                      package: str = "") -> ProbeResult:
    """Run an arbitrary decision snippet; map its traceback to modules."""
    proc = _run(python, cwd, code)
    frames = parse_traceback(proc.stderr)
    return ProbeResult(
        ok=proc.returncode == 0,
        numpy_version=None,
        frames=frames,
        modules=frames_to_modules(frames, Path(cwd), package),
        stderr=proc.stderr,
    )


def parse_traceback(stderr: str) -> list[Frame]:
    frames = []
    for line in stderr.splitlines():
        m = FRAME_RE.match(line)
        if m:
            frames.append(Frame(file=m.group(1), line=int(m.group(2)), func=m.group(3)))
    return frames


def frames_to_modules(frames: list[Frame], root: str | Path,
                      package: str = "") -> list[str]:
    """Map frames under root to dotted modules, deduped (call order kept)."""
    root = Path(root).resolve()
    modules: list[str] = []
    for f in frames:
        try:
            rel = Path(f.file).resolve().relative_to(root)
        except (ValueError, OSError):
            continue
        if rel.suffix != ".py":
            continue
        parts = list(rel.with_suffix("").parts)
        if package and package in parts:
            parts = parts[parts.index(package):]  # src/ layout: start at package
        if parts[-1] == "__init__":
            parts = parts[:-1]
        mod = ".".join(parts)
        if not modules or modules[-1] != mod:
            modules.append(mod)
    return modules


def main() -> int:  # `python -m chainreaction.verifier <target_dir> <package>`
    target, package = sys.argv[1], sys.argv[2]
    res = run_import_probe(sys.executable, target, package)
    print(f"ok={res.ok} numpy={res.numpy_version}")
    for m in res.modules:
        print(f"  {m}")
    if not res.ok:
        print("--- stderr tail ---")
        print("\n".join(res.stderr.splitlines()[-8:]))
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
