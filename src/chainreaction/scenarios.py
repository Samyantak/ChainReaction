"""Canned demo scenarios (key-free stubs; the interpreter fills change
sets live once MODEL_API_KEY exists). Single source of truth for the
backend, demo scripts, and (later) the UI scenario picker.
"""

from dataclasses import dataclass, field

from chainreaction.interpret import ChangeItem

PEEL_SCTYPES = """\
import numpy as np
if not hasattr(np, "sctypes"):
    np.sctypes = {
        "float": [np.float16, np.float32, np.float64, np.longdouble],
        "int": [np.int8, np.int16, np.int32, np.int64],
        "uint": [np.uint8, np.uint16, np.uint32, np.uint64],
        "complex": [np.complex64, np.complex128, np.clongdouble],
        "bool": [np.bool_],
        "object": [np.object_],
    }
"""


@dataclass
class Scenario:
    id: str
    decision: str
    target: str
    package: str
    guide_url: str
    stub_items: list[ChangeItem] = field(default_factory=list)
    peel_preamble: str = ""


NIBABEL_NUMPY2 = Scenario(
    id="nibabel-numpy2",
    decision="upgrade numpy to 2.0",
    target="targets/nibabel",
    package="nibabel",
    guide_url="https://numpy.org/doc/stable/numpy_2_0_migration_guide.html",
    stub_items=[
        ChangeItem("numpy.sctypes", "removed",
                   "`np.sctypes` was removed in the NumPy 2.0 release."),
        ChangeItem("numpy.maximum_sctype", "removed",
                   "`np.maximum_sctype` was removed in the NumPy 2.0 release."),
    ],
    peel_preamble=PEEL_SCTYPES,
)

SCENARIOS = {s.id: s for s in [NIBABEL_NUMPY2]}


def get(scenario_id: str) -> Scenario:
    try:
        return SCENARIOS[scenario_id]
    except KeyError:
        raise KeyError(f"unknown scenario: {scenario_id!r}") from None
