"""Baselines for the eval: grep and ruff NPY201. No LLM."""

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from chainreaction.interpret import CONFIG_KINDS, SEED_KINDS

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


def patterns_for_changeset(changeset) -> list[str]:
    """Grep patterns derived from the change set's own breakage symbols.

    One word-boundary pattern per verified removed/renamed member and
    config break short name (numpy.sctypes -> \\bsctypes\\b,
    ...PRICING -> \\bPRICING\\b), sorted and deduped. No hand-picked
    lists: the same rule serves every decision and repo, and an empty
    change set searches for nothing. Grep stays dumb — it cannot tell
    a real use from a comment — which is exactly what the baseline
    measures.
    """
    shorts = set()
    for item in changeset.items:
        if item.kind not in SEED_KINDS + CONFIG_KINDS:
            continue
        if not getattr(item, "verified", False):
            continue
        short = item.symbol.rpartition(".")[2] or item.symbol
        if short:
            shorts.add(short)
    return [r"\b" + re.escape(short) + r"\b" for short in sorted(shorts)]


def grep_hits(root: str | Path, patterns: list[str] | None = None) -> list[Hit]:
    """Naive regex search for removed-symbol mentions (the dumb baseline).

    None keeps the illustrative NumPy defaults; an explicit list —
    possibly empty — is used as given.
    """
    root = Path(root)
    if patterns is None:
        patterns = NUMPY2_PATTERNS
    compiled = [(p, re.compile(p)) for p in patterns]
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


def resolve_tool(name: str) -> str | None:
    """Locate a PATH tool (ruff today): PATH first, venv sibling second."""
    found = shutil.which(name)
    if found:
        return found
    exe = f"{name}.exe" if os.name == "nt" else name
    cand = Path(sys.executable).parent / exe
    return str(cand) if cand.exists() else None


def _ruff_exe() -> str | None:
    return resolve_tool("ruff")


def ruff_npy201(target: str | Path, run=None) -> list[Hit] | None:
    """Run `ruff check --select NPY201`; None when ruff is not installed.

    run(cmd, cwd) injects the subprocess layer (the run store passes a
    ctx.run_cmd adapter so replays reuse the recording); it must return
    an object with .stdout and .missing (set when never executed).
    """
    args = ["check", "--select", "NPY201", "--output-format", "concise",
            str(target)]
    if run is None:
        ruff = _ruff_exe()
        if ruff is None:
            return None
        proc = subprocess.run(
            [ruff, *args],
            capture_output=True,
            text=True,
            timeout=300,
        )
        out = proc.stdout
    else:
        # Never resolve here: the adapter records under the logical tool
        # name, so replay serves the recording even where ruff is absent.
        res = run(["ruff", *args], ".")
        if getattr(res, "missing", None):
            return None
        out = res.stdout
    hits: list[Hit] = []
    for line in out.splitlines():
        m = re.match(r"^(.+?):(\d+):\d+: (NPY201.*)$", line)
        if m:
            hits.append(Hit(m.group(1), int(m.group(2)), "NPY201", m.group(3)))
    return hits
