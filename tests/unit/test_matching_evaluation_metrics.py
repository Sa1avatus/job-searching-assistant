import pytest

from evaluation.matching.metrics import (
    blocker_precision,
    ndcg_at_k,
    pearson_correlation,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    score_range_accuracy,
)


def test_ranking_metrics() -> None:
    ranked = ["a", "b", "c"]
    relevant = {"b", "c"}

    assert precision_at_k(ranked, relevant, 2) == 0.5
    assert recall_at_k(ranked, relevant, 2) == 0.5
    assert reciprocal_rank(ranked, relevant) == 0.5
    assert ndcg_at_k([0, 3, 1], 3) == pytest.approx(0.6443, abs=0.001)


def test_quality_metrics() -> None:
    assert pearson_correlation([10, 20, 30], [1, 2, 3]) == pytest.approx(1)
    assert blocker_precision([True, True, False], [True, False, True]) == 0.5
    assert score_range_accuracy([80, 30], [(70, 90), (0, 20)]) == 0.5


def test_irrelevant_in_top_k() -> None:
    from evaluation.matching.metrics import irrelevant_in_top_k

    ranked = ["a", "b", "c", "d"]
    irrelevant = {"c", "d"}

    assert irrelevant_in_top_k(ranked, irrelevant, 2) == 0
    assert irrelevant_in_top_k(ranked, irrelevant, 3) == 1
    assert irrelevant_in_top_k(ranked, irrelevant, 4) == 2


def test_relevant_rejected_count() -> None:
    from evaluation.matching.metrics import relevant_rejected_count

    assert relevant_rejected_count({"a", "b"}, {"b", "c"}) == 1
    assert relevant_rejected_count({"a", "b"}, {"c", "d"}) == 0
    assert relevant_rejected_count(set(), {"a"}) == 0
