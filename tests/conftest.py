"""Shared pytest markers."""

import pytest

from chainreaction.verifier import target_python


def _missing_target_env(*envs):
    """First named env whose interpreter is missing, else None."""
    for env in envs:
        try:
            target_python(env)
        except FileNotFoundError as exc:
            return str(exc)
    return None


def requires_target_envs(*envs):
    """Skip the test when a named target env interpreter is missing.

    The live-pipeline tests need scripts/make_target_envs.py, which CI
    deliberately skips; gating on the interpreter keeps them green
    there while still running wherever the envs exist.
    """
    missing = _missing_target_env(*envs)
    return pytest.mark.skipif(
        missing is not None,
        reason=f"target env interpreter missing: {missing}",
    )
