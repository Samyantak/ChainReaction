"""Canned demo scenarios (key-free stubs; the interpreter fills change
sets live once MODEL_API_KEY exists). Single source of truth for the
backend, demo scripts, and (later) the UI scenario picker.
"""

from dataclasses import dataclass, field

from chainreaction.interpret import BreakDetail, ChangeItem

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


PR_1250_URL = "https://github.com/nipy/nibabel/pull/1250"
# Source (.py, non-test) files touched by the ground-truth PR.
PR_1250_FILES = [
    "nibabel/casting.py",
    "nibabel/conftest.py",
    "nibabel/ecat.py",
    "nibabel/freesurfer/io.py",
    "nibabel/nifti1.py",
    "nibabel/quaternions.py",
    "nibabel/spatialimages.py",
    "nibabel/streamlines/trk.py",
]


@dataclass
class Scenario:
    id: str
    decision: str
    target: str
    package: str
    guide_url: str
    stub_items: list[ChangeItem] = field(default_factory=list)
    peel_preamble: str = ""
    pr_url: str = ""
    pr_files: list[str] = field(default_factory=list)
    # Decision probes replace the import probe for non-version decisions;
    # "" keeps the classic import/peeled/before layers.
    probe_code: str = ""
    verify_env: str = "numpy2"
    before_env: str = "numpy1"  # "" = no before layer
    grep_patterns: list[str] | None = None  # None = numpy defaults
    run_ruff: bool = True


NIBABEL_NUMPY2 = Scenario(
    id="nibabel-numpy2",
    decision="upgrade numpy to 2.0",
    target="targets/nibabel",
    package="nibabel",
    guide_url="https://numpy.org/doc/stable/numpy_2_0_migration_guide.html",
    pr_url=PR_1250_URL,
    pr_files=list(PR_1250_FILES),
    stub_items=[
        ChangeItem("numpy.sctypes", "removed",
                   "sctypes Access dtypes explicitly instead.",
                   replacement="Access dtypes explicitly instead."),
        ChangeItem("numpy.maximum_sctype", "removed",
                   "maximum_sctype Use a specific dtype instead.",
                   replacement="Use a specific dtype instead."),
    ],
    peel_preamble=PEEL_SCTYPES,
)

# Exercises the Meta switch without network or cost: tmp dirs, :memory: db.
# Fails at the first blocker (provider dispatch rejects "meta").
META_PROBE = """\
import sys
import tempfile
sys.path.insert(0, "src")
tmp = tempfile.mkdtemp()
from sponsorfit.llm import MeteredLLMClient
client = MeteredLLMClient(run_id="probe", db_path=":memory:",
                          traces_dir=tmp)
client.call(provider="meta", model="muse-spark-1.3",
            messages=[{"role": "user", "content": "ping"}])
"""

SPONSORFIT_META = Scenario(
    id="sponsorfit-meta",
    decision="switch to Meta Model API",
    target="targets/sponsorfit",
    package="sponsorfit",
    # Vendor guide URL pending — stub quotes recorded by hand.
    guide_url="",
    stub_items=[
        ChangeItem("sponsorfit.llm.Provider", "rejected_value",
                   "call the muse-spark-1.3 model by name",
                   detail=BreakDetail('"meta" in Provider',
                                      'Literal["anthropic","openai","ollama"]')),
        ChangeItem("sponsorfit.llm.MeteredLLMClient._call_openai",
                   "missing_param",
                   "Set the base URL to https://api.meta.ai/v1",
                   detail=BreakDetail("base_url=https://api.meta.ai/v1",
                                      "openai.OpenAI() with no base_url")),
        ChangeItem("sponsorfit.pricing.PRICING", "missing_key",
                   "bill meta/muse-spark-1.3 usage against the meta table",
                   detail=BreakDetail('PRICING["meta"]["muse-spark-1.3"]',
                                      "KeyError: no meta provider table")),
    ],
    probe_code=META_PROBE,
    verify_env="sponsorfit",
    before_env="",
    grep_patterns=[r"Provider", r"PRICING", r"_call_openai"],
    run_ruff=False,
)

SCENARIOS = {s.id: s for s in [NIBABEL_NUMPY2, SPONSORFIT_META]}


def get(scenario_id: str) -> Scenario:
    try:
        return SCENARIOS[scenario_id]
    except KeyError:
        raise KeyError(f"unknown scenario: {scenario_id!r}") from None
