"""Ranking metrics on graded human labels (pure Python, no numpy).

``gains`` are the human gains in the order a ranker returned the items (best first):
0 = not relevant, 1 = maybe, 2 = relevant. Binary metrics treat gain >= ``relevant_gain`` as a
hit. Groups with nothing relevant have no defined recall/MRR and are reported as None so an
empty group can never look like a perfect or a failed ranking.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

RELEVANT_GAIN = 2


def _dcg(gains: Sequence[int], k: int) -> float:
    return float(sum((2**gain - 1) / math.log2(rank + 2) for rank, gain in enumerate(gains[:k])))


def ndcg_at_k(gains: Sequence[int], k: int = 10) -> float | None:
    """Normalised DCG with exponential gain; None when no item has a positive gain."""
    ideal = _dcg(sorted(gains, reverse=True), k)
    if ideal == 0:
        return None
    return _dcg(gains, k) / ideal


def precision_at_k(
    gains: Sequence[int], k: int = 10, relevant_gain: int = RELEVANT_GAIN
) -> float | None:
    top = gains[:k]
    if not top:
        return None
    return sum(1 for g in top if g >= relevant_gain) / len(top)


def recall_at_k(
    gains: Sequence[int], k: int = 10, relevant_gain: int = RELEVANT_GAIN
) -> float | None:
    total = sum(1 for g in gains if g >= relevant_gain)
    if total == 0:
        return None
    return sum(1 for g in gains[:k] if g >= relevant_gain) / total


def reciprocal_rank(gains: Sequence[int], relevant_gain: int = RELEVANT_GAIN) -> float | None:
    if not any(g >= relevant_gain for g in gains):
        return None
    for rank, gain in enumerate(gains, start=1):
        if gain >= relevant_gain:
            return 1.0 / rank
    return None


def mean_defined(values: Sequence[float | None]) -> float | None:
    defined = [v for v in values if v is not None]
    return sum(defined) / len(defined) if defined else None


def order_by_scores(gains: Sequence[int], scores: Sequence[float], ids: Sequence[str]) -> list[int]:
    """Gains re-ordered by descending score; ties broken by id so results are deterministic."""
    order = sorted(range(len(gains)), key=lambda i: (-scores[i], ids[i]))
    return [gains[i] for i in order]
