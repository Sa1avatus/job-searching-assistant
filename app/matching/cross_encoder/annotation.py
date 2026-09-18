"""Stratified Pointwise Sampling for LTR Human Annotation.

Provides:
- Annotation queue with stratified sampling (current_top, ltr_top, rank_disagreement, middle_rank, random)
- Pointwise and pairwise feedback submission
- Statistics and dataset readiness
- Deterministic sampling with seed=42
"""
from __future__ import annotations

import random
import structlog
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.matching.cross_encoder.features import FeatureExtractor, MatchFeatures
from app.matching.cross_encoder.ltr_feature_contract import LTR_FEATURE_NAMES
from app.matching.cross_encoder.ltr_scorer import LTRScorer
from app.storage.database import session_scope
from app.storage.tables import (
    ApplicationMatchResultRow,
    ApplicationRow,
    CvFileRow,
    UserRow,
    VacancyRow,
)

logger = structlog.get_logger(__name__)

# Sampling configuration
SEED = 42
DEFAULT_LIMIT = 100
STRATA_QUOTAS = {
    "current_top": 20,
    "ltr_top": 20,
    "rank_disagreement": 30,
    "middle_rank": 20,
    "random": 10,
}
STRATA_PRIORITY = [
    "rank_disagreement",
    "ltr_top",
    "current_top",
    "middle_rank",
    "random",
]


class AnnotationQueueItem(BaseModel):
    """Single item in the annotation queue."""
    vacancy_id: str
    vacancy_title: str
    vacancy_company: str
    vacancy_location: str
    vacancy_description: str
    current_rank: int | None = None
    ltr_rank: int | None = None
    current_score: float | None = None
    ltr_score: float | None = None
    sampling_reason: str
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    salary_text: str = ""
    work_format: str = ""
    employment_types: list[str] = Field(default_factory=list)


class AnnotationQueue(BaseModel):
    """Annotation queue response."""
    resume_id: str
    resume_filename: str
    items: list[AnnotationQueueItem]
    total_eligible: int
    sampled_count: int
    strata_breakdown: dict[str, int]


class AnnotationStats(BaseModel):
    """Annotation statistics."""
    total_pointwise: int
    total_pairwise: int
    pointwise_by_label: dict[str, int]
    pairwise_by_label: dict[str, int]
    unique_resumes: int
    unique_vacancies: int


class DatasetReadiness(BaseModel):
    """Dataset readiness for training."""
    pointwise_observations: int
    pairwise_observations: int
    total_observations: int
    unique_resume_groups: int
    min_observations_per_group: int
    ready_for_training: bool


class FeedbackResponse(BaseModel):
    """Feedback submission response."""
    status: str  # accepted, forbidden, invalid
    feedback_id: str | None = None
    label: str | None = None


@dataclass
class SampledCandidate:
    """A candidate vacancy selected for annotation."""
    vacancy_id: str
    vacancy_title: str
    vacancy_company: str
    vacancy_location: str
    vacancy_description: str
    required_skills: list[str]
    preferred_skills: list[str]
    salary_text: str
    work_format: str
    employment_types: list[str]
    current_rank: int | None
    ltr_rank: int | None
    current_score: float | None
    ltr_score: float | None
    sampling_reason: str
    strata: list[str] = field(default_factory=list)

    def to_queue_item(self) -> AnnotationQueueItem:
        return AnnotationQueueItem(
            vacancy_id=self.vacancy_id,
            vacancy_title=self.vacancy_title,
            vacancy_company=self.vacancy_company,
            vacancy_location=self.vacancy_location,
            vacancy_description=self.vacancy_description,
            current_rank=self.current_rank,
            ltr_rank=self.ltr_rank,
            current_score=self.current_score,
            ltr_score=self.ltr_score,
            sampling_reason=self.sampling_reason,
            required_skills=self.required_skills,
            preferred_skills=self.preferred_skills,
            salary_text=self.salary_text,
            work_format=self.work_format,
            employment_types=self.employment_types,
        )


def get_annotation_queue(
    session: Session,
    user_id: str,
    resume_id: str,
    features_list: list[MatchFeatures],
    *,
    limit: int = DEFAULT_LIMIT,
    vacancy_meta: dict[str, dict] | None = None,
    resume_text: str = "",
    resume_skills: list[str] | None = None,
) -> AnnotationQueue:
    """Get stratified pointwise annotation queue for a resume.

    Builds eligible pool from all vacancies, excludes already pointwise-labelled,
    applies stratified sampling, and returns up to `limit` items.
    """
    # Verify ownership
    cv = session.get(CvFileRow, resume_id)
    if not cv or cv.user_id != user_id:
        return AnnotationQueue(
            resume_id=resume_id,
            resume_filename="",
            items=[],
            total_eligible=0,
            sampled_count=0,
            strata_breakdown={},
        )

    # Get all vacancies with match results for this resume
    all_candidates = _build_candidate_pool(session, resume_id, features_list, vacancy_meta or {})

    # Exclude already pointwise-annotated vacancies for this resume
    eligible = _exclude_pointwise_annotated(session, user_id, resume_id, all_candidates)

    # Apply stratified sampling
    sampled = _stratified_sample(eligible, limit=limit)

    # Convert to queue items
    items = [c.to_queue_item() for c in sampled]

    # Calculate strata breakdown
    strata_breakdown = {}
    for c in sampled:
        strata_breakdown[c.sampling_reason] = strata_breakdown.get(c.sampling_reason, 0) + 1

    return AnnotationQueue(
        resume_id=resume_id,
        resume_filename=cv.original_filename,
        items=items,
        total_eligible=len(eligible),
        sampled_count=len(items),
        strata_breakdown=strata_breakdown,
    )


def _build_candidate_pool(
    session: Session,
    resume_id: str,
    features_list: list[MatchFeatures],
    vacancy_meta: dict[str, dict],
) -> list[SampledCandidate]:
    """Build the full candidate pool with rankings."""
    # Build lookup for features
    features_by_vacancy = {f.vacancy_id: f for f in features_list}

    # Get all match results for this resume
    # Join Application -> ApplicationMatchResultRow -> Vacancy
    stmt = (
        select(ApplicationMatchResultRow, VacancyRow, ApplicationRow)
        .join(ApplicationRow, ApplicationMatchResultRow.application_id == ApplicationRow.id)
        .join(VacancyRow, ApplicationRow.vacancy_id == VacancyRow.id)
        .where(ApplicationRow.selected_cv_file_id == resume_id)
    )
    results = session.execute(stmt).all()

    candidates = []
    for match_result, vacancy, application in results:
        features = features_by_vacancy.get(vacancy.id)
        if not features:
            continue

        meta = vacancy_meta.get(vacancy.id, {})

        # Current rank from existing pipeline
        current_score = match_result.final_score
        current_rank = None  # Will be computed after sorting

        # LTR score
        ltr_score = getattr(match_result, "ltr_score", None)
        ltr_rank = None  # Will be computed after sorting

        candidates.append(SampledCandidate(
            vacancy_id=vacancy.id,
            vacancy_title=vacancy.title,
            vacancy_company=vacancy.company,
            vacancy_location=vacancy.location or "",
            vacancy_description=vacancy.description_text or "",
            required_skills=vacancy.required_skills or [],
            preferred_skills=vacancy.preferred_skills or [],
            salary_text=vacancy.salary_text or "",
            work_format=vacancy.work_format or "unspecified",
            employment_types=vacancy.employment_types or [],
            current_rank=current_rank,
            ltr_rank=ltr_rank,
            current_score=current_score,
            ltr_score=ltr_score,
            sampling_reason="",
            strata=[],
        ))

    # Sort by current score to assign current_rank
    candidates.sort(key=lambda c: c.current_score or 0, reverse=True)
    for i, c in enumerate(candidates):
        c.current_rank = i + 1

    # Sort by LTR score to assign ltr_rank (if available)
    ltr_candidates = [c for c in candidates if c.ltr_score is not None]
    ltr_candidates.sort(key=lambda c: c.ltr_score or 0, reverse=True)
    for i, c in enumerate(ltr_candidates):
        c.ltr_rank = i + 1

    return candidates


def _exclude_pointwise_annotated(
    session: Session,
    user_id: str,
    resume_id: str,
    candidates: list[SampledCandidate],
) -> list[SampledCandidate]:
    """Exclude vacancies that already have pointwise annotation for this resume."""
    # Get all pointwise-annotated vacancy_ids for this resume/user
    # We'll check the annotation storage (could be DB or file-based)
    annotated_vacancy_ids = _get_pointwise_annotated_vacancies(session, user_id, resume_id)

    eligible = [c for c in candidates if c.vacancy_id not in annotated_vacancy_ids]
    logger.info(
        "annotation_excluded_pointwise",
        resume_id=resume_id,
        total=len(candidates),
        excluded=len(candidates) - len(eligible),
        eligible=len(eligible),
    )
    return eligible


def _get_pointwise_annotated_vacancies(
    session: Session,
    user_id: str,
    resume_id: str,
) -> set[str]:
    """Get set of vacancy_ids that have pointwise annotations for this resume/user.

    Checks the annotation_feedback table.
    """
    from app.storage.tables import AnnotationFeedbackRow
    from sqlalchemy import select

    stmt = select(AnnotationFeedbackRow.vacancy_id).where(
        AnnotationFeedbackRow.user_id == user_id,
        AnnotationFeedbackRow.resume_id == resume_id,
        AnnotationFeedbackRow.feedback_type == "pointwise",
    )
    result = session.execute(stmt).scalars().all()
    return set(result)


def _stratified_sample(
    candidates: list[SampledCandidate],
    limit: int = DEFAULT_LIMIT,
) -> list[SampledCandidate]:
    """Apply stratified sampling to select candidates for annotation.

    Strata (in priority order for tiebreaking):
    1. rank_disagreement (30) - largest |current_rank - ltr_rank|
    2. ltr_top (20) - top by LTR score
    3. current_top (20) - top by current score
    4. middle_rank (20) - middle of ranking (rank 50-300 or adapted)
    5. random (10) - random from remaining

    Returns UNION of selected candidates, deduplicated, up to limit.
    """
    if not candidates:
        return []

    rng = random.Random(SEED)
    selected = {}  # vacancy_id -> SampledCandidate (with merged strata)
    strata_counts = {k: 0 for k in STRATA_QUOTAS}

    # 1. rank_disagreement: largest absolute rank difference
    disagreement_candidates = [c for c in candidates if c.current_rank and c.ltr_rank]
    disagreement_candidates.sort(
        key=lambda c: abs(c.current_rank - c.ltr_rank),
        reverse=True,
    )
    for c in disagreement_candidates:
        if strata_counts["rank_disagreement"] >= STRATA_QUOTAS["rank_disagreement"]:
            break
        if c.vacancy_id not in selected:
            c.strata.append("rank_disagreement")
            c.sampling_reason = "rank_disagreement"
            selected[c.vacancy_id] = c
            strata_counts["rank_disagreement"] += 1

    # 2. ltr_top: top by LTR score
    ltr_sorted = [c for c in candidates if c.ltr_rank is not None]
    ltr_sorted.sort(key=lambda c: c.ltr_rank)
    for c in ltr_sorted:
        if strata_counts["ltr_top"] >= STRATA_QUOTAS["ltr_top"]:
            break
        if c.vacancy_id not in selected:
            c.strata.append("ltr_top")
            c.sampling_reason = "ltr_top"
            selected[c.vacancy_id] = c
            strata_counts["ltr_top"] += 1
        else:
            # Already selected, upgrade sampling_reason if higher priority
            existing = selected[c.vacancy_id]
            existing.strata.append("ltr_top")
            if STRATA_PRIORITY.index("ltr_top") < STRATA_PRIORITY.index(existing.sampling_reason):
                existing.sampling_reason = "ltr_top"

    # 3. current_top: top by current score
    current_sorted = sorted(candidates, key=lambda c: c.current_rank or float('inf'))
    for c in current_sorted:
        if strata_counts["current_top"] >= STRATA_QUOTAS["current_top"]:
            break
        if c.vacancy_id not in selected:
            c.strata.append("current_top")
            c.sampling_reason = "current_top"
            selected[c.vacancy_id] = c
            strata_counts["current_top"] += 1
        else:
            existing = selected[c.vacancy_id]
            existing.strata.append("current_top")
            if STRATA_PRIORITY.index("current_top") < STRATA_PRIORITY.index(existing.sampling_reason):
                existing.sampling_reason = "current_top"

    # 4. middle_rank: middle of ranking
    # Adapt range based on candidate count
    n = len(candidates)
    middle_start = min(50, n // 4)
    middle_end = min(300, 3 * n // 4)
    middle_candidates = [c for c in candidates if middle_start < (c.current_rank or n + 1) <= middle_end]
    # Sort by current_rank for deterministic selection
    middle_candidates.sort(key=lambda c: c.current_rank or float('inf'))
    for c in middle_candidates:
        if strata_counts["middle_rank"] >= STRATA_QUOTAS["middle_rank"]:
            break
        if c.vacancy_id not in selected:
            c.strata.append("middle_rank")
            c.sampling_reason = "middle_rank"
            selected[c.vacancy_id] = c
            strata_counts["middle_rank"] += 1
        else:
            existing = selected[c.vacancy_id]
            existing.strata.append("middle_rank")
            if STRATA_PRIORITY.index("middle_rank") < STRATA_PRIORITY.index(existing.sampling_reason):
                existing.sampling_reason = "middle_rank"

    # 5. random: random from remaining
    remaining = [c for c in candidates if c.vacancy_id not in selected]
    rng.shuffle(remaining)
    for c in remaining:
        if strata_counts["random"] >= STRATA_QUOTAS["random"]:
            break
        c.strata.append("random")
        c.sampling_reason = "random"
        selected[c.vacancy_id] = c
        strata_counts["random"] += 1

    # If still under limit, fill from remaining eligible
    result = list(selected.values())
    if len(result) < limit:
        remaining = [c for c in candidates if c.vacancy_id not in selected]
        # Sort by current_rank for deterministic fill
        remaining.sort(key=lambda c: c.current_rank or float('inf'))
        for c in remaining:
            if len(result) >= limit:
                break
            c.strata.append("fill")
            c.sampling_reason = "fill"
            result.append(c)

    # Sort final result by priority of sampling_reason, then by rank
    def sort_key(c: SampledCandidate):
        priority = STRATA_PRIORITY.index(c.sampling_reason) if c.sampling_reason in STRATA_PRIORITY else 99
        return (priority, c.current_rank or float('inf'))

    result.sort(key=sort_key)
    return result[:limit]


def submit_pointwise(
    session: Session,
    user_id: str,
    resume_id: str,
    vacancy_id: str,
    label: str,
    reasons: list[str],
    comment: str | None,
) -> FeedbackResponse:
    """Submit pointwise human feedback for a resume-vacancy pair."""
    # Verify ownership
    cv = session.get(CvFileRow, resume_id)
    if not cv or cv.user_id != user_id:
        return FeedbackResponse(status="forbidden")

    # Verify vacancy exists
    vacancy = session.get(VacancyRow, vacancy_id)
    if not vacancy:
        return FeedbackResponse(status="invalid", label="Vacancy not found")

    # Check for existing pointwise annotation (upsert behavior)
    from app.storage.tables import AnnotationFeedbackRow
    from sqlalchemy import select
    from datetime import datetime, UTC
    import uuid

    existing_stmt = select(AnnotationFeedbackRow).where(
        AnnotationFeedbackRow.user_id == user_id,
        AnnotationFeedbackRow.resume_id == resume_id,
        AnnotationFeedbackRow.vacancy_id == vacancy_id,
        AnnotationFeedbackRow.feedback_type == "pointwise",
    )
    existing = session.execute(existing_stmt).scalar_one_or_none()

    if existing:
        # Update existing
        existing.label = label
        existing.reasons = reasons
        existing.comment = comment
        existing.updated_at = datetime.now(UTC)
        feedback_id = existing.id
    else:
        # Create new
        feedback_id = str(uuid.uuid4())
        feedback = AnnotationFeedbackRow(
            id=feedback_id,
            user_id=user_id,
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            feedback_type="pointwise",
            label=label,
            reasons=reasons,
            comment=comment,
        )
        session.add(feedback)

    logger.info(
        "pointwise_annotation_submitted",
        user_id=user_id,
        resume_id=resume_id,
        vacancy_id=vacancy_id,
        label=label,
    )

    return FeedbackResponse(
        status="accepted",
        feedback_id=feedback_id,
        label=label,
    )


def submit_pairwise(
    session: Session,
    user_id: str,
    resume_id: str,
    vacancy_a_id: str,
    vacancy_b_id: str,
    preference: str,
    a_reasons: list[str],
    b_reasons: list[str],
    comment: str | None,
) -> FeedbackResponse:
    """Submit pairwise human preference feedback."""
    # Verify ownership
    cv = session.get(CvFileRow, resume_id)
    if not cv or cv.user_id != user_id:
        return FeedbackResponse(status="forbidden")

    # Verify vacancies exist
    vacancy_a = session.get(VacancyRow, vacancy_a_id)
    vacancy_b = session.get(VacancyRow, vacancy_b_id)
    if not vacancy_a or not vacancy_b:
        return FeedbackResponse(status="invalid", label="Vacancy not found")

    # Check for existing pairwise annotation (upsert behavior)
    from app.storage.tables import AnnotationFeedbackRow
    from sqlalchemy import select
    from datetime import datetime, UTC
    import uuid

    existing_stmt = select(AnnotationFeedbackRow).where(
        AnnotationFeedbackRow.user_id == user_id,
        AnnotationFeedbackRow.resume_id == resume_id,
        AnnotationFeedbackRow.vacancy_a_id == vacancy_a_id,
        AnnotationFeedbackRow.vacancy_b_id == vacancy_b_id,
        AnnotationFeedbackRow.feedback_type == "pairwise",
    )
    existing = session.execute(existing_stmt).scalar_one_or_none()

    if existing:
        # Update existing
        existing.label = preference
        existing.a_reasons = a_reasons
        existing.b_reasons = b_reasons
        existing.comment = comment
        existing.updated_at = datetime.now(UTC)
        feedback_id = existing.id
    else:
        # Create new
        feedback_id = str(uuid.uuid4())
        feedback = AnnotationFeedbackRow(
            id=feedback_id,
            user_id=user_id,
            resume_id=resume_id,
            vacancy_id=vacancy_a_id,  # Primary vacancy for pointwise compatibility
            vacancy_a_id=vacancy_a_id,
            vacancy_b_id=vacancy_b_id,
            feedback_type="pairwise",
            label=preference,
            reasons=[],  # Not used for pairwise
            a_reasons=a_reasons,
            b_reasons=b_reasons,
            comment=comment,
        )
        session.add(feedback)

    logger.info(
        "pairwise_annotation_submitted",
        user_id=user_id,
        resume_id=resume_id,
        vacancy_a_id=vacancy_a_id,
        vacancy_b_id=vacancy_b_id,
        preference=preference,
    )

    return FeedbackResponse(
        status="accepted",
        feedback_id=feedback_id,
        label=preference,
    )


def get_annotation_stats(
    session: Session,
    user_id: str | None = None,
) -> AnnotationStats:
    """Get annotation statistics."""
    from app.storage.tables import AnnotationFeedbackRow
    from sqlalchemy import select, func

    base_stmt = select(AnnotationFeedbackRow)
    if user_id:
        base_stmt = base_stmt.where(AnnotationFeedbackRow.user_id == user_id)

    # Pointwise stats
    pw_stmt = base_stmt.where(AnnotationFeedbackRow.feedback_type == "pointwise")
    pw_results = session.execute(pw_stmt).scalars().all()
    pointwise_by_label: dict[str, int] = {}
    for r in pw_results:
        pointwise_by_label[r.label] = pointwise_by_label.get(r.label, 0) + 1

    # Pairwise stats
    pws_stmt = base_stmt.where(AnnotationFeedbackRow.feedback_type == "pairwise")
    pws_results = session.execute(pws_stmt).scalars().all()
    pairwise_by_label: dict[str, int] = {}
    for r in pws_results:
        pairwise_by_label[r.label] = pairwise_by_label.get(r.label, 0) + 1

    unique_resumes = len(set(r.resume_id for r in pw_results + pws_results))
    unique_vacancies = len(set(r.vacancy_id for r in pw_results + pws_results))

    return AnnotationStats(
        total_pointwise=len(pw_results),
        total_pairwise=len(pws_results),
        pointwise_by_label=pointwise_by_label,
        pairwise_by_label=pairwise_by_label,
        unique_resumes=unique_resumes,
        unique_vacancies=unique_vacancies,
    )


def get_dataset_readiness(
    session: Session,
) -> DatasetReadiness:
    """Get dataset readiness for training."""
    from app.storage.tables import AnnotationFeedbackRow
    from sqlalchemy import select, func

    # Count pointwise observations per resume group
    pw_stmt = select(
        AnnotationFeedbackRow.resume_id,
        func.count(AnnotationFeedbackRow.id)
    ).where(AnnotationFeedbackRow.feedback_type == "pointwise").group_by(AnnotationFeedbackRow.resume_id)
    pw_counts = dict(session.execute(pw_stmt).all())

    # Count pairwise observations per resume group
    pws_stmt = select(
        AnnotationFeedbackRow.resume_id,
        func.count(AnnotationFeedbackRow.id)
    ).where(AnnotationFeedbackRow.feedback_type == "pairwise").group_by(AnnotationFeedbackRow.resume_id)
    pws_counts = dict(session.execute(pws_stmt).all())

    all_resume_ids = set(pw_counts.keys()) | set(pws_counts.keys())
    total_pointwise = sum(pw_counts.values())
    total_pairwise = sum(pws_counts.values())

    min_obs = min(
        [pw_counts.get(rid, 0) + pws_counts.get(rid, 0) for rid in all_resume_ids],
        default=0
    )

    # Ready if at least 2 resume groups with >= 50 observations each
    ready = len(all_resume_ids) >= 2 and min_obs >= 50

    return DatasetReadiness(
        pointwise_observations=total_pointwise,
        pairwise_observations=total_pairwise,
        total_observations=total_pointwise + total_pairwise,
        unique_resume_groups=len(all_resume_ids),
        min_observations_per_group=min_obs,
        ready_for_training=ready,
    )