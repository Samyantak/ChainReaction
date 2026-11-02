"""Propagation engine tests on a synthetic package fixture."""

from pathlib import Path

import pytest

from chainreaction.graph.repo import build_graph_from_repo
from chainreaction.interpret import ChangeItem
from chainreaction.propagate import (
    Impact,
    failure_path,
    order_config_by_entry,
    predicted_chain,
    probe_entries,
    propagate,
    resolve_symbol,
    vendor_chain,
)


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


def test_lazy_module_hard_via_parent_uses_stay_at_risk(tmp_path):
    g = build_graph_from_repo(_pkg(tmp_path), "demo")
    impacts = propagate(g, {"numpy.sctypes"})

    assert impacts["demo.lazy.load"].status == "AT_RISK"
    assert impacts["demo.lazy.risky"].status == "AT_RISK"
    assert impacts["demo.lazy.wrapper"].status == "AT_RISK"
    # lazy's own code is clean, but importing it executes the broken
    # demo/__init__ first, so the module itself is HARD.
    assert impacts["demo.lazy"].status == "HARD"
    assert impacts["demo.lazy"].via[0][:2] == ("demo.lazy", "demo")


def test_submodule_under_hard_package_is_hard(tmp_path):
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from . import leaf\n", encoding="utf-8")
    (pkg / "leaf.py").write_text(
        "import numpy as np\nOK = np.sctypes['float']\n", encoding="utf-8")
    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("", encoding="utf-8")
    (sub / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    g = build_graph_from_repo(tmp_path, "demo")
    assert g.edges["demo.sub.mod", "demo"]["kind"] == "PARENT_INIT"
    impacts = propagate(g, {"numpy.sctypes"})
    assert impacts["demo.sub.mod"].status == "HARD"
    assert impacts["demo.sub.mod"].via[0][:2] == ("demo.sub.mod", "demo")


def test_failure_path_skips_cascade_blocked_modules(tmp_path):
    # clean is imported FIRST but only cascade-HARD (its own code is
    # fine); the walk must skip it — the executing root won't re-fail.
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from . import clean\nfrom . import leaf\n", encoding="utf-8")
    (pkg / "clean.py").write_text("VALUE = 1\n", encoding="utf-8")
    (pkg / "leaf.py").write_text(
        "import numpy as np\nOK = np.sctypes['float']\n", encoding="utf-8")
    g = build_graph_from_repo(tmp_path, "demo")
    impacts = propagate(g, {"numpy.sctypes"})
    assert impacts["demo.clean"].status == "HARD"
    hops = failure_path(g, impacts, {"numpy.sctypes"}, "demo")
    assert predicted_chain(g, hops) == ["demo", "demo.leaf"]


def test_config_items_seed_and_resolve(tmp_path):
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "svc.py").write_text(
        "TABLE = {}\n"
        "class Client:\n"
        "    def run(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    (pkg / "agent.py").write_text(
        "from .svc import Client\n"
        "def act(client: Client):\n"
        "    return client.run()\n",
        encoding="utf-8",
    )
    g = build_graph_from_repo(tmp_path, "demo")
    assert resolve_symbol(g, "demo", "demo.svc.TABLE") == "demo.svc"
    assert resolve_symbol(g, "demo", "demo.svc.Client.run") == \
        "demo.svc.Client.run"
    with pytest.raises(ValueError, match="unresolvable"):
        resolve_symbol(g, "demo", "demo.nope.deeper")
    items = [ChangeItem("demo.svc.TABLE", "missing_key", "q"),
             ChangeItem("demo.svc.Client.run", "rejected_value", "q")]
    impacts = propagate(g, set(), items, "demo")
    assert impacts["demo.svc"].status == "AT_RISK"
    assert impacts["demo.svc.Client.run"].status == "AT_RISK"
    assert impacts["demo.agent.act"].status == "AT_RISK"
    assert "demo" not in impacts  # config breaks are never import breaks


def test_failure_path_skips_transitive_cascade_block(tmp_path):
    # mid is DIRECTLY hard (imports inner) but inner only fails via the
    # executing root — mid must be skipped like clean was.
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from . import mid\nfrom . import leaf\n", encoding="utf-8")
    (pkg / "mid.py").write_text("from . import inner\n", encoding="utf-8")
    (pkg / "inner.py").write_text("VALUE = 1\n", encoding="utf-8")
    (pkg / "leaf.py").write_text(
        "import numpy as np\nOK = np.sctypes['float']\n", encoding="utf-8")
    g = build_graph_from_repo(tmp_path, "demo")
    impacts = propagate(g, {"numpy.sctypes"})
    assert impacts["demo.mid"].status == "HARD"
    hops = failure_path(g, impacts, {"numpy.sctypes"}, "demo")
    assert predicted_chain(g, hops) == ["demo", "demo.leaf"]


def test_failure_path_ignores_parent_init_race_in_via(tmp_path):
    # mid's recorded via may show only the ancestor-init cause (closure
    # order artifact) even though mid genuinely fails via leaf: the walk
    # must re-evaluate structurally and still route through mid, not boom.
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from . import mid\nfrom . import boom\n", encoding="utf-8")
    (pkg / "mid.py").write_text("from . import leaf\n", encoding="utf-8")
    (pkg / "leaf.py").write_text(
        "import numpy as np\nOK = np.sctypes['float']\n", encoding="utf-8")
    (pkg / "boom.py").write_text(
        "import numpy as np\nOK = np.sctypes['float']\n", encoding="utf-8")
    g = build_graph_from_repo(tmp_path, "demo")
    impacts = propagate(g, {"numpy.sctypes"})
    assert g.edges["demo.mid", "demo"]["kind"] == "PARENT_INIT"
    mid = impacts["demo.mid"]
    impacts["demo.mid"] = Impact(
        "HARD", mid.symbol,
        [("demo.mid", "demo", str(pkg / "mid.py"), 1)] + impacts["demo"].via)
    hops = failure_path(g, impacts, {"numpy.sctypes"}, "demo")
    assert predicted_chain(g, hops) == ["demo", "demo.mid", "demo.leaf"]


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


def _vendor_pkg(tmp_path):
    pkg = tmp_path / "fit"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from . import llm\n", encoding="utf-8")
    (pkg / "llm.py").write_text(
        "from typing import Literal\n"
        "Provider = Literal[\"a\"]\n"
        "class Client:\n"
        "    def call(self, provider):\n"
        "        if provider == \"a\":\n"
        "            return self._do()\n"
        "        raise ValueError(provider)\n"
        "    def _do(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    (pkg / "app.py").write_text(
        "from .llm import Client\n"
        "def run(client: Client):\n"
        "    return client.call(\"x\")\n",
        encoding="utf-8",
    )
    (pkg / "util.py").write_text(
        "def helper():\n    return 0\n", encoding="utf-8")
    return tmp_path


_VENDOR_PROBE = (
    "from fit.llm import Client\n"
    "client = Client()\n"
    "client.call(\"meta\")\n"
)


def _vendor_items():
    return [
        ChangeItem("fit.llm.Provider", "rejected_value", "", verified=True,
                   source="code", code_quote='Provider = Literal["a"]',
                   code_file="fit/llm.py", code_line=2),
        ChangeItem("fit.llm.Client._do", "missing_param", "", verified=True,
                   source="code", code_quote="return 1",
                   code_file="fit/llm.py", code_line=9),
        ChangeItem("fit.util.helper", "missing_key", "", verified=True,
                   source="code", code_quote="return 0",
                   code_file="fit/util.py", code_line=2),
    ]


def test_probe_entries_finds_constructed_method_calls(tmp_path):
    g = build_graph_from_repo(_vendor_pkg(tmp_path), "fit")
    assert probe_entries(_VENDOR_PROBE, "fit", g) == [
        "fit.llm.Client", "fit.llm.Client.call"]
    assert probe_entries("", "fit", g) == []
    assert probe_entries("def broken(:", "fit", g) == []
    assert probe_entries("import os\nos.getcwd()\n", "fit", g) == []


def test_vendor_reached_config_fails_module_hard(tmp_path):
    g = build_graph_from_repo(_vendor_pkg(tmp_path), "fit")
    entries = probe_entries(_VENDOR_PROBE, "fit", g)
    impacts = propagate(g, set(), _vendor_items(), "fit",
                        entries=tuple(entries))
    assert impacts["fit.llm"].status == "HARD"
    assert impacts["fit.llm.Client._do"].status == "AT_RISK"
    assert impacts["fit.llm.Client.call"].status == "AT_RISK"
    assert impacts["fit.app.run"].status == "AT_RISK"
    # Call-time breaks never fail importers: app imports llm at module
    # level and stays clean.
    assert "fit.app" not in impacts
    assert "fit" not in impacts
    # Unreached config stays a guess, never HARD.
    assert impacts["fit.util.helper"].status == "AT_RISK"
    assert "fit.util" not in impacts


def test_vendor_config_without_entries_stays_at_risk(tmp_path):
    g = build_graph_from_repo(_vendor_pkg(tmp_path), "fit")
    impacts = propagate(g, set(), _vendor_items(), "fit")
    assert impacts["fit.llm"].status == "AT_RISK"
    assert impacts["fit.llm.Client._do"].status == "AT_RISK"
    assert not [n for n, i in impacts.items() if i.status == "HARD"]


def test_vendor_order_and_chain_hit_provider_first(tmp_path):
    g = build_graph_from_repo(_vendor_pkg(tmp_path), "fit")
    entries = probe_entries(_VENDOR_PROBE, "fit", g)
    ordered = order_config_by_entry(g, entries, _vendor_items(), "fit")
    assert [i.symbol for i in ordered] == [
        "fit.llm.Provider", "fit.llm.Client._do", "fit.util.helper"]
    hops = vendor_chain(g, "fit", entries, ordered)
    assert hops == [("fit.llm.Client.call", "fit.llm", "fit/llm.py", 2)]
    assert predicted_chain(g, hops) == ["fit.llm"]
    assert vendor_chain(g, "fit", [], ordered) == []
