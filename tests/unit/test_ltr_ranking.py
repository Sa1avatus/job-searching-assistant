"""LTR metrics, baseline ranker, benchmark and artifact guards (stage 3A)."""

import json
import math
import random

import pytest

from app.matching.cross_encoder.ltr_feature_contract import LTR_FEATURE_NAMES
from app.matching.ltr.baseline import (
    CurrentPipelineRanker,
    LogisticRanker,
    RankedItem,
    RankingGroup,
    benchmark,
)
from app.matching.ltr.metrics import (
    mean_defined,
    ndcg_at_k,
    order_by_scores,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from app.matching.ltr.schema import (
    FEATURE_SCHEMA_VERSION,
    ModelArtifact,
    ModelArtifactError,
    feature_schema_hash,
)

# -- metrics -----------------------------------------------------------------


def test_ndcg_is_one_for_the_ideal_order_and_lower_otherwise() -> None:
    assert ndcg_at_k([2, 1, 0, 0]) == pytest.approx(1.0)
    assert 0 < ndcg_at_k([0, 0, 1, 2]) < ndcg_at_k([1, 2, 0, 0]) < 1.0


def test_ndcg_matches_a_hand_computed_value() -> None:
    # gains [0, 2] -> DCG = 3 / log2(3); ideal [2, 0] -> 3 / log2(2)
    assert ndcg_at_k([0, 2]) == pytest.approx((3 / math.log2(3)) / 3)


def test_ndcg_respects_k() -> None:
    assert ndcg_at_k([0, 0, 2], k=2) == pytest.approx(0.0)


def test_metrics_are_undefined_without_a_relevant_item_not_zero() -> None:
    assert ndcg_at_k([0, 0, 0]) is None
    assert recall_at_k([0, 1, 1]) is None  # 'maybe' is not a hit
    assert reciprocal_rank([1, 0, 1]) is None
    assert precision_at_k([]) is None


def test_precision_recall_and_mrr() -> None:
    gains = [0, 2, 1, 2, 0]

    assert precision_at_k(gains, k=2) == 0.5
    assert recall_at_k(gains, k=2) == 0.5
    assert reciprocal_rank(gains) == 0.5
    assert mean_defined([None, 1.0, 0.0]) == 0.5 and mean_defined([None]) is None


def test_ties_are_ordered_by_id_so_results_are_deterministic() -> None:
    assert order_by_scores([2, 0, 1], [1.0, 1.0, 1.0], ["b", "c", "a"]) == [1, 2, 0]


# -- baseline and benchmark --------------------------------------------------


def _group(gid: str, n: int, seed: int, informative: bool = True) -> RankingGroup:
    rng = random.Random(seed)
    items = []
    for i in range(n):
        gain = rng.choice([0, 0, 1, 2])
        signal = gain / 2 + rng.gauss(0, 0.1)
        features = dict.fromkeys(LTR_FEATURE_NAMES)
        features["required_coverage"] = signal if informative else rng.random()
        features["hard_skill_coverage"] = signal if informative else rng.random()
        items.append(
            RankedItem(
                vacancy_id=f"{gid}-{i:03d}",
                features=features,
                gain=gain,
                baseline_score=rng.random() * 100,  # a production score unrelated to the labels
            )
        )
    return RankingGroup(gid, tuple(items))


def test_logistic_ranker_learns_a_signal_and_handles_missing_features() -> None:
    train = [_group(f"t{i}", 40, i) for i in range(5)]
    ranker = LogisticRanker(epochs=200)
    ranker.fit(train)
    group = _group("eval", 40, 99)

    scores = ranker.score(group)

    gains = [item.gain for item in group.items]
    ordered = order_by_scores(gains, scores, [i.vacancy_id for i in group.items])
    assert ndcg_at_k(ordered, 10) > 0.9
    assert all(math.isfinite(s) for s in scores)  # None features were imputed, not NaN


def test_logistic_ranker_refuses_to_fit_on_nothing_or_score_unfitted() -> None:
    with pytest.raises(ValueError):
        LogisticRanker().fit([])
    with pytest.raises(RuntimeError):
        LogisticRanker().score(_group("g", 3, 1))


def test_parameters_round_trip() -> None:
    ranker = LogisticRanker(epochs=50)
    ranker.fit([_group("t", 30, 3)])
    clone = LogisticRanker.from_parameters(ranker.parameters(), ranker.feature_names)

    group = _group("e", 10, 4)
    assert clone.score(group) == pytest.approx(ranker.score(group))


def test_benchmark_declares_a_win_only_with_enough_evaluation_data() -> None:
    train = [_group(f"t{i}", 40, i) for i in range(5)]
    big_eval = [_group(f"e{i}", 40, 100 + i) for i in range(3)]
    tiny_eval = [_group("e", 8, 200), _group("f", 8, 201)]

    strong = benchmark(
        train, big_eval, [CurrentPipelineRanker(), LogisticRanker()], fold="validation"
    )
    weak = benchmark(
        train, tiny_eval, [CurrentPipelineRanker(), LogisticRanker()], fold="validation"
    )

    assert strong.verdict["logistic_regression"] == "challenger_beats_baseline"
    assert strong.deltas["logistic_regression"]["ndcg_at_10"] > 0
    assert weak.verdict["logistic_regression"] == "insufficient_evidence"


def test_a_challenger_that_does_not_beat_the_baseline_is_reported_as_such() -> None:
    train = [_group(f"t{i}", 40, i, informative=False) for i in range(5)]
    # the production score is perfectly aligned with the labels here
    evaluation = []
    for i in range(3):
        base = _group(f"e{i}", 40, 300 + i, informative=False)
        evaluation.append(
            RankingGroup(
                base.group_id,
                tuple(
                    RankedItem(x.vacancy_id, x.features, x.gain, baseline_score=float(x.gain))
                    for x in base.items
                ),
            )
        )

    report = benchmark(
        train, evaluation, [CurrentPipelineRanker(), LogisticRanker()], fold="validation"
    )

    assert report.verdict["logistic_regression"] == "baseline_holds"


def test_benchmark_ignores_single_item_groups() -> None:
    report = benchmark(
        [_group("t", 30, 1)],
        [_group("solo", 1, 2), _group("e", 30, 3), _group("f", 30, 4)],
        [CurrentPipelineRanker(), LogisticRanker()],
        fold="validation",
    )

    assert report.n_groups == 2


# -- artifact guards ---------------------------------------------------------


def _artifact(**overrides) -> ModelArtifact:
    values = {
        "model_type": "logistic",
        "feature_names": list(LTR_FEATURE_NAMES),
        "schema_version": FEATURE_SCHEMA_VERSION,
        "schema_hash": feature_schema_hash(),
        "parameters": {"means": [], "scales": [], "weights": [], "bias": 0.0},
        "trained_on": {"split": "gold"},
        "provisional": False,
    }
    return ModelArtifact(**{**values, **overrides})


def test_schema_hash_changes_with_feature_order() -> None:
    reordered = list(reversed(LTR_FEATURE_NAMES))

    assert feature_schema_hash() != feature_schema_hash(reordered)


def test_artifact_round_trip(tmp_path) -> None:
    path = _artifact().save(tmp_path / "m" / "model.json")

    assert ModelArtifact.load(path).model_type == "logistic"


def test_provisional_models_are_refused_unless_explicitly_allowed(tmp_path) -> None:
    path = _artifact(provisional=True).save(tmp_path / "model.json")

    with pytest.raises(ModelArtifactError, match="provisional"):
        ModelArtifact.load(path)
    assert ModelArtifact.load(path, allow_provisional=True).provisional is True


def test_a_model_from_another_feature_schema_is_refused(tmp_path) -> None:
    path = _artifact().save(tmp_path / "model.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_hash"] = "deadbeefdeadbeef"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ModelArtifactError, match="different feature schema"):
        ModelArtifact.load(path)


# -- LambdaMART (optional extra) ---------------------------------------------


def test_lambdamart_is_benchmarked_when_lightgbm_is_installed() -> None:
    pytest.importorskip("lightgbm")
    from app.matching.ltr.baseline import LambdaMartRanker

    train = [_group(f"t{i}", 40, i) for i in range(6)]
    evaluation = [_group(f"e{i}", 40, 100 + i) for i in range(3)]

    report = benchmark(
        train,
        evaluation,
        [CurrentPipelineRanker(), LambdaMartRanker(n_estimators=40)],
        fold="validation",
    )

    assert report.verdict["lambdamart"] == "challenger_beats_baseline"
