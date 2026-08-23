"""
LTR Feature Contract — shared between training and inference.

This module defines the canonical feature order and names used by LightGBM LambdaMART.
Both training and inference MUST use this exact list and order.
"""

from __future__ import annotations

from typing import Final

# Feature names in EXACT order used during LightGBM training.
# Source: scripts/train_lambdamart.py prepare_lgb_data() -> feature_cols
# Must match the columns in ltr_dataset.jsonl (excluding resume_id, vacancy_id, human_signal)

LTR_FEATURE_NAMES: Final[list[str]] = [
    "ce_ettin_raw",
    "ce_mmbert_raw",
    "ce_modernbert_raw",
    "existing_match_score",
    "existing_reranker_score",
    "existing_semantic_similarity",
    "required_total",
    "required_matched",
    "required_unmatched",
    "required_coverage",
    "required_avg_score",
    "preferred_total",
    "preferred_matched",
    "preferred_unmatched",
    "preferred_coverage",
    "preferred_avg_score",
    "hard_skill_coverage",
    "experience_coverage",
    "seniority_coverage",
    "language_coverage",
    "blocker_count",
    "has_hard_blocker",
    "has_location",
    "has_work_format",
    "has_salary",
    "has_employment_types",
]

# Number of features expected by the model
LTR_FEATURE_COUNT: Final[int] = len(LTR_FEATURE_NAMES)

# Label gain mapping for NDCG (matches training)
LTR_LABEL_GAIN: Final[list[int]] = [0, 1, 3]  # label 0, 1, 2 -> gain


def validate_feature_vector(features: list[float]) -> None:
    """Validate that feature vector has correct length."""
    if len(features) != LTR_FEATURE_COUNT:
        raise ValueError(
            f"LTR feature vector length mismatch: expected {LTR_FEATURE_COUNT}, "
            f"got {len(features)}"
        )


def get_feature_index(name: str) -> int:
    """Get index of a feature by name."""
    try:
        return LTR_FEATURE_NAMES.index(name)
    except ValueError as err:
        raise KeyError(f"Feature '{name}' not in LTR feature contract") from err


def feature_vector_to_dict(vector: list[float]) -> dict[str, float]:
    """Convert feature vector to named dict for debugging."""
    validate_feature_vector(vector)
    return dict(zip(LTR_FEATURE_NAMES, vector, strict=True))