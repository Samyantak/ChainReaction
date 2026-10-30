"""Propagation engine tests on a synthetic package fixture."""

from pathlib import Path

from chainreaction.graph.repo import build_graph_from_repo
from chainreaction.propagate import failure_path, predicted_chain, propagate


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


def _methods(tmp_path):
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text(
        "from .b import helper\n"
        "def caller():\n"
        "    return helper()\n",
        encoding="utf-8",
    )
    (pkg / "b.py").write_text(
        "import numpy as np\n"
        "def helper():\n"
        "    return np.sctypes['int']\n"
        "class C:\n"
        "    def chelp(self):\n"
        "        return np.sctypes['uint']\n"
        "    def method(self):\n"
        "        return self.chelp()\n"
        "class D:\n"
        "    def chelp(self):\n"
        "        return 0\n",
        encoding="utf-8",
    )
    return tmp_path


def test_cross_module_and_self_calls_propagate(tmp_path):
    g = build_graph_from_repo(_methods(tmp_path), "demo")
    impacts = propagate(g, {"numpy.sctypes"})
    assert impacts["demo.a.caller"].status == "AT_RISK"
    assert impacts["demo.b.C.method"].status == "AT_RISK"
    assert "demo.b.D.chelp" not in impacts  # same name, other class: clean


def test_failure_path_follows_execution_order(tmp_path):
    g = build_graph_from_repo(_pkg(tmp_path), "demo")
    impacts = propagate(g, {"numpy.sctypes"})
    hops = failure_path(g, impacts, {"numpy.sctypes"}, "demo")
    assert predicted_chain(g, hops) == ["demo", "demo.mid", "demo.leaf"]
    assert hops[-1][:2] == ("demo.leaf.ok", "numpy.sctypes")
    assert all(g.edges.get((u, v)) is not None for u, v, _, _ in hops)


def test_failure_path_prefers_first_failing_import(tmp_path):
    # __init__ imports slow (2-hop failure) BEFORE fast (1-hop failure):
    # runtime fails through slow even though fast is the shorter path.
    pkg = tmp_path / "race"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from . import slow\nfrom . import fast\n", encoding="utf-8")
    (pkg / "slow.py").write_text("from . import leaf\n", encoding="utf-8")
    (pkg / "fast.py").write_text("from . import leaf\n", encoding="utf-8")
    (pkg / "leaf.py").write_text(
        "import numpy as np\nOK = np.sctypes['float']\n", encoding="utf-8")
    g = build_graph_from_repo(tmp_path, "race")
    impacts = propagate(g, {"numpy.sctypes"})
    assert impacts["race"].status == "HARD"
    hops = failure_path(g, impacts, {"numpy.sctypes"}, "race")
    assert predicted_chain(g, hops) == ["race", "race.slow", "race.leaf"]


def test_failure_path_empty_when_package_clean(tmp_path):
    g = build_graph_from_repo(_pkg(tmp_path), "demo")
    assert failure_path(g, propagate(g, {"numpy.nope"}),
                        {"numpy.nope"}, "demo") == []
    assert predicted_chain(g, []) == []
