"""Score normalization for cross-encoder outputs.

Provides rank-based normalization to convert raw cross-encoder scores
to uniform [0, 1] distributions suitable for LTR feature input.

Pure Python on purpose: the API image does not ship numpy.
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Any


class ScoreNormalizer:
    """Normalize cross-encoder scores using rank transformation.

    Converts raw scores to percentile ranks within each model's score distribution.
    This makes scores from different models comparable and robust to outliers.
    """

    def __init__(self, method: str = "rank") -> None:
        if method != "rank":
            raise ValueError(f"Unsupported normalization method: {method}")
        self.method = method
        self._fitted: dict[str, list[float]] = {}

    def fit(self, model_name: str, scores: list[float]) -> None:
        """Fit the normalizer on a list of raw scores for a model."""
        self._fitted[model_name] = sorted(float(score) for score in scores)

    def transform(self, model_name: str, score: float | None) -> float | None:
        """Return the share of fitted scores <= ``score`` (in [0, 1]).

        None when the input is None or the model has no fitted scores.
        """
        if score is None:
            return None
        fitted_scores = self._fitted.get(model_name)
        if not fitted_scores:
            return None
        return bisect_right(fitted_scores, float(score)) / len(fitted_scores)

    def transform_batch(self, model_name: str, scores: list[float | None]) -> list[float | None]:
        return [self.transform(model_name, s) for s in scores]

    def is_fitted(self, model_name: str) -> bool:
        return len(self._fitted.get(model_name, [])) > 0

    def get_fitted_size(self, model_name: str) -> int:
        return len(self._fitted.get(model_name, []))

    def to_dict(self) -> dict[str, Any]:
        """Serialize fitted state for storage."""
        return {"method": self.method, "fitted": {k: list(v) for k, v in self._fitted.items()}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScoreNormalizer:
        normalizer = cls(method=data.get("method", "rank"))
        normalizer._fitted = {
            k: sorted(float(x) for x in v) for k, v in data.get("fitted", {}).items()
        }
        return normalizer
