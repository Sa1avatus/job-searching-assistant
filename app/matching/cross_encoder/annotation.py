"""Human annotation for LTR: review queue, feedback submission, statistics, dataset export.

* The review queue is a *union of strata* (top by the current ranking, top by the LTR
  ranking, largest rank disagreement, mid-ranking, random) so the human sees the cases that
  teach the model the most. Each item carries an explainable ``review_priority`` (a
  heuristic, not a probability) and no single company may dominate the queue.
* Feedback is validated by ``app.domain.annotation``. A judgement exists once: pointwise by
  (user, resume, vacancy), pairwise by (user, resume, canonical pair); the database enforces
  it with partial unique indexes and a concurrent double submit falls back to an update.
* Only decisive pairwise answers become training pairs; ``both_equal`` / ``neither`` never do.
* Sampling is deterministic (seed 42, stable tie-breaks by vacancy id).
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.annotation import (
    POINTWISE_GAIN,
    InvalidAnnotation,
    canonicalize_pair,
    limit_bucket_dominance,
    normalize_comment,
    normalize_reasons,
    review_priority,
    training_pair,
    validate_confidence,
    validate_pairwise_label,
    validate_pointwise_label,
)
from app.matching.cross_encoder.features import MatchFeatures
from app.matching.cross_encoder.normalization import ScoreNormalizer
from app.storage.tables import (
    AnnotationFeedbackRow,
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
MAX_COMPANY_SHARE = 0.3
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
    # Heuristic queue-ordering signal in [0, 1]; NOT a probability or a calibrated confidence.
    review_priority: float = 0.0
    priority_reasons: list[str] = Field(default_factory=list)
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


class PairQueueItem(BaseModel):
    """Two vacancies a human should compare head to head, and why this pair."""

    vacancy_a: AnnotationQueueItem
    vacancy_b: AnnotationQueueItem
    reason: str  # close_scores | rankings_reversed


class PairQueue(BaseModel):
    resume_id: str
    items: list[PairQueueItem]
    total_candidates: int


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
    code: str | None = None
    detail: str | None = None


class ExportIssue(BaseModel):
    feedback_id: str
    code: str
    message: str


class DatasetExport(BaseModel):
    """Validated, reproducible snapshot of the human labels.

    ``dataset_hash`` covers only the exported rows, in a canonical order, so the same labels
    always give the same hash.
    """

    dataset_hash: str
    pointwise: list[dict[str, Any]]
    pairs: list[dict[str, Any]]
    undecided_pairs: int
    issues: list[ExportIssue]


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
    review_priority: float = 0.0
    priority_reasons: list[str] = field(default_factory=list)

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
            review_priority=self.review_priority,
            priority_reasons=list(self.priority_reasons),
            required_skills=self.required_skills,
            preferred_skills=self.preferred_skills,
            salary_text=self.salary_text,
            work_format=self.work_format,
            employment_types=self.employment_types,
        )


# ── queue ──────────────────────────────────────────────────────────────────────


def get_annotation_queue(
    session: Session,
    user_id: str,
    resume_id: str,
    features_list: list[MatchFeatures],
    *,
    limit: int = DEFAULT_LIMIT,
    vacancy_meta: dict[str, dict[str, Any]] | None = None,
    resume_text: str = "",
    resume_skills: list[str] | None = None,
) -> AnnotationQueue:
    """Stratified, prioritised, diversity-limited queue for one resume the user owns."""
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

    all_candidates = _build_candidate_pool(
        session, resume_id, features_list, vacancy_meta or {}, user_id=user_id
    )
    eligible = _exclude_pointwise_annotated(session, user_id, resume_id, all_candidates)
    sampled = _stratified_sample(eligible, limit=limit)

    strata_breakdown: dict[str, int] = {}
    for candidate in sampled:
        strata_breakdown[candidate.sampling_reason] = (
            strata_breakdown.get(candidate.sampling_reason, 0) + 1
        )
    return AnnotationQueue(
        resume_id=resume_id,
        resume_filename=cv.original_filename,
        items=[candidate.to_queue_item() for candidate in sampled],
        total_eligible=len(eligible),
        sampled_count=len(sampled),
        strata_breakdown=strata_breakdown,
    )


# Only these match statuses carry a real score; pending/failed rows hold placeholders.
RANKABLE_STATUSES = ("scored", "degraded")


def resume_match_results_statement(
    session: Session, user_id: str, resume_id: str
) -> Select[tuple[ApplicationMatchResultRow, VacancyRow, ApplicationRow]]:
    """Scored match results that belong to (user, resume) - the annotation candidate pool.

    An application counts for a resume when it selected that resume, or when it selected none
    and the resume is the user's active one: matching does not require an explicit selection,
    so feedback must not depend on it either.
    """
    user = session.get(UserRow, user_id)
    belongs = ApplicationRow.selected_cv_file_id == resume_id
    if user is not None and user.active_cv_file_id == resume_id:
        belongs = or_(belongs, ApplicationRow.selected_cv_file_id.is_(None))
    return (
        select(ApplicationMatchResultRow, VacancyRow, ApplicationRow)
        .join(ApplicationRow, ApplicationMatchResultRow.application_id == ApplicationRow.id)
        .join(VacancyRow, ApplicationRow.vacancy_id == VacancyRow.id)
        .where(
            ApplicationRow.user_id == user_id,
            ApplicationMatchResultRow.status.in_(RANKABLE_STATUSES),
            belongs,
        )
        .order_by(VacancyRow.id, ApplicationMatchResultRow.application_id)
    )


def _build_candidate_pool(
    session: Session,
    resume_id: str,
    features_list: list[MatchFeatures],
    vacancy_meta: dict[str, dict[str, Any]],
    *,
    user_id: str | None = None,
) -> list[SampledCandidate]:
    """All rankable vacancies for the resume, with deterministic current/LTR ranks."""
    features_by_vacancy = {f.vacancy_id: f for f in features_list}
    if user_id is None:
        owner = session.get(CvFileRow, resume_id)
        user_id = owner.user_id if owner is not None else ""
    statement = resume_match_results_statement(session, user_id, resume_id)

    by_vacancy: dict[str, SampledCandidate] = {}
    for match_result, vacancy, _application in session.execute(statement).all():
        if vacancy.id not in features_by_vacancy:
            continue
        current_score = match_result.final_score
        candidate = by_vacancy.get(vacancy.id)
        if candidate is not None and (candidate.current_score or 0) >= (current_score or 0):
            continue  # several applications for one vacancy: keep the best-scored one
        by_vacancy[vacancy.id] = SampledCandidate(
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
            current_rank=None,
            ltr_rank=None,
            current_score=current_score,
            ltr_score=getattr(match_result, "ltr_score", None),
            sampling_reason="",
            strata=[],
        )

    candidates = list(by_vacancy.values())
    # equal scores must not depend on database row order: break ties by vacancy id
    candidates.sort(key=lambda c: (-(c.current_score or 0.0), c.vacancy_id))
    for rank, candidate in enumerate(candidates, start=1):
        candidate.current_rank = rank
    ltr_ranked = sorted(
        (c for c in candidates if c.ltr_score is not None),
        key=lambda c: (-(c.ltr_score or 0.0), c.vacancy_id),
    )
    for rank, candidate in enumerate(ltr_ranked, start=1):
        candidate.ltr_rank = rank
    return candidates


def _exclude_pointwise_annotated(
    session: Session,
    user_id: str,
    resume_id: str,
    candidates: list[SampledCandidate],
) -> list[SampledCandidate]:
    """Drop vacancies this user already labelled pointwise for this resume."""
    annotated = _get_pointwise_annotated_vacancies(session, user_id, resume_id)
    eligible = [c for c in candidates if c.vacancy_id not in annotated]
    logger.info(
        "annotation_excluded_pointwise",
        resume_id=resume_id,
        total=len(candidates),
        excluded=len(candidates) - len(eligible),
        eligible=len(eligible),
    )
    return eligible


def _get_pointwise_annotated_vacancies(session: Session, user_id: str, resume_id: str) -> set[str]:
    statement = select(AnnotationFeedbackRow.vacancy_id).where(
        AnnotationFeedbackRow.user_id == user_id,
        AnnotationFeedbackRow.resume_id == resume_id,
        AnnotationFeedbackRow.feedback_type == "pointwise",
    )
    return set(session.execute(statement).scalars().all())


def _percentiles(
    candidates: list[SampledCandidate],
) -> dict[str, tuple[float | None, float | None]]:
    """Percentile of each candidate's current/LTR score within the SAME pool (1 = best).

    Both rankings go through one normaliser, so a 0-100 production score and an unbounded
    LTR score become comparable before they are used to measure disagreement.
    """
    normalizer = ScoreNormalizer()
    normalizer.fit("current", [c.current_score for c in candidates if c.current_score is not None])
    normalizer.fit("ltr", [c.ltr_score for c in candidates if c.ltr_score is not None])
    return {
        c.vacancy_id: (
            normalizer.transform("current", c.current_score),
            normalizer.transform("ltr", c.ltr_score),
        )
        for c in candidates
    }


def _stratified_sample(
    candidates: list[SampledCandidate],
    limit: int = DEFAULT_LIMIT,
) -> list[SampledCandidate]:
    """Union of strata, ordered by explainable priority, with company diversity.

    Strata (each contributes at most its quota of *members*, overlaps included):
      rank_disagreement (30) largest |current_rank - ltr_rank|; ltr_top (20); current_top (20);
      middle_rank (20) mid-ranking (ranks ~n/4 .. 3n/4, capped at 50..300); random (10).
    Result order: review_priority desc, then stratum priority, current rank, vacancy id.
    """
    if not candidates:
        return []

    pool = sorted(candidates, key=lambda c: (c.current_rank or float("inf"), c.vacancy_id))
    for candidate in pool:
        candidate.strata = []
        candidate.sampling_reason = ""
    selected: dict[str, SampledCandidate] = {}

    def take(stratum: str, ordered: list[SampledCandidate]) -> None:
        for candidate in ordered[: STRATA_QUOTAS[stratum]]:
            candidate.strata.append(stratum)
            selected.setdefault(candidate.vacancy_id, candidate)

    disagreement = sorted(
        (c for c in pool if c.current_rank and c.ltr_rank),
        key=lambda c: (
            -abs((c.current_rank or 0) - (c.ltr_rank or 0)),
            c.current_rank or float("inf"),
            c.vacancy_id,
        ),
    )
    take("rank_disagreement", disagreement)
    take(
        "ltr_top",
        sorted((c for c in pool if c.ltr_rank), key=lambda c: (c.ltr_rank or 0, c.vacancy_id)),
    )
    take("current_top", pool)

    n = len(pool)
    middle_start, middle_end = min(50, n // 4), min(300, 3 * n // 4)
    take("middle_rank", [c for c in pool if middle_start < (c.current_rank or n + 1) <= middle_end])

    remaining = [c for c in pool if c.vacancy_id not in selected]
    random.Random(SEED).shuffle(remaining)
    take("random", remaining)

    for candidate in selected.values():
        candidate.sampling_reason = next(s for s in STRATA_PRIORITY if s in candidate.strata)

    result = list(selected.values())
    if len(result) < limit:  # top up from the best-ranked leftovers
        for candidate in pool:
            if len(result) >= limit:
                break
            if candidate.vacancy_id not in selected:
                candidate.strata.append("fill")
                candidate.sampling_reason = "fill"
                result.append(candidate)

    percentiles = _percentiles(pool)
    for candidate in result:
        current_pct, ltr_pct = percentiles[candidate.vacancy_id]
        priority = review_priority(
            current_pct=current_pct,
            ltr_pct=ltr_pct,
            strata=[s for s in candidate.strata if s != "fill"],
        )
        candidate.review_priority = priority.score
        candidate.priority_reasons = list(priority.reasons)

    def order(candidate: SampledCandidate) -> tuple[float, int, float, str]:
        stratum = (
            STRATA_PRIORITY.index(candidate.sampling_reason)
            if candidate.sampling_reason in STRATA_PRIORITY
            else len(STRATA_PRIORITY)
        )
        return (
            -candidate.review_priority,
            stratum,
            candidate.current_rank or float("inf"),
            candidate.vacancy_id,
        )

    result.sort(key=order)
    return limit_bucket_dominance(
        result,
        bucket=lambda c: c.vacancy_company.casefold().strip(),
        limit=limit,
        max_share=MAX_COMPANY_SHARE,
    )


def load_features_from_db(session: Session, user_id: str, resume_id: str) -> list[MatchFeatures]:
    """Build match features for the (user, resume) candidate pool straight from the database.

    Only what the database holds is available (existing pipeline scores and vacancy metadata);
    cross-encoder scores and requirement matches are absent and stay ``None``, never 0.
    """
    from app.matching.cross_encoder.features import FeatureExtractor

    results = session.execute(resume_match_results_statement(session, user_id, resume_id)).all()
    if not results:
        return []

    existing_scores: dict[str, dict[str, Any]] = {}
    vacancy_meta: dict[str, dict[str, Any]] = {}
    for match_result, vacancy, _application in results:
        existing_scores[f"{resume_id}|{vacancy.id}"] = {
            "match_score": match_result.final_score,
            "reranker_score": getattr(match_result, "reranker_score", None),
            "semantic_similarity": getattr(match_result, "semantic_similarity", None),
        }
        vacancy_meta[vacancy.id] = {
            "id": vacancy.id,
            "title": vacancy.title,
            "company": vacancy.company,
            "location": vacancy.location,
            "description_text": vacancy.description_text,
            "required_skills": vacancy.required_skills or [],
            "preferred_skills": vacancy.preferred_skills or [],
            "salary_text": vacancy.salary_text,
            "work_format": vacancy.work_format,
            "employment_types": vacancy.employment_types or [],
        }
    extractor = FeatureExtractor(
        requirement_matches={},
        existing_scores=existing_scores,
        vacancy_meta=vacancy_meta,
        cross_encoder_scores={},
    )
    return [extractor.extract(resume_id, vacancy.id) for _, vacancy, _ in results]


MAX_PAIR_APPEARANCES = 2


def get_pair_queue(
    session: Session,
    user_id: str,
    resume_id: str,
    features_list: list[MatchFeatures],
    *,
    limit: int = 20,
    vacancy_meta: dict[str, dict[str, Any]] | None = None,
) -> PairQueue:
    """Hard pairs to compare: neighbours with near-equal scores, then pairs the current and
    LTR rankings order oppositely. Already-judged pairs (in either order) are skipped and no
    vacancy is shown more than twice, so one item cannot dominate the session."""
    cv = session.get(CvFileRow, resume_id)
    if not cv or cv.user_id != user_id:
        return PairQueue(resume_id=resume_id, items=[], total_candidates=0)

    pool = _build_candidate_pool(
        session, resume_id, features_list, vacancy_meta or {}, user_id=user_id
    )
    judged = set(
        session.execute(
            select(AnnotationFeedbackRow.pair_key).where(
                AnnotationFeedbackRow.user_id == user_id,
                AnnotationFeedbackRow.resume_id == resume_id,
                AnnotationFeedbackRow.feedback_type == "pairwise",
            )
        ).scalars()
    )

    candidates: list[tuple[float, str, SampledCandidate, SampledCandidate]] = []
    for upper, lower in zip(pool, pool[1:], strict=False):  # pool is sorted by current rank
        gap = abs((upper.current_score or 0.0) - (lower.current_score or 0.0))
        candidates.append((gap, "close_scores", upper, lower))
    with_ltr = [c for c in pool if c.ltr_rank is not None]
    for i, first in enumerate(with_ltr):
        for second in with_ltr[i + 1 : i + 1 + 25]:
            if (first.ltr_rank or 0) > (second.ltr_rank or 0):  # LTR disagrees with current
                gap = 1.0 / (1 + (first.ltr_rank or 0) - (second.ltr_rank or 0))
                candidates.append((gap, "rankings_reversed", first, second))
    candidates.sort(key=lambda c: (c[0], c[1], c[2].vacancy_id, c[3].vacancy_id))

    shown: dict[str, int] = {}
    items: list[PairQueueItem] = []
    seen_keys: set[str] = set()
    for _gap, reason, first, second in candidates:
        key = ":".join(sorted((first.vacancy_id, second.vacancy_id)))
        if key in judged or key in seen_keys:
            continue
        if any(shown.get(c.vacancy_id, 0) >= MAX_PAIR_APPEARANCES for c in (first, second)):
            continue
        seen_keys.add(key)
        for c in (first, second):
            shown[c.vacancy_id] = shown.get(c.vacancy_id, 0) + 1
        items.append(
            PairQueueItem(
                vacancy_a=first.to_queue_item(), vacancy_b=second.to_queue_item(), reason=reason
            )
        )
        if len(items) >= limit:
            break
    return PairQueue(resume_id=resume_id, items=items, total_candidates=len(candidates))


# ── submission ─────────────────────────────────────────────────────────────────


def _invalid(error: InvalidAnnotation) -> FeedbackResponse:
    return FeedbackResponse(status="invalid", code=error.code, detail=error.message)


def _owns(session: Session, user_id: str, resume_id: str) -> CvFileRow | None:
    cv = session.get(CvFileRow, resume_id)
    return cv if cv is not None and cv.user_id == user_id else None


def _apply_sampling_context(row: AnnotationFeedbackRow, sampling: dict[str, Any] | None) -> None:
    if not sampling:
        return
    row.sampling_reason = sampling.get("sampling_reason")
    row.current_rank_at_sampling = sampling.get("current_rank")
    row.ltr_rank_at_sampling = sampling.get("ltr_rank")
    row.current_score_at_sampling = sampling.get("current_score")
    row.ltr_score_at_sampling = sampling.get("ltr_score")


def _persist(
    session: Session,
    lookup: Select[tuple[AnnotationFeedbackRow]],
    build: Callable[[], AnnotationFeedbackRow],
    update: Callable[[AnnotationFeedbackRow], None],
) -> AnnotationFeedbackRow:
    """Insert-or-update that survives a concurrent double submit.

    The unique index decides the race: the loser's INSERT fails inside a SAVEPOINT, and it
    then updates the winner's row instead of erroring or creating a duplicate.
    """
    existing = session.execute(lookup).scalar_one_or_none()
    if existing is not None:
        update(existing)
        return existing
    row = build()
    try:
        with session.begin_nested():
            session.add(row)
    except IntegrityError:
        winner = session.execute(lookup).scalar_one()
        update(winner)
        return winner
    return row


def submit_pointwise(
    session: Session,
    user_id: str,
    resume_id: str,
    vacancy_id: str,
    label: str,
    reasons: list[str],
    comment: str | None,
    *,
    confidence: str | None = None,
    sampling: dict[str, Any] | None = None,
) -> FeedbackResponse:
    """Record (or update) the human pointwise judgement of one resume-vacancy pair.

    Flushes only; the caller commits, so a failure leaves nothing half written.
    """
    if _owns(session, user_id, resume_id) is None:
        return FeedbackResponse(status="forbidden")
    try:
        validate_pointwise_label(label)
        clean_reasons = normalize_reasons(reasons)
        clean_comment = normalize_comment(comment)
        validate_confidence(confidence)
    except InvalidAnnotation as error:
        return _invalid(error)
    if session.get(VacancyRow, vacancy_id) is None:
        return FeedbackResponse(
            status="invalid", code="vacancy_not_found", detail="Vacancy not found"
        )

    lookup = select(AnnotationFeedbackRow).where(
        AnnotationFeedbackRow.user_id == user_id,
        AnnotationFeedbackRow.resume_id == resume_id,
        AnnotationFeedbackRow.vacancy_id == vacancy_id,
        AnnotationFeedbackRow.feedback_type == "pointwise",
    )

    def build() -> AnnotationFeedbackRow:
        row = AnnotationFeedbackRow(
            user_id=user_id,
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            feedback_type="pointwise",
            annotator_id=user_id,
            source="dashboard",
        )
        update(row)
        return row

    def update(row: AnnotationFeedbackRow) -> None:
        row.label = label
        row.reasons = clean_reasons
        row.comment = clean_comment
        row.confidence = confidence
        _apply_sampling_context(row, sampling)

    row = _persist(session, lookup, build, update)
    logger.info(
        "pointwise_annotation_submitted",
        user_id=user_id,
        resume_id=resume_id,
        vacancy_id=vacancy_id,
        label=label,
    )
    return FeedbackResponse(status="accepted", feedback_id=row.id, label=label)


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
    *,
    confidence: str | None = None,
    sampling: dict[str, Any] | None = None,
) -> FeedbackResponse:
    """Record (or update) a pairwise preference under its canonical identity.

    The same two vacancies shown in the opposite order are the same judgement: the pair is
    stored smaller-id-first and the label/reasons are flipped with it, so a reason always
    stays attached to the vacancy it was written about.
    """
    if _owns(session, user_id, resume_id) is None:
        return FeedbackResponse(status="forbidden")
    try:
        clean_a = normalize_reasons(a_reasons)
        clean_b = normalize_reasons(b_reasons)
        clean_comment = normalize_comment(comment)
        validate_confidence(confidence)
        pair = canonicalize_pair(vacancy_a_id, vacancy_b_id, preference, clean_a, clean_b)
    except InvalidAnnotation as error:
        return _invalid(error)
    if (
        session.get(VacancyRow, vacancy_a_id) is None
        or session.get(VacancyRow, vacancy_b_id) is None
    ):
        return FeedbackResponse(
            status="invalid", code="vacancy_not_found", detail="Vacancy not found"
        )

    lookup = select(AnnotationFeedbackRow).where(
        AnnotationFeedbackRow.user_id == user_id,
        AnnotationFeedbackRow.resume_id == resume_id,
        AnnotationFeedbackRow.pair_key == pair.pair_key,
        AnnotationFeedbackRow.feedback_type == "pairwise",
    )

    def build() -> AnnotationFeedbackRow:
        row = AnnotationFeedbackRow(
            user_id=user_id,
            resume_id=resume_id,
            vacancy_id=pair.first_id,
            vacancy_a_id=pair.first_id,
            vacancy_b_id=pair.second_id,
            pair_key=pair.pair_key,
            feedback_type="pairwise",
            reasons=[],
            annotator_id=user_id,
            source="dashboard",
        )
        update(row)
        return row

    def update(row: AnnotationFeedbackRow) -> None:
        row.label = pair.label
        row.a_reasons = pair.first_reasons
        row.b_reasons = pair.second_reasons
        row.comment = clean_comment
        row.confidence = confidence
        _apply_sampling_context(row, sampling)

    row = _persist(session, lookup, build, update)
    logger.info(
        "pairwise_annotation_submitted",
        user_id=user_id,
        resume_id=resume_id,
        pair_key=pair.pair_key,
        preference=pair.label,
    )
    return FeedbackResponse(status="accepted", feedback_id=row.id, label=pair.label)


# ── statistics, readiness, export ──────────────────────────────────────────────


def get_annotation_stats(session: Session, user_id: str | None = None) -> AnnotationStats:
    """Label counts (aggregated in SQL) and coverage."""
    scope = [AnnotationFeedbackRow.user_id == user_id] if user_id else []
    by_label = session.execute(
        select(
            AnnotationFeedbackRow.feedback_type,
            AnnotationFeedbackRow.label,
            func.count(),
        )
        .where(*scope)
        .group_by(AnnotationFeedbackRow.feedback_type, AnnotationFeedbackRow.label)
    ).all()
    pointwise = {label: count for kind, label, count in by_label if kind == "pointwise"}
    pairwise = {label: count for kind, label, count in by_label if kind == "pairwise"}

    resumes = set(
        session.execute(select(AnnotationFeedbackRow.resume_id).where(*scope).distinct()).scalars()
    )
    vacancies: set[str] = set()
    for column in (
        AnnotationFeedbackRow.vacancy_id,
        AnnotationFeedbackRow.vacancy_a_id,
        AnnotationFeedbackRow.vacancy_b_id,
    ):
        vacancies |= {
            v for v in session.execute(select(column).where(*scope).distinct()).scalars() if v
        }
    return AnnotationStats(
        total_pointwise=sum(pointwise.values()),
        total_pairwise=sum(pairwise.values()),
        pointwise_by_label=pointwise,
        pairwise_by_label=pairwise,
        unique_resumes=len(resumes),
        unique_vacancies=len(vacancies),
    )


def get_dataset_readiness(session: Session) -> DatasetReadiness:
    """Readiness for training: at least 2 resume groups with >= 50 observations each."""
    counts: dict[tuple[str, str], int] = {
        (resume_id, kind): count
        for resume_id, kind, count in session.execute(
            select(
                AnnotationFeedbackRow.resume_id,
                AnnotationFeedbackRow.feedback_type,
                func.count(AnnotationFeedbackRow.id),
            ).group_by(AnnotationFeedbackRow.resume_id, AnnotationFeedbackRow.feedback_type)
        ).all()
    }
    resume_ids = {resume_id for resume_id, _ in counts}
    total_pointwise = sum(c for (_, kind), c in counts.items() if kind == "pointwise")
    total_pairwise = sum(c for (_, kind), c in counts.items() if kind == "pairwise")
    min_obs = min(
        (counts.get((r, "pointwise"), 0) + counts.get((r, "pairwise"), 0) for r in resume_ids),
        default=0,
    )
    return DatasetReadiness(
        pointwise_observations=total_pointwise,
        pairwise_observations=total_pairwise,
        total_observations=total_pointwise + total_pairwise,
        unique_resume_groups=len(resume_ids),
        min_observations_per_group=min_obs,
        ready_for_training=len(resume_ids) >= 2 and min_obs >= 50,
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def export_dataset(session: Session, user_id: str | None = None) -> DatasetExport:
    """Validated export of human labels; anything doubtful is reported, never exported.

    Checks: label vocabulary, reason tags, owner (resume must belong to the labelling user),
    canonical pair consistency, timestamps, duplicates. Undecided pairwise answers are counted
    but never turned into training pairs.
    """
    statement = select(AnnotationFeedbackRow).order_by(
        AnnotationFeedbackRow.user_id,
        AnnotationFeedbackRow.resume_id,
        AnnotationFeedbackRow.feedback_type,
        AnnotationFeedbackRow.vacancy_id,
        AnnotationFeedbackRow.pair_key,
        AnnotationFeedbackRow.id,
    )
    if user_id:
        statement = statement.where(AnnotationFeedbackRow.user_id == user_id)
    rows = session.execute(statement).scalars().all()
    resume_owner = {
        cv_id: owner
        for cv_id, owner in session.execute(select(CvFileRow.id, CvFileRow.user_id)).all()
    }

    issues: list[ExportIssue] = []
    pointwise: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    undecided = 0

    for row in rows:
        problem: tuple[str, str] | None = None
        try:
            if row.feedback_type == "pointwise":
                validate_pointwise_label(row.label)
            elif row.feedback_type == "pairwise":
                validate_pairwise_label(row.label)
            else:
                problem = ("unknown_type", f"Unknown feedback type {row.feedback_type!r}")
            normalize_reasons(
                [*(row.reasons or []), *(row.a_reasons or []), *(row.b_reasons or [])]
            )
        except InvalidAnnotation as error:
            problem = (error.code, error.message)
        if problem is None and resume_owner.get(row.resume_id) != row.user_id:
            problem = ("ownership_mismatch", "The resume does not belong to the labelling user")
        created, updated = _aware(row.created_at), _aware(row.updated_at)
        if problem is None and (created is None or updated is None or updated < created):
            problem = ("bad_timestamps", "created_at/updated_at are missing or out of order")
        if problem is None and row.feedback_type == "pairwise":
            first, second = row.vacancy_a_id, row.vacancy_b_id
            if not first or not second or first >= second or row.pair_key != f"{first}:{second}":
                problem = ("non_canonical_pair", "Pair is not stored in canonical order")
        identity = (
            (row.user_id, row.resume_id, "pointwise", row.vacancy_id)
            if row.feedback_type == "pointwise"
            else (row.user_id, row.resume_id, "pairwise", row.pair_key or "")
        )
        if problem is None and identity in seen:
            problem = ("duplicate", "A second label exists for the same logical judgement")
        if problem is not None:
            issues.append(ExportIssue(feedback_id=row.id, code=problem[0], message=problem[1]))
            continue
        seen.add(identity)

        common = {
            "user_id": row.user_id,
            "resume_id": row.resume_id,
            "annotator_id": row.annotator_id or row.user_id,
            "source": row.source,
            "confidence": row.confidence,
            "created_at": created.isoformat() if created else None,
            "sampling_reason": row.sampling_reason,
            "current_rank": row.current_rank_at_sampling,
        }
        if row.feedback_type == "pointwise":
            pointwise.append(
                {
                    **common,
                    "vacancy_id": row.vacancy_id,
                    "label": row.label,
                    "gain": POINTWISE_GAIN[row.label],
                    "reasons": list(row.reasons or []),
                }
            )
            continue
        decided = training_pair(row.vacancy_a_id, row.vacancy_b_id, row.label)
        if decided is None:
            undecided += 1
            continue
        winner, loser = decided
        winner_reasons = row.a_reasons if winner == row.vacancy_a_id else row.b_reasons
        loser_reasons = row.b_reasons if winner == row.vacancy_a_id else row.a_reasons
        pairs.append(
            {
                **common,
                "winner_id": winner,
                "loser_id": loser,
                "winner_reasons": list(winner_reasons or []),
                "loser_reasons": list(loser_reasons or []),
            }
        )

    canonical = json.dumps({"pointwise": pointwise, "pairs": pairs}, sort_keys=True)
    return DatasetExport(
        dataset_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        pointwise=pointwise,
        pairs=pairs,
        undecided_pairs=undecided,
        issues=issues,
    )
