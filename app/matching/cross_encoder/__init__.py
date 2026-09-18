"""Cross-encoder matching components."""

from app.matching.cross_encoder.features import MatchFeatures, FeatureExtractor
from app.matching.cross_encoder.ltr_feature_contract import LTR_FEATURE_NAMES, LTR_FEATURE_COUNT
from app.matching.cross_encoder.ltr_scorer import LTRScorer
from app.matching.cross_encoder.annotation import (
    AnnotationQueue,
    AnnotationQueueItem,
    AnnotationStats,
    DatasetReadiness,
    FeedbackResponse,
    get_annotation_queue,
    get_annotation_stats,
    get_dataset_readiness,
    submit_pairwise,
    submit_pointwise,
)

__all__ = [
    "MatchFeatures",
    "FeatureExtractor",
    "LTR_FEATURE_NAMES",
    "LTR_FEATURE_COUNT",
    "LTRScorer",
    "AnnotationQueue",
    "AnnotationQueueItem",
    "AnnotationStats",
    "DatasetReadiness",
    "FeedbackResponse",
    "get_annotation_queue",
    "get_annotation_stats",
    "get_dataset_readiness",
    "submit_pairwise",
    "submit_pointwise",
]