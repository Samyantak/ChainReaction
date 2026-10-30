"""Scorer: predicted (propagation) vs actual (verifier) comparison. No LLM."""

from dataclasses import dataclass, field

import networkx as nx

from chainreaction.graph.repo import relative_posix
from chainreaction.propagate import matches_symbol


@dataclass
class ChainScore:
    match: bool
    predicted: list[str] = field(default_factory=list)
    actual: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # predicted but not actual
    extra: list[str] = field(default_factory=list)  # actual but not predicted


@dataclass
class SetScore:
    precision: float
    recall: float
    predicted: set[str] = field(default_factory=set)
    actual: set[str] = field(default_factory=set)


def chain_match(predicted: list[str], actual: list[str]) -> ChainScore:
    return ChainScore(
        match=list(predicted) == list(actual),
        predicted=list(predicted),
        actual=list(actual),
        missing=[m for m in predicted if m not in actual],
        extra=[m for m in actual if m not in predicted],
    )


def precision_recall(predicted: set[str], actual: set[str]) -> SetScore:
    predicted, actual = set(predicted), set(actual)
    tp = len(predicted & actual)
    precision = tp / len(predicted) if predicted else 1.0
    recall = tp / len(actual) if actual else 1.0
    return SetScore(precision=precision, recall=recall,
                    predicted=predicted, actual=actual)


def direct_use_files(g: nx.DiGraph, broken: set[str], target: str
                   ) -> set[str]:
    """Repo-relative source files that use a broken symbol directly.

    Scores the files that need editing (the PR's answer), not every
    module the break propagates to — "everything breaks" trivially wins
    recall but predicts no edit set.
    """
    files = set()
    for _u, v, d in g.edges(data=True):
        if d.get("kind") != "USES_EXTERNAL":
            continue
        if matches_symbol(v, broken) is None:
            continue
        rel = relative_posix(d["file"], target)
        if "/tests/" not in f"/{rel}" and not rel.startswith("tests/"):
            files.add(rel)
    return files
