"""
LTR Scorer — LightGBM LambdaMART inference for matching pipeline.

Loads the trained LambdaMART model and provides scoring for resume-vacancy pairs.
Integrates with existing MatchFeatures for feature extraction.
"""

from __future__ import annotations

import logging
from pathlib import Path

import lightgbm as lgb
import numpy as np

from app.matching.cross_encoder.features import MatchFeatures
from app.matching.cross_encoder.ltr_feature_contract import (
    LTR_FEATURE_COUNT,
    LTR_FEATURE_NAMES,
)

logger = logging.getLogger(__name__)


class LTRScorer:
    """
    LightGBM LambdaMART scorer for resume-vacancy ranking.

    Loads a pre-trained LightGBM model and scores MatchFeatures vectors.
    Designed to integrate with existing matching pipeline.
    """

    def __init__(
        self,
        model_path: str | Path = "models/lambdamart_baseline.txt",
        *,
        label_gain: list[int] | None = None,
    ) -> None:
        """
        Initialize LTR scorer.

        Args:
            model_path: Path to LightGBM model file.
            label_gain: Label gain mapping for NDCG. Default: [0, 1, 3].
        """
        self._model_path = Path(model_path)
        self._label_gain = label_gain or [0, 1, 3]
        self._booster: lgb.Booster | None = None
        self._loaded = False

    def load(self) -> None:
        """Load the LightGBM model from disk."""
        if not self._model_path.exists():
            raise FileNotFoundError(f"LTR model not found: {self._model_path}")

        logger.info("Loading LTR model from %s", self._model_path)
        self._booster = lgb.Booster(model_file=str(self._model_path))

        # Validate feature count matches contract
        model_feature_count = self._booster.num_feature()
        if model_feature_count != LTR_FEATURE_COUNT:
            logger.warning(
                "LTR model feature count (%d) != contract (%d). "
                "This may indicate training/inference mismatch.",
                model_feature_count, LTR_FEATURE_COUNT
            )

        self._loaded = True
        logger.info("LTR model loaded: %d features", model_feature_count)

    def unload(self) -> None:
        """Free model resources."""
        self._booster = None
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def score(self, features: MatchFeatures) -> float:
        """
        Score a single resume-vacancy pair using LambdaMART.

        Args:
            features: MatchFeatures extracted for a resume-vacancy pair.

        Returns:
            LambdaMART prediction score (higher = more relevant).
        """
        if not self._loaded:
            raise RuntimeError("LTR model not loaded. Call load() first.")

        # Extract feature vector in canonical order
        feature_vector = features.feature_vector(LTR_FEATURE_NAMES)

        # Validate length
        if len(feature_vector) != LTR_FEATURE_COUNT:
            raise ValueError(
                f"Feature vector length {len(feature_vector)} != "
                f"expected {LTR_FEATURE_COUNT}"
            )

        # Predict
        X = np.array([feature_vector], dtype=np.float32)
        pred = self._booster.predict(X)[0]

        return float(pred)

    def score_batch(self, features_list: list[MatchFeatures]) -> list[float]:
        """
        Score multiple resume-vacancy pairs.

        Args:
            features_list: List of MatchFeatures for multiple pairs.

        Returns:
            List of prediction scores in same order.
        """
        if not self._loaded:
            raise RuntimeError("LTR model not loaded. Call load() first.")

        if not features_list:
            return []

        # Build feature matrix
        X = np.zeros((len(features_list), LTR_FEATURE_COUNT), dtype=np.float32)
        for i, features in enumerate(features_list):
            vec = features.feature_vector(LTR_FEATURE_NAMES)
            if len(vec) != LTR_FEATURE_COUNT:
                raise ValueError(
                    f"Row {i}: feature vector length {len(vec)} != "
                    f"expected {LTR_FEATURE_COUNT}"
                )
            X[i] = vec

        # Predict all
        preds = self._booster.predict(X)
        return preds.tolist()

    def score_batch_arrays(self, X: np.ndarray) -> np.ndarray:
        """
        Score from pre-built feature matrix (for internal use).

        Args:
            X: Feature matrix of shape (n_samples, LTR_FEATURE_COUNT).

        Returns:
            Predictions array of shape (n_samples,).
        """
        if not self._loaded:
            raise RuntimeError("LTR model not loaded. Call load() first.")

        if X.shape[1] != LTR_FEATURE_COUNT:
            raise ValueError(
                f"Feature matrix has {X.shape[1]} columns, "
                f"expected {LTR_FEATURE_COUNT}"
            )

        return self._booster.predict(X)


def create_ltr_scorer(
    model_path: str | Path | None = None,
    enabled: bool = False,
) -> LTRScorer | None:
    """
    Factory function to create LTR scorer based on configuration.

    Args:
        model_path: Path to LightGBM model. If None, uses default.
        enabled: Whether LTR is enabled. If False, returns None.

    Returns:
        LTRScorer instance if enabled, None otherwise.
    """
    if not enabled:
        logger.info("LTR scorer disabled by configuration")
        return None

    default_path = Path("models/lambdamart_baseline.txt")
    scorer = LTRScorer(model_path or default_path)

    try:
        scorer.load()
        return scorer
    except FileNotFoundError as e:
        logger.warning("LTR model not found at %s: %s", model_path or default_path, e)
        return None
    except Exception as e:
        logger.error("Failed to load LTR model: %s", e)
        return None