"""Baselines for the eval: grep and ruff NPY201. No LLM."""

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Illustrative subset of NumPy 2.0 removals (not exhaustive).
NUMPY2_PATTERNS = [
    r"np\.sctypes",
    r"np\.maximum_sctype",
    r"np\.float_",
    r"np\.unicode_",
    r"np\.string_",
    r"np\.Inf\b",
    r"np\.NaN\b",
    r"np\.bool8",
    r"np\.object0",
    r"np\.mat\b",
]


@dataclass
class Hit:
    file: str
    line: int
    pattern: str
    text: str


def grep_hits(root: str | Path, patterns: list[str] | None = None) -> list[Hit]:
    root = Path(root)
    compiled = [(p, re.compile(p)) for p in (patterns or NUMPY2_PATTERNS)]
    hits: list[Hit] = []
    for path in sorted(root.rglob("*.py")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for i, text in enumerate(lines, 1):
            for pattern, rx in compiled:
                if rx.search(text):
                    hits.append(Hit(str(path), i, pattern, text.strip()))
                    break
    return hits


def _ruff_exe() -> str | None:
    found = shutil.which("ruff")
    if found:
        return found
    name = "ruff.exe" if os.name == "nt" else "ruff"
    cand = Path(sys.executable).parent / name
    return str(cand) if cand.exists() else None


def ruff_npy201(target: str | Path) -> list[Hit] | None:
    """Run `ruff check --select NPY201`; None when ruff is not installed."""
    ruff = _ruff_exe()
    if ruff is None:
        return None
    proc = subprocess.run(
        [ruff, "check", "--select", "NPY201", "--output-format", "concise",
         str(target)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    hits: list[Hit] = []
    for line in proc.stdout.splitlines():
        m = re.match(r"^(.+?):(\d+):\d+: (NPY201.*)$", line)
        if m:
            hits.append(Hit(m.group(1), int(m.group(2)), "NPY201", m.group(3)))
    return hits
