"""Scorer and baseline tests (pure + synthetic files)."""

import networkx as nx
import pytest

from chainreaction.baselines import _ruff_exe, grep_hits
from chainreaction.scorer import chain_match, precision_recall, predicted_files


def test_chain_match_exact_and_details():
    s = chain_match(["a", "b"], ["a", "b"])
    assert s.match is True and s.missing == [] and s.extra == []
    s = chain_match(["a", "b", "c"], ["a", "c"])
    assert s.match is False and s.missing == ["b"] and s.extra == []


def test_precision_recall():
    s = precision_recall({"a", "b", "c"}, {"b", "c", "d"})
    assert s.precision == pytest.approx(2 / 3)
    assert s.recall == pytest.approx(2 / 3)


def test_predicted_files_maps_hard_modules_to_repo_paths():
    g = nx.DiGraph()
    g.add_node("pkg.a", kind="module", file="targets/nibabel/nibabel/a.py")
    g.add_node("pkg.tests.test_a", kind="module",
               file="targets/nibabel/nibabel/tests/test_a.py")
    g.add_node("pkg.a.f", kind="function",
               file="targets/nibabel/nibabel/a.py")
    assert predicted_files(g, {"pkg.a", "pkg.tests.test_a", "pkg.a.f"},
                           "targets/nibabel") == {"nibabel/a.py"}


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
