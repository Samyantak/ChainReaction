"""Create the version-pinned target environments.

envs/numpy1 -> numpy 1.26.4 (the "before" run: the import passes)
envs/numpy2 -> numpy 2.0.2  (the "after" run: the sctypes break reproduces)

Needs `uv` (it fetches Python 3.11; neither pinned numpy ships
wheels for the 3.13 interpreter this repo itself runs on).
Re-runnable: existing envs are left alone unless --fresh is given.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_PYTHON = "3.11"
ENVS = {"numpy1": "numpy==1.26.4", "numpy2": "numpy==2.0.2"}
# packaging+pytest: run the target suite; coverage+pytest-cov: build the
# test map; scipy/h5py/pydicom: nibabel's optional test deps (harmless
# for other targets).
EXTRA = ["packaging>=17", "pytest", "coverage>=7", "pytest-cov",
         "scipy", "h5py", "pydicom<3"]  # this nibabel predates pydicom 3


def env_python(envdir: Path) -> Path:
    return (envdir / "Scripts" / "python.exe" if sys.platform == "win32"
            else envdir / "bin" / "python")


def run(*cmd: str) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> int:
    if shutil.which("uv") is None:
        print("error: `uv` not found (install: https://docs.astral.sh/uv/)")
        return 2
    fresh = "--fresh" in sys.argv
    run("uv", "python", "install", BASE_PYTHON)
    for name, pin in ENVS.items():
        envdir = ROOT / "envs" / name
        if envdir.exists():
            if not fresh:
                print(f"{envdir} exists, leaving alone (--fresh to rebuild)")
                continue
            shutil.rmtree(envdir)
        run("uv", "venv", "-p", BASE_PYTHON, str(envdir))
        run("uv", "pip", "install", "--python", str(env_python(envdir)),
            pin, *EXTRA)
        out = subprocess.run(
            [str(env_python(envdir)), "-c",
             "import numpy; print(numpy.__version__)"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        want = pin.split("==")[1]
        assert out == want, f"{name}: got numpy {out}, want {want}"
        print(f"{name}: numpy {out} OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
