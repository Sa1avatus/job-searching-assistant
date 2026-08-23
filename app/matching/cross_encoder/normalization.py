"""Score normalization for cross-encoder outputs.

Provides rank-based normalization to convert raw cross-encoder scores
to uniform [0, 1] distributions suitable for LTR feature input.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np


class ScoreNormalizer:
    """Normalize cross-encoder scores using rank transformation.

    Converts raw scores to percentile ranks within each model's score distribution.
    This makes scores from different models comparable and robust to outliers.
    """

    def __init__(self, method: str = "rank") -> None:
        """
        Args:
            method: Normalization method. Currently only "rank" is supported.
        """
        if method != "rank":
            raise ValueError(f"Unsupported normalization method: {method}")
        self.method = method
        self._fitted: dict[str, np.ndarray] = {}

    def fit(self, model_name: str, scores: list[float]) -> None:
        """Fit the normalizer on a list of scores for a model.

        Args:
            model_name: Identifier for the model (e.g., "ettin", "mmbert").
            scores: List of raw scores to fit on.
        """
        arr = np.array(scores, dtype=np.float64)
        # Store sorted unique values for rank interpolation
        self._fitted[model_name] = np.sort(arr)

    def transform(self, model_name: str, score: float | None) -> float | None:
        """Transform a single score to its percentile rank.

        Args:
            model_name: Model identifier used in fit().
            score: Raw score to normalize.

        Returns:
            Normalized score in [0, 1] range, or None if input is None
            or model not fitted.
        """
        if score is None:
            return None
        if model_name not in self._fitted:
            return None

        fitted_scores = self._fitted[model_name]
        if len(fitted_scores) == 0:
            return None

        # Compute percentile rank using searchsorted
        # This gives the proportion of scores <= the given score
        rank = np.searchsorted(fitted_scores, score, side="right")
        return float(rank) / float(len(fitted_scores))

    def transform_batch(self, model_name: str, scores: list[float | None]) -> list[float | None]:
        """Transform a batch of scores.

        Args:
            model_name: Model identifier used in fit().
            scores: List of raw scores.

        Returns:
            List of normalized scores.
        """
        return [self.transform(model_name, s) for s in scores]

    def is_fitted(self, model_name: str) -> bool:
        """Check if a model has been fitted."""
        return model_name in self._fitted and len(self._fitted[model_name]) > 0

    def get_fitted_size(self, model_name: str) -> int:
        """Get the number of fitted samples for a model."""
        return len(self._fitted.get(model_name, []))

    def to_dict(self) -> dict[str, Any]:
        """Serialize fitted state for storage."""
        return {
            "method": self.method,
            "fitted": {k: v.tolist() for k, v in self._fitted.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScoreNormalizer":
        """Deserialize fitted state."""
        normalizer = cls(method=data.get("method", "rank"))
        normalizer._fitted = {
            k: np.array(v, dtype=np.float64) for k, v in data.get("fitted", {}).items()
        }
        return normalizer
