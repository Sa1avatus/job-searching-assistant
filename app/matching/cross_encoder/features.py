"""Feature extraction for hybrid matching scorer.

Extracts structured features from existing DB data (requirement_matches,
vacancy_requirements, candidate_evidence, profile_facts) WITHOUT creating
new extraction pipelines.

Each feature set is for a single (resume_id, vacancy_id) pair.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MatchFeatures:
    """Structured features for a resume-vacancy pair.

    All features are in [0, 1] range or counts.
    None means feature is unavailable (NOT 0.0).
    """
    # Cross-encoder scores (raw)
    ce_ettin_raw: float | None = None
    ce_mmbert_raw: float | None = None
    ce_modernbert_raw: float | None = None

    # Existing pipeline scores
    existing_match_score: float | None = None  # 0-100
    existing_reranker_score: float | None = None
    existing_semantic_similarity: float | None = None

    # Normalized cross-encoder scores (filled by normalizer)
    ce_ettin_norm: float | None = None
    ce_mmbert_norm: float | None = None
    ce_modernbert_norm: float | None = None

    # Required skills coverage
    required_total: int = 0
    required_matched: int = 0
    required_unmatched: int = 0
    required_coverage: float | None = None  # matched / total
    required_avg_score: float | None = None

    # Preferred skills coverage
    preferred_total: int = 0
    preferred_matched: int = 0
    preferred_unmatched: int = 0
    preferred_coverage: float | None = None
    preferred_avg_score: float | None = None

    # Type-specific coverage (from requirement_matches)
    hard_skill_coverage: float | None = None
    experience_coverage: float | None = None
    seniority_coverage: float | None = None
    language_coverage: float | None = None

    # Blocker flags
    blocker_count: int = 0
    has_hard_blocker: bool = False

    # Vacancy metadata (if available)
    has_location: bool = False
    has_work_format: bool = False
    has_salary: bool = False
    has_employment_types: bool = False

    # Metadata
    resume_id: str = ""
    vacancy_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to flat dict for model input."""
        return {
            k: v for k, v in {
                "ce_ettin_raw": self.ce_ettin_raw,
                "ce_mmbert_raw": self.ce_mmbert_raw,
                "ce_modernbert_raw": self.ce_modernbert_raw,
                "existing_match_score": self.existing_match_score,
                "existing_reranker_score": self.existing_reranker_score,
                "existing_semantic_similarity": self.existing_semantic_similarity,
                "ce_ettin_norm": self.ce_ettin_norm,
                "ce_mmbert_norm": self.ce_mmbert_norm,
                "ce_modernbert_norm": self.ce_modernbert_norm,
                "required_total": self.required_total,
                "required_matched": self.required_matched,
                "required_unmatched": self.required_unmatched,
                "required_coverage": self.required_coverage,
                "required_avg_score": self.required_avg_score,
                "preferred_total": self.preferred_total,
                "preferred_matched": self.preferred_matched,
                "preferred_unmatched": self.preferred_unmatched,
                "preferred_coverage": self.preferred_coverage,
                "preferred_avg_score": self.preferred_avg_score,
                "hard_skill_coverage": self.hard_skill_coverage,
                "experience_coverage": self.experience_coverage,
                "seniority_coverage": self.seniority_coverage,
                "language_coverage": self.language_coverage,
                "blocker_count": self.blocker_count,
                "has_hard_blocker": int(self.has_hard_blocker),
            }.items() if v is not None
        }

    def feature_vector(self, feature_names: list[str]) -> list[float]:
        """Extract a numeric vector for the given feature names.
        Missing features are filled with 0.0.
        """
        d = self.to_dict()
        return [float(d.get(fn, 0.0) or 0.0) for fn in feature_names]


# ── Feature names for the scoring model ────────────────────────────────

CORE_FEATURE_NAMES = [
    "ce_ettin_norm",
    "required_coverage",
    "preferred_coverage",
    "hard_skill_coverage",
    "experience_coverage",
    "seniority_coverage",
]

FULL_FEATURE_NAMES = [
    "ce_ettin_norm",
    "existing_match_score",
    "existing_reranker_score",
    "existing_semantic_similarity",
    "required_coverage",
    "required_avg_score",
    "preferred_coverage",
    "preferred_avg_score",
    "hard_skill_coverage",
    "experience_coverage",
    "seniority_coverage",
    "language_coverage",
    "blocker_count",
]


class FeatureExtractor:
    """Extracts features from pre-loaded DB data.

    Does NOT query the DB directly — takes pre-loaded dicts for portability.
    """

    def __init__(
        self,
        requirement_matches: dict[str, list[dict]],  # vacancy_id → [matches]
        existing_scores: dict[str, dict],  # "resume_id|vacancy_id" → scores
        vacancy_meta: dict[str, dict],  # vacancy_id → metadata
        cross_encoder_scores: dict[str, dict[str, float]],  # model_name → {key → score}
    ) -> None:
        self._req_matches = requirement_matches
        self._existing_scores = existing_scores
        self._vacancy_meta = vacancy_meta
        self._ce_scores = cross_encoder_scores

    def extract(self, resume_id: str, vacancy_id: str) -> MatchFeatures:
        """Extract features for a single pair."""
        key = f"{resume_id}|{vacancy_id}"
        features = MatchFeatures(resume_id=resume_id, vacancy_id=vacancy_id)

        # Cross-encoder scores
        for model_name, scores in self._ce_scores.items():
            raw = scores.get(key)
            if raw is not None:
                if "ettin" in model_name:
                    features.ce_ettin_raw = raw
                elif "mmbert" in model_name or "mmBERT" in model_name:
                    features.ce_mmbert_raw = raw
                elif "modernbert" in model_name:
                    features.ce_modernbert_raw = raw

        # Existing pipeline scores
        ex = self._existing_scores.get(key, {})
        features.existing_match_score = ex.get("match_score")
        features.existing_reranker_score = ex.get("reranker_score")
        features.existing_semantic_similarity = ex.get("semantic_similarity")

        # Requirement coverage
        reqs = self._req_matches.get(vacancy_id, [])
        self._extract_requirement_features(features, reqs)

        # Vacancy metadata
        meta = self._vacancy_meta.get(vacancy_id, {})
        features.has_location = bool(meta.get("location"))
        features.has_work_format = meta.get("work_format", "unspecified") != "unspecified"
        features.has_salary = bool(meta.get("salary_text"))
        features.has_employment_types = bool(meta.get("employment_types"))

        return features

    def extract_batch(self, resume_id: str, vacancy_ids: list[str]) -> list[MatchFeatures]:
        """Extract features for multiple vacancies."""
        return [self.extract(resume_id, vid) for vid in vacancy_ids]

    def _extract_requirement_features(
        self, features: MatchFeatures, reqs: list[dict]
    ) -> None:
        """Extract requirement coverage features."""
        if not reqs:
            return

        match_level_scores = {
            "exact": 1.0, "strong": 0.9, "partial": 0.65,
            "related": 0.35, "theoretical_only": 0.15,
            "insufficient_evidence": 0.15, "missing": 0.0,
            "blocker": 0.0, "evaluation_error": 0.0,
        }

        required = [r for r in reqs if r.get("req_importance") == "required"]
        preferred = [r for r in reqs if r.get("req_importance") == "preferred"]

        # Required
        features.required_total = len(required)
        if required:
            matched = sum(1 for r in required if r.get("match_level") in ("exact", "strong", "partial"))
            features.required_matched = matched
            features.required_unmatched = len(required) - matched
            features.required_coverage = matched / len(required)
            scores = [match_level_scores.get(r.get("match_level", ""), 0) for r in required]
            features.required_avg_score = sum(scores) / len(scores)

        # Preferred
        features.preferred_total = len(preferred)
        if preferred:
            matched = sum(1 for r in preferred if r.get("match_level") in ("exact", "strong", "partial"))
            features.preferred_matched = matched
            features.preferred_unmatched = len(preferred) - matched
            features.preferred_coverage = matched / len(preferred)
            scores = [match_level_scores.get(r.get("match_level", ""), 0) for r in preferred]
            features.preferred_avg_score = sum(scores) / len(scores)

        # Type-specific coverage
        features.hard_skill_coverage = self._type_coverage(reqs, "hard_skill")
        features.experience_coverage = self._type_coverage(reqs, "experience")
        features.seniority_coverage = self._type_coverage(reqs, "seniority")
        features.language_coverage = self._type_coverage(reqs, "language")

        # Blockers
        features.blocker_count = sum(1 for r in reqs if r.get("is_hard_blocker"))
        features.has_hard_blocker = features.blocker_count > 0

    @staticmethod
    def _type_coverage(reqs: list[dict], req_type: str) -> float | None:
        typed = [r for r in reqs if r.get("req_type") == req_type]
        if not typed:
            return None
        matched = sum(1 for r in typed if r.get("match_level") in ("exact", "strong", "partial"))
        return matched / len(typed)