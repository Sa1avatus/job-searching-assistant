from __future__ import annotations

import math
from collections.abc import Sequence


def precision_at_k(ranked_ids: Sequence[str], relevant_ids: set[str], k: int) -> float:
    _require_positive_k(k)
    selected = ranked_ids[:k]
    return sum(item in relevant_ids for item in selected) / k


def recall_at_k(ranked_ids: Sequence[str], relevant_ids: set[str], k: int) -> float:
    _require_positive_k(k)
    if not relevant_ids:
        return 1.0
    return sum(item in relevant_ids for item in ranked_ids[:k]) / len(relevant_ids)


def reciprocal_rank(ranked_ids: Sequence[str], relevant_ids: set[str]) -> float:
    for rank, item in enumerate(ranked_ids, start=1):
        if item in relevant_ids:
            return 1 / rank
    return 0.0


def ndcg_at_k(relevances: Sequence[float], k: int) -> float:
    _require_positive_k(k)
    observed = _discounted_gain(relevances[:k])
    ideal = _discounted_gain(sorted(relevances, reverse=True)[:k])
    return observed / ideal if ideal else 1.0


def pearson_correlation(actual: Sequence[float], expected: Sequence[float]) -> float:
    if len(actual) != len(expected) or len(actual) < 2:
        raise ValueError("Correlation requires equal sequences with at least two values")
    actual_mean = sum(actual) / len(actual)
    expected_mean = sum(expected) / len(expected)
    numerator = sum(
        (actual_value - actual_mean) * (expected_value - expected_mean)
        for actual_value, expected_value in zip(actual, expected, strict=True)
    )
    actual_variance = sum((value - actual_mean) ** 2 for value in actual)
    expected_variance = sum((value - expected_mean) ** 2 for value in expected)
    denominator = math.sqrt(actual_variance * expected_variance)
    return numerator / denominator if denominator else 0.0


def blocker_precision(predicted: Sequence[bool], expected: Sequence[bool]) -> float:
    if len(predicted) != len(expected):
        raise ValueError("Blocker labels must have equal lengths")
    predicted_positives = sum(predicted)
    if not predicted_positives:
        return 1.0 if not any(expected) else 0.0
    true_positives = sum(
        predicted_value and expected_value
        for predicted_value, expected_value in zip(predicted, expected, strict=True)
    )
    return true_positives / predicted_positives


def score_range_accuracy(scores: Sequence[float], ranges: Sequence[tuple[float, float]]) -> float:
    if len(scores) != len(ranges) or not scores:
        raise ValueError("Scores and expected ranges must have equal non-zero lengths")
    correct = sum(
        lower <= score <= upper
        for score, (lower, upper) in zip(scores, ranges, strict=True)
    )
    return correct / len(scores)


def _discounted_gain(relevances: Sequence[float]) -> float:
    return sum(
        (2**relevance - 1) / math.log2(rank + 1)
        for rank, relevance in enumerate(relevances, start=1)
    )


def _require_positive_k(k: int) -> None:
    if k <= 0:
        raise ValueError("k must be positive")


def irrelevant_in_top_k(ranked_ids: Sequence[str], irrelevant_ids: set[str], k: int) -> int:
    _require_positive_k(k)
    return sum(item in irrelevant_ids for item in ranked_ids[:k])


def relevant_rejected_count(
    relevant_ids: set[str], rejected_ids: set[str]
) -> int:
    return len(relevant_ids & rejected_ids)
