"""UI-facing API endpoints (M6 §11), kept in one router so web.py stays
a thin composition root.

All read endpoints work in replay with no key. Responses stay tolerant
of recordings made before a field existed: the UI hides sections whose
data is absent rather than crashing (M6 plan point 3).
"""

from fastapi import APIRouter

from chainreaction import __version__, llm as llm_mod

router = APIRouter()


@router.get("/api/status")
def ui_status() -> dict:
    """Key presence, live-mode availability, and build version."""
    key_present = llm_mod.has_key()
    return {
        "key_present": key_present,
        "live_available": key_present,
        "version": __version__,
    }
