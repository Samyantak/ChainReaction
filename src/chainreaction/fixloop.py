"""Fix loop: propose unified-diff patches -> run suite -> record.

Patches apply to a fresh temp copy of the target (the shared snapshot is
never mutated). Patch proposal uses Muse Spark (needs MODEL_API_KEY);
the red-to-green mechanics run key-free.
"""

import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from chainreaction.llm import chat, extract_json

COUNT_RE = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)")


class PatchApplyError(RuntimeError):
    pass


@dataclass
class Patch:
    id: str
    description: str
    diff: str  # unified diff, paths relative to the target root


@dataclass
class SuiteResult:
    returncode: int
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    summary: str = ""
    tail: str = ""


@dataclass
class LoopIteration:
    index: int
    applied: list[str] = field(default_factory=list)
    result: SuiteResult = field(default_factory=lambda: SuiteResult(-1))
    green: bool = False


@dataclass
class LoopResult:
    workdir: str
    iterations: list[LoopIteration] = field(default_factory=list)
    green: bool = False


def copy_target(target: str | Path) -> Path:
    """Fresh work copy of the target tree (snapshot stays pristine)."""
    workdir = Path(tempfile.mkdtemp(prefix="cr-fix-"))
    shutil.rmtree(workdir)
    shutil.copytree(target, workdir, ignore_dangling_symlinks=True)
    return workdir


def _prune_old_workdirs(keep: Path) -> None:
    """Delete stale cr-fix-* work copies; keep the latest for inspection."""
    for entry in keep.parent.glob("cr-fix-*"):
        if entry != keep and entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)


def _clear_pycache(workdir: str | Path) -> None:
    """Drop bytecode caches so the next suite run sees patched sources.

    A patch that keeps file size and lands in the same mtime second
    would otherwise reuse the previous run's .pyc and silently no-op.
    """
    for cache in Path(workdir).rglob("__pycache__"):
        if cache.is_dir() and not cache.is_symlink():
            shutil.rmtree(cache, ignore_errors=True)


def _normalize_diff(diff: str) -> str:
    """Give bare blank context lines their required single space.

    Hand-written and model-generated diffs often emit truly empty lines
    where unified diff requires " " (a space). Added/removed lines always
    start with +/- so a bare "" line is unambiguously blank context.
    (To ADD a blank line, write "+" — a bare line always means context.)
    """
    trailing_nl = diff.endswith("\n")
    lines = diff.split("\n")
    if trailing_nl and lines and lines[-1] == "":
        lines.pop()
    text = "\n".join(" " if ln == "" else ln for ln in lines)
    return text + "\n" if trailing_nl else text


def apply_patch(workdir: str | Path, patch: Patch) -> None:
    """Apply a unified diff with `git apply` (works outside any repo).

    The diff goes over the pipe as UTF-8 bytes: text mode would translate
    LF to CRLF on Windows and then never match the target's LF files.
    """
    proc = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        input=_normalize_diff(patch.diff).encode("utf-8"),
        cwd=str(workdir),
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace")
        raise PatchApplyError(f"patch {patch.id} failed to apply: "
                              f"{stderr.strip()}")


def run_suite(python: str, workdir: str | Path, tests_rel: str | list[str],
              timeout: int = 600) -> SuiteResult:
    """Run the target pytest suite; parse the one-line summary."""
    targets = [tests_rel] if isinstance(tests_rel, str) else list(tests_rel)
    try:
        proc = subprocess.run(
            [python, "-m", "pytest", *targets, "-q", "--tb=no", "-rf"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return SuiteResult(returncode=-1, summary="timeout",
                           tail=f"suite exceeded {timeout}s")
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    summary = ""
    # Collection crashes land on stderr; normal summaries on stdout.
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        found = COUNT_RE.findall(line)
        if found:
            summary = line.strip()
        for num, kind in found:
            key = "errors" if kind in ("error", "errors") else kind
            if key in counts:
                counts[key] = int(num)
    readable = proc.stdout.strip() or proc.stderr.strip()
    return SuiteResult(
        returncode=proc.returncode,
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["errors"],
        skipped=counts["skipped"],
        summary=summary,
        tail="\n".join(readable.splitlines()[-6:]),
    )


def run_fix_loop(python: str, target: str | Path, patches: list[Patch],
                 tests_rel: str | list[str],
                 timeout_per_run: int = 600) -> LoopResult:
    """Apply patches cumulatively, running the suite after each, to first green."""
    workdir = copy_target(target)
    result = LoopResult(workdir=str(workdir))
    applied: list[str] = []
    for i, patch in enumerate(patches):
        apply_patch(workdir, patch)
        _clear_pycache(workdir)
        applied.append(patch.id)
        suite = run_suite(python, workdir, tests_rel,
                          timeout=timeout_per_run)
        green = suite.returncode == 0
        result.iterations.append(LoopIteration(i, list(applied), suite, green))
        if green:
            result.green = True
            break
    _prune_old_workdirs(workdir)
    return result


PROPOSE_SYSTEM_PROMPT = """\
You are the ChainReaction fix proposer. Given a broken symbol, the file
that uses it, and the surrounding code, emit ONE minimal unified diff
that fixes exactly that use. Rules:
- Output a single JSON object: {"description": "...", "diff": "..."}.
- The diff must apply with `git apply -p1`: paths like
  a/nibabel/casting.py and b/nibabel/casting.py, relative to the target
  root, with at least 3 lines of context around every hunk.
- Fix ONLY the reported use; do not refactor, reformat, or touch tests.
- Blank context lines must contain a single space; added/removed lines
  start with +/-.
- Prefer the migration guide's recommended replacement."""


def propose_fix(symbol: str, evidence: str, target: str | Path = ".",
                patch_id: str = "P-auto") -> Patch:
    """Propose a Patch for a broken symbol via Muse Spark.

    evidence is "path/to/file.py:line" relative to the target root.
    Raises RuntimeError when MODEL_API_KEY is missing.
    """
    path, _, lineno = evidence.partition(":")
    src = Path(target) / path
    if not lineno.isdigit() or not src.is_file():
        raise ValueError(f"bad evidence, want file.py:line: {evidence!r}")
    center = int(lineno)
    lines = src.read_text(encoding="utf-8").splitlines()
    lo = max(1, center - 60)
    hi = min(len(lines), center + 60)
    excerpt = "\n".join(f"{i}: {lines[i - 1]}" for i in range(lo, hi + 1))
    user = (f"Broken symbol: {symbol}\nUse site: {evidence}\n\n"
            f"File excerpt (line-numbered):\n{excerpt}\n")
    data = extract_json(chat(PROPOSE_SYSTEM_PROMPT, user, max_tokens=2000))
    diff = data.get("diff", "")
    if "@@" not in diff:
        raise ValueError("model did not return a unified diff")
    return Patch(id=patch_id,
                 description=data.get("description", f"fix {symbol}"),
                 diff=diff)


FAIL_RE = re.compile(r"^FAILED (\S+)", re.MULTILINE)


def failed_tests(result: SuiteResult) -> list[str]:
    """Parse failing node ids from a suite run's short summary."""
    return FAIL_RE.findall(result.tail or "")


def run_auto_loop(python: str, target: str | Path,
                  tests_rel: str | list[str],
                  propose: Callable[[str], Patch], *,
                  timeout_per_run: int = 600,
                  max_rounds: int = 3) -> LoopResult:
    """Red-to-green loop: baseline -> propose one patch -> re-run to green.

    propose(first_failed_nodeid) returns the next Patch (e.g. a partial
    over propose_fix fed by the test map). One workdir for the whole loop;
    stops at the first green run. Records only patch iterations — the
    baseline run that proves red is the caller's SuiteResult.
    """
    workdir = copy_target(target)
    result = LoopResult(workdir=str(workdir))
    applied: list[str] = []
    suite = run_suite(python, workdir, tests_rel, timeout=timeout_per_run)
    for i in range(max_rounds):
        if suite.returncode == 0:
            result.green = True
            break
        failures = failed_tests(suite)
        patch = propose(failures[0] if failures else "")
        patch.id = f"P-r{i + 1}"
        apply_patch(workdir, patch)
        _clear_pycache(workdir)
        applied.append(patch.id)
        suite = run_suite(python, workdir, tests_rel,
                          timeout=timeout_per_run)
        green = suite.returncode == 0
        result.iterations.append(LoopIteration(i, list(applied), suite, green))
        result.green = green
        if green:
            break
    _prune_old_workdirs(workdir)
    return result


def main() -> int:  # `python -m chainreaction.fixloop --help`
    print("fixloop module: use run_fix_loop() or scripts/show_fixloop.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
