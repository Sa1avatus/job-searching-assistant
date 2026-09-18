"""Rankers and the benchmark that compares them on frozen human labels.

* ``CurrentPipelineRanker`` orders by the production score - the bar any challenger must beat.
* ``LogisticRanker`` is the lightweight baseline: logistic regression on soft graded targets
  (gain / 2), z-scored features with train-mean imputation. Pure Python, deterministic.
* ``LambdaMartRanker`` (LightGBM, optional extra) is trained only when lightgbm is installed.

Everything here is a plain function of the data it is given: training data comes from the
``train`` fold, metrics from a frozen ``validation``/``test`` fold, never the other way round.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.matching.cross_encoder.ltr_feature_contract import LTR_FEATURE_NAMES
from app.matching.ltr.metrics import (
    RELEVANT_GAIN,
    mean_defined,
    ndcg_at_k,
    order_by_scores,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

MIN_EVAL_GROUPS = 2
MIN_EVAL_ITEMS = 50


@dataclass(frozen=True, slots=True)
class RankedItem:
    vacancy_id: str
    features: dict[str, float | None]
    gain: int  # human label gain 0..2
    baseline_score: float | None = None  # the production pipeline's score


@dataclass(frozen=True, slots=True)
class RankingGroup:
    """All labelled items of one query (a resume)."""

    group_id: str
    items: tuple[RankedItem, ...]


class Ranker(Protocol):
    name: str

    def fit(self, groups: Sequence[RankingGroup]) -> None: ...

    def score(self, group: RankingGroup) -> list[float]: ...


def _value(features: dict[str, float | None], name: str) -> float | None:
    value = features.get(name)
    return None if value is None else float(value)


class CurrentPipelineRanker:
    name = "current_pipeline"

    def fit(self, groups: Sequence[RankingGroup]) -> None:
        return None

    def score(self, group: RankingGroup) -> list[float]:
        return [
            item.baseline_score if item.baseline_score is not None else 0.0 for item in group.items
        ]


@dataclass(slots=True)
class LogisticRanker:
    name: str = "logistic_regression"
    feature_names: list[str] = field(default_factory=lambda: list(LTR_FEATURE_NAMES))
    learning_rate: float = 0.3
    epochs: int = 300
    l2: float = 0.01
    means: list[float] = field(default_factory=list)
    scales: list[float] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0

    def _row(self, features: dict[str, float | None]) -> list[float]:
        values = []
        for index, name in enumerate(self.feature_names):
            raw = _value(features, name)
            value = self.means[index] if raw is None else raw
            values.append((value - self.means[index]) / self.scales[index])
        return values

    def fit(self, groups: Sequence[RankingGroup]) -> None:
        items = [item for group in groups for item in group.items]
        if not items:
            raise ValueError("cannot fit on an empty training fold")
        count = len(self.feature_names)
        self.means, self.scales = [], []
        for name in self.feature_names:
            observed = [v for i in items if (v := _value(i.features, name)) is not None]
            mean = sum(observed) / len(observed) if observed else 0.0
            variance = sum((v - mean) ** 2 for v in observed) / len(observed) if observed else 0.0
            self.means.append(mean)
            self.scales.append(math.sqrt(variance) or 1.0)
        rows = [self._row(i.features) for i in items]
        targets = [i.gain / RELEVANT_GAIN for i in items]
        self.weights = [0.0] * count
        self.bias = 0.0
        n = len(rows)
        for _ in range(self.epochs):
            grad_w = [0.0] * count
            grad_b = 0.0
            for row, target in zip(rows, targets, strict=True):
                z = self.bias + sum(w * x for w, x in zip(self.weights, row, strict=True))
                error = 1.0 / (1.0 + math.exp(-max(min(z, 30.0), -30.0))) - target
                grad_b += error
                for j, x in enumerate(row):
                    grad_w[j] += error * x
            self.bias -= self.learning_rate * grad_b / n
            for j in range(count):
                self.weights[j] -= self.learning_rate * (grad_w[j] / n + self.l2 * self.weights[j])

    def score(self, group: RankingGroup) -> list[float]:
        if not self.weights:
            raise RuntimeError("ranker is not fitted")
        return [
            self.bias + sum(w * x for w, x in zip(self.weights, self._row(i.features), strict=True))
            for i in group.items
        ]

    def parameters(self) -> dict[str, Any]:
        return {
            "means": self.means,
            "scales": self.scales,
            "weights": self.weights,
            "bias": self.bias,
        }

    @classmethod
    def from_parameters(
        cls, parameters: dict[str, Any], feature_names: list[str]
    ) -> LogisticRanker:
        ranker = cls(feature_names=list(feature_names))
        ranker.means = list(parameters["means"])
        ranker.scales = list(parameters["scales"])
        ranker.weights = list(parameters["weights"])
        ranker.bias = float(parameters["bias"])
        return ranker


class LambdaMartRanker:
    """LightGBM LambdaMART. Needs the optional ``ltr`` extra (lightgbm, numpy)."""

    name = "lambdamart"

    def __init__(self, feature_names: list[str] | None = None, **params: Any) -> None:
        self.feature_names = feature_names or list(LTR_FEATURE_NAMES)
        self.params = {
            "objective": "lambdarank",
            "metric": "ndcg",
            "ndcg_eval_at": [10],
            "learning_rate": 0.05,
            "num_leaves": 7,
            "min_data_in_leaf": 5,
            "n_estimators": 150,
            "verbosity": -1,
            "random_state": 42,
            **params,
        }
        self._model: Any = None

    def _matrix(self, groups: Sequence[RankingGroup]) -> list[list[float]]:
        nan = float("nan")
        return [
            [nan if (v := _value(item.features, n)) is None else v for n in self.feature_names]
            for group in groups
            for item in group.items
        ]

    def fit(self, groups: Sequence[RankingGroup]) -> None:
        import lightgbm as lgb  # optional dependency

        usable = [g for g in groups if len(g.items) > 1]
        if not usable:
            raise ValueError("LambdaMART needs groups with at least two labelled items")
        model = lgb.LGBMRanker(**self.params)
        model.fit(
            self._matrix(usable),
            [item.gain for group in usable for item in group.items],
            group=[len(group.items) for group in usable],
        )
        self._model = model

    def score(self, group: RankingGroup) -> list[float]:
        if self._model is None:
            raise RuntimeError("ranker is not fitted")
        return [float(v) for v in self._model.predict(self._matrix([group]))]


@dataclass(slots=True)
class RankerResult:
    name: str
    ndcg_at_10: float | None
    precision_at_10: float | None
    recall_at_10: float | None
    mrr: float | None


@dataclass(slots=True)
class BenchmarkReport:
    fold: str
    n_groups: int
    n_items: int
    results: list[RankerResult]
    baseline: str
    deltas: dict[str, dict[str, float | None]]
    verdict: dict[str, str]


def _metrics(ranker: Ranker, groups: Sequence[RankingGroup], k: int) -> RankerResult:
    per_group: list[tuple[float | None, ...]] = []
    for group in groups:
        gains = [item.gain for item in group.items]
        ids = [item.vacancy_id for item in group.items]
        ordered = order_by_scores(gains, ranker.score(group), ids)
        per_group.append(
            (
                ndcg_at_k(ordered, k),
                precision_at_k(ordered, k),
                recall_at_k(ordered, k),
                reciprocal_rank(ordered),
            )
        )
    columns = list(zip(*per_group, strict=True)) if per_group else [(), (), (), ()]
    return RankerResult(ranker.name, *(mean_defined(column) for column in columns))


def benchmark(
    train_groups: Sequence[RankingGroup],
    eval_groups: Sequence[RankingGroup],
    rankers: Sequence[Ranker],
    *,
    fold: str,
    k: int = 10,
    baseline: str = "current_pipeline",
) -> BenchmarkReport:
    """Fit every ranker on the train groups and score the frozen evaluation groups.

    A challenger only earns ``challenger_beats_baseline`` with a positive NDCG@k lead on enough
    evaluation data; otherwise the verdict says why it cannot be trusted, so a lucky tiny sample
    can never justify replacing the production default.
    """
    usable = [g for g in eval_groups if len(g.items) > 1]
    for ranker in rankers:
        ranker.fit(train_groups)
    results = [_metrics(ranker, usable, k) for ranker in rankers]
    by_name = {r.name: r for r in results}
    base = by_name.get(baseline)
    n_items = sum(len(g.items) for g in usable)
    enough = len(usable) >= MIN_EVAL_GROUPS and n_items >= MIN_EVAL_ITEMS

    deltas: dict[str, dict[str, float | None]] = {}
    verdict: dict[str, str] = {}
    for result in results:
        if result.name == baseline or base is None:
            continue

        def diff(a: float | None, b: float | None) -> float | None:
            return None if a is None or b is None else round(a - b, 6)

        deltas[result.name] = {
            "ndcg_at_10": diff(result.ndcg_at_10, base.ndcg_at_10),
            "precision_at_10": diff(result.precision_at_10, base.precision_at_10),
            "recall_at_10": diff(result.recall_at_10, base.recall_at_10),
            "mrr": diff(result.mrr, base.mrr),
        }
        lead = deltas[result.name]["ndcg_at_10"]
        if not enough:
            verdict[result.name] = "insufficient_evidence"
        elif lead is not None and lead > 0:
            verdict[result.name] = "challenger_beats_baseline"
        else:
            verdict[result.name] = "baseline_holds"
    return BenchmarkReport(fold, len(usable), n_items, results, baseline, deltas, verdict)
