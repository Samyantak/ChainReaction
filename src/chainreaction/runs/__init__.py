"""Job runner, run store, and honest replay (Milestone 1).

Every analysis is a background run: stages communicate only through
`RunContext` gateways (`emit`/`llm`/`run_cmd`/`fetch`/`save_artifact`),
and every gateway call is recorded. Replay re-executes the same stages
against the recordings — no network, no key — and fails loudly on any
divergence (`ReplayMiss`).
"""

from chainreaction.runs.context import Cancelled, CommitMismatch, ReplayMiss, RunContext
from chainreaction.runs.stages import STAGES
from chainreaction.runs.store import (
    ACTIVE,
    TERMINAL,
    list_runs,
    preset_dir,
    presets_dir,
    run_dir,
    runs_dir,
)
from chainreaction.runs.worker import (
    RunRequest,
    cancel_run,
    create_run,
    get_handle,
    startup_sweep,
    submit_run,
)

__all__ = [
    "ACTIVE",
    "STAGES",
    "TERMINAL",
    "Cancelled",
    "CommitMismatch",
    "ReplayMiss",
    "RunContext",
    "RunRequest",
    "cancel_run",
    "create_run",
    "get_handle",
    "list_runs",
    "preset_dir",
    "presets_dir",
    "run_dir",
    "runs_dir",
    "startup_sweep",
    "submit_run",
]
