"""Create the version-pinned target environments.

envs/numpy1 -> numpy 1.26.4 (the "before" run: the import passes)
envs/numpy2 -> numpy 2.0.2  (the "after" run: the sctypes break reproduces)
envs/sponsorfit -> sponsorfit's deps (the Meta-switch decision probe)

Needs `uv` (it fetches older Pythons; neither pinned numpy ships
wheels for the 3.13 interpreter this repo itself runs on, and
sponsorfit requires <3.13).
Re-runnable: existing envs are left alone unless --fresh is given.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# packaging+pytest: run the target suite; coverage+pytest-cov: build the
# test map; scipy/h5py/pydicom: nibabel's optional test deps.
NUMPY_EXTRA = ["packaging>=17", "pytest", "coverage>=7", "pytest-cov",
               "scipy", "h5py",
               "pydicom<3"]  # this nibabel predates pydicom 3
ENVS = {
    # name: (base python, packages, import check, expected version or None)
    "numpy1": ("3.11", ["numpy==1.26.4", *NUMPY_EXTRA], "numpy", "1.26.4"),
    "numpy2": ("3.11", ["numpy==2.0.2", *NUMPY_EXTRA], "numpy", "2.0.2"),
    "sponsorfit": ("3.12",
                   ["anthropic>=0.40", "feedparser>=6.0.14", "openai>=1.50",
                    "protego>=0.7.0", "python-dotenv>=1.0", "pytest>=8.0"],
                   "openai", None),
}


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
    only = next((a.split("=", 1)[1] for a in sys.argv
                 if a.startswith("--only=")), None)
    for name, (base_python, packages, check_mod, want) in ENVS.items():
        if only is not None and name != only:
            continue
        run("uv", "python", "install", base_python)
        envdir = ROOT / "envs" / name
        if envdir.exists():
            if not fresh:
                print(f"{envdir} exists, leaving alone (--fresh to rebuild)")
                continue
            shutil.rmtree(envdir)
        run("uv", "venv", "-p", base_python, str(envdir))
        run("uv", "pip", "install", "--python", str(env_python(envdir)),
            *packages)
        out = subprocess.run(
            [str(env_python(envdir)), "-c",
             f"import {check_mod}; "
             f"print(getattr({check_mod}, '__version__', 'ok'))"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if want is not None:
            assert out == want, f"{name}: got {out}, want {want}"
        print(f"{name}: {check_mod} {out} OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
