"""Propagation engine tests on a synthetic package fixture."""

from pathlib import Path

from chainreaction.graph.repo import build_graph_from_repo
from chainreaction.propagate import propagate


def _pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from . import mid\nfrom . import lazy\n", encoding="utf-8"
    )
    (pkg / "mid.py").write_text("from . import leaf\n", encoding="utf-8")
    (pkg / "leaf.py").write_text(
        "import numpy as np\n"
        "\n"
        "def ok():\n"
        "    return np.sctypes['float'][:]\n"
        "\n"
        "OK = ok()\n",
        encoding="utf-8",
    )
    (pkg / "lazy.py").write_text(
        "def load():\n"
        "    from . import leaf\n"
        "    return leaf\n"
        "\n"
        "def risky():\n"
        "    import numpy as np\n"
        "    return np.sctypes['int']\n"
        "\n"
        "def wrapper():\n"
        "    return risky()\n",
        encoding="utf-8",
    )
    return tmp_path


def test_hard_cascade_through_importers(tmp_path):
    g = build_graph_from_repo(_pkg(tmp_path), "demo")
    impacts = propagate(g, {"numpy.sctypes"})

    assert impacts["demo.leaf"].status == "HARD"
    assert impacts["demo.mid"].status == "HARD"
    assert impacts["demo"].status == "HARD"

    via = impacts["demo.leaf"].via
    assert via[0][:2] == ("demo.leaf", "demo.leaf.ok")
    assert via[0][3] == 6  # OK = ok()
    assert via[-1][:2] == ("demo.leaf.ok", "numpy.sctypes")
    assert via[-1][3] == 4  # np.sctypes use
    assert all(hop[2].endswith("leaf.py") for hop in via)


def test_lazy_importer_and_untriggered_use_stay_at_risk(tmp_path):
    g = build_graph_from_repo(_pkg(tmp_path), "demo")
    impacts = propagate(g, {"numpy.sctypes"})

    assert impacts["demo.lazy.load"].status == "AT_RISK"
    assert impacts["demo.lazy.risky"].status == "AT_RISK"
    assert impacts["demo.lazy.wrapper"].status == "AT_RISK"
    assert "demo.lazy" not in impacts  # importing lazy triggers nothing


def test_unknown_symbol_marks_nothing(tmp_path):
    g = build_graph_from_repo(_pkg(tmp_path), "demo")
    assert propagate(g, {"numpy.nope"}) == {}
