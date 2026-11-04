"""Scorer and baseline tests (pure + synthetic files)."""

import networkx as nx
import pytest

from chainreaction.baselines import _ruff_exe, grep_hits
from chainreaction.scorer import (
    chain_match,
    direct_use_files,
    precision_recall,
    used_breaking_symbols,
)


def test_chain_match_exact_and_details():
    s = chain_match(["a", "b"], ["a", "b"])
    assert s.match is True and s.missing == [] and s.extra == []
    s = chain_match(["a", "b", "c"], ["a", "c"])
    assert s.match is False and s.missing == ["b"] and s.extra == []


def test_chain_match_empty_is_never_match_when_failed():
    assert chain_match([], []).match is True
    assert chain_match([], [], failed=True).match is False
    assert chain_match(["a"], ["a"], failed=True).match is True
    assert chain_match(["a"], [], failed=True).match is False


def test_precision_recall():
    s = precision_recall({"a", "b", "c"}, {"b", "c", "d"})
    assert s.precision == pytest.approx(2 / 3)
    assert s.recall == pytest.approx(2 / 3)


def test_direct_use_files_scores_symbol_uses_not_cascade():
    g = nx.DiGraph()
    g.add_node("pkg.a", kind="module", file="t/pkg/a.py")
    g.add_node("pkg.b", kind="module", file="t/pkg/b.py")
    g.add_edge("pkg.a", "numpy.sctypes", kind="USES_EXTERNAL",
               file="t/pkg/a.py", line=3)
    g.add_edge("pkg.b", "numpy.sctypes", kind="USES_EXTERNAL",
               file="t/pkg/tests/test_b.py", line=3)  # tests excluded
    g.add_edge("pkg.a", "numpy.other", kind="USES_EXTERNAL",
               file="t/pkg/a.py", line=9)  # unmatched symbol
    assert direct_use_files(g, {"numpy.sctypes"}, "t") == {"pkg/a.py"}


def test_used_breaking_symbols_matches_prefix_and_uses():
    g = nx.DiGraph()
    g.add_edge("pkg.a", "numpy.sctypes", kind="USES_EXTERNAL",
               file="t/pkg/a.py", line=3)
    g.add_edge("pkg.a", "numpy.compat.py3k.asbytes", kind="USES_EXTERNAL",
               file="t/pkg/a.py", line=9)
    g.add_edge("pkg.a", "numpy.other", kind="USES_EXTERNAL",
               file="t/pkg/a.py", line=11)
    assert used_breaking_symbols(
        g, {"numpy.sctypes", "numpy.compat", "numpy.missing"}) == {
        "numpy.sctypes", "numpy.compat"}


def test_grep_hits_finds_numpy2_patterns(tmp_path):
    (tmp_path / "a.py").write_text(
        "x = np.sctypes['float']\ny = 1\nz = np.float_(1.0)\n", encoding="utf-8"
    )
    (tmp_path / "b.py").write_text("y = 1\n", encoding="utf-8")
    hits = grep_hits(tmp_path)
    assert [(h.file.endswith("a.py"), h.line) for h in hits] == [
        (True, 1),
        (True, 3),
    ]
    assert {h.pattern for h in hits} == {r"np\.sctypes", r"np\.float_"}


@pytest.mark.skipif(_ruff_exe() is None, reason="ruff not installed")
def test_ruff_baseline_smoke(tmp_path):
    from chainreaction.baselines import ruff_npy201

    (tmp_path / "a.py").write_text("import numpy as np\nx = np.sctypes\n")
    hits = ruff_npy201(tmp_path)
    assert hits is not None and any(h.file.endswith("a.py") for h in hits)


def test_patterns_for_changeset_derives_shorts():
    from chainreaction.baselines import patterns_for_changeset
    from chainreaction.interpret import ChangeItem, ChangeSet

    cs = ChangeSet("d", "u", [
        ChangeItem("numpy.sctypes", "removed", "q", verified=True),
        ChangeItem("numpy.ndarray.newbyteorder", "removed", "q",
                   verified=True),
        ChangeItem("fit.llm.Provider", "rejected_value", "q", verified=True),
        ChangeItem("numpy.unverified", "removed", "q", verified=False),
        ChangeItem("numpy.trapz", "deprecated", "q", verified=True),
        ChangeItem("meta.voice", "changed", "q", verified=True),
    ])
    assert patterns_for_changeset(cs) == [
        r"\bProvider\b", r"\bnewbyteorder\b", r"\bsctypes\b"]


def test_grep_hits_empty_list_searches_nothing(tmp_path):
    from chainreaction.baselines import grep_hits

    (tmp_path / "a.py").write_text("x = np.sctypes\n", encoding="utf-8")
    assert grep_hits(tmp_path, []) == []
    assert grep_hits(tmp_path) != []  # None keeps numpy defaults


def test_ruff_npy201_replays_without_exe(monkeypatch):
    from types import SimpleNamespace

    from chainreaction import baselines
    from chainreaction.baselines import ruff_npy201

    def _boom():
        raise AssertionError("injected runs must not resolve ruff")

    monkeypatch.setattr(baselines, "_ruff_exe", _boom)
    run = lambda cmd, cwd: SimpleNamespace(  # noqa: E731 - tiny fake
        stdout="pkg/a.py:3:9: NPY201 numpy.sctypes\n", missing=None)
    hits = ruff_npy201("pkg", run=run)
    assert [(h.file, h.line) for h in hits] == [("pkg/a.py", 3)]
