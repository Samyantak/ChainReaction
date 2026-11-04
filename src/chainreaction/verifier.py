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
# pytest's native entry headers (--tb=short): bare
# "tests/x.py:12: in fun" lines (the ____/==== runs sit on their own
# line). Paths are relative to the pytest cwd; callers resolve them.
PYTEST_FRAME_RE = re.compile(
    r"^(?:[=_]+\s+)?(.+?\.py):(\d+): in (\S+)\s*$")


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
    python: str, cwd: str | Path, package: str, preamble: str = "",
    run=None, timeout: int = 300,
) -> ProbeResult:
    """Run `import <package>` in a subprocess; capture numpy version + traceback.

    run(cmd, cwd) injects the subprocess layer (the run store passes a
    ctx.run_cmd adapter so replays reuse the recording); it must return
    an object with .returncode/.stdout/.stderr. timeout only applies to
    the default runner; injected runners own their timeout.
    """
    code = (
        "try:\n"
        "    import numpy\n"
        "    print('NUMPY=' + numpy.__version__)\n"
        "except ImportError:\n"
        "    print('NUMPY=MISSING')\n"
        f"{preamble}\n"
        f"import {package}\n"
    )
    proc = (_run(python, cwd, code, timeout) if run is None
            else run([python, "-c", code], cwd))
    numpy_version = None
    for line in proc.stdout.splitlines():
        if line.startswith("NUMPY="):
            numpy_version = line.removeprefix("NUMPY=") or None
    if numpy_version == "MISSING":
        numpy_version = None
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
                      package: str = "", run=None,
                      timeout: int = 300) -> ProbeResult:
    """Run an arbitrary decision snippet; map its traceback to modules.

    run(cmd, cwd) injects the subprocess layer (see run_import_probe).
    """
    proc = (_run(python, cwd, code, timeout) if run is None
            else run([python, "-c", code], cwd))
    frames = parse_traceback(proc.stderr)
    return ProbeResult(
        ok=proc.returncode == 0,
        numpy_version=None,
        frames=frames,
        modules=frames_to_modules(frames, Path(cwd), package),
        stderr=proc.stderr,
    )


def parse_traceback(stderr: str) -> list[Frame]:
    """Frames from Python tracebacks and pytest native headers.

    Probe outputs only ever match FRAME_RE; pytest --tb=short suite
    outputs only ever match PYTEST_FRAME_RE. Both list outermost
    first, so callers take the deepest in-tree frame the same way.
    Paths normalize explicitly (recorded Windows output carries
    backslashes; Path/os.sep only convert them on Windows).
    """
    frames = []
    for line in stderr.splitlines():
        m = FRAME_RE.match(line)
        if m:
            frames.append(Frame(file=m.group(1).replace("\\", "/"),
                                line=int(m.group(2)), func=m.group(3)))
            continue
        m = PYTEST_FRAME_RE.match(line)
        if m:
            frames.append(Frame(file=m.group(1).replace("\\", "/"),
                                line=int(m.group(2)), func=m.group(3)))
    return frames


def site_packages_subpath(file: str, package: str) -> str | None:
    """Posix subpath under site-packages/<package>/ for installed-copy frames.

    Non-editable installs point tracebacks at the env's copy, in real
    form (E:/.../after/Lib/site-packages/flask_login/x.py) or scrubbed
    (<env:after>/site-packages/flask_login/x.py). Both map back the
    same way so live and replay agree with or without local envs.
    None for anything else under site-packages (foreign packages).
    """
    if not package:
        return None
    text = str(file).replace("\\", "/")
    marker = f"site-packages/{package}/"
    idx = text.find(marker)
    if idx >= 0 and (idx == 0 or text[idx - 1] == "/"):
        rest = text[idx + len(marker):]
        return rest or None
    marker = f"site-packages/{package}.py"
    idx = text.find(marker)
    if (idx >= 0 and (idx == 0 or text[idx - 1] == "/")
            and idx + len(marker) == len(text)):
        return f"{package}.py"
    return None


def frames_to_modules(frames: list[Frame], root: str | Path,
                      package: str = "") -> list[str]:
    """Map frames under root to dotted modules, deduped (call order kept).

    Frames in the env's installed copy of the target package
    (site-packages/<package>/) map back to repo modules the same way.
    """
    root = Path(root).resolve()
    modules: list[str] = []

    def push(mod: str) -> None:
        if mod and (not modules or modules[-1] != mod):
            modules.append(mod)

    for f in frames:
        try:
            rel = Path(f.file).resolve().relative_to(root)
        except (ValueError, OSError):
            rel = None
        if rel is None:
            sub = site_packages_subpath(f.file, package)
            if sub is not None and sub.endswith(".py"):
                parts = sub[:-len(".py")].split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                push(package if not parts
                     else f"{package}." + ".".join(parts))
            continue
        if rel.suffix != ".py":
            continue
        parts = list(rel.with_suffix("").parts)
        if package and package in parts:
            parts = parts[parts.index(package):]  # src/: start at package
        if parts[-1] == "__init__":
            parts = parts[:-1]
        push(".".join(parts))
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
