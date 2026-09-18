"""Annotation API endpoints for human feedback collection.

Endpoints:
    GET  /v1/annotation/discover            - List users + resumes for UI dropdowns
    GET  /v1/annotation/queue               - Active learning review queue (pairwise)
    GET  /v1/annotation/pointwise-sample    - Stratified pointwise sampling queue
    POST /v1/annotation/pointwise           - Submit pointwise feedback
    POST /v1/annotation/pairwise            - Submit pairwise feedback
    GET  /v1/annotation/stats               - Annotation statistics
    GET  /v1/annotation/readiness           - Dataset readiness

All endpoints require x-api-key header (existing auth pattern).
Ownership verified via CvFileRow.user_id.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

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
from app.matching.cross_encoder.features import FeatureExtractor, MatchFeatures
from app.matching.cross_encoder.normalization import ScoreNormalizer
from app.storage.database import session_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/annotation", tags=["annotation"])


# ── Discovery models ───────────────────────────────────────────────────

class ResumeInfo(BaseModel):
    resume_id: str
    filename: str
    user_id: str


class UserInfo(BaseModel):
    user_id: str
    display_name: str
    resumes: list[ResumeInfo]


class DiscoveryResponse(BaseModel):
    users: list[UserInfo]
    total_users: int
    total_resumes: int


# ── Discovery endpoint ─────────────────────────────────────────────────

@router.get("/discover", response_model=DiscoveryResponse)
def discover_users_and_resumes(
    session: Session = Depends(session_scope),
) -> DiscoveryResponse:
    """List all users and their resumes for UI dropdown population.

    Returns users with CV files so the annotation UI can present
    a selection dropdown instead of requiring manual UUID entry.
    """
    from app.storage.tables import UserRow, CvFileRow

    users = session.query(UserRow).all()
    result_users = []
    total_resumes = 0

    for user in users:
        cv_files = session.query(CvFileRow).filter(
            CvFileRow.user_id == user.id
        ).all()
        resumes = [
            ResumeInfo(
                resume_id=cv.id,
                filename=cv.original_filename,
                user_id=user.id,
            )
            for cv in cv_files
        ]
        if resumes:  # Only include users with CV files
            result_users.append(UserInfo(
                user_id=user.id,
                display_name=user.display_name or user.id[:12],
                resumes=resumes,
            ))
            total_resumes += len(resumes)

    return DiscoveryResponse(
        users=result_users,
        total_users=len(result_users),
        total_resumes=total_resumes,
    )


# ── Request models ─────────────────────────────────────────────────────

class PointwiseSubmitRequest(BaseModel):
    resume_id: str = Field(..., min_length=1)
    vacancy_id: str = Field(..., min_length=1)
    label: str = Field(..., pattern="^(relevant|maybe|not_relevant)$")
    reasons: list[str] = Field(default_factory=list)
    comment: str | None = Field(default=None, max_length=2000)


class PairwiseSubmitRequest(BaseModel):
    resume_id: str = Field(..., min_length=1)
    vacancy_a_id: str = Field(..., min_length=1)
    vacancy_b_id: str = Field(..., min_length=1)
    preference: str = Field(..., pattern="^(a_better|b_better|both_equal|neither)$")
    a_reasons: list[str] = Field(default_factory=list)
    b_reasons: list[str] = Field(default_factory=list)
    comment: str | None = Field(default=None, max_length=2000)


# ── Pointwise Sample endpoint ──────────────────────────────────────────

@router.get("/pointwise-sample", response_model=AnnotationQueue)
def get_pointwise_sample(
    user_id: str = Query(..., min_length=1),
    resume_id: str = Query(..., min_length=1),
    limit: int = Query(default=100, ge=1, le=200),
    session: Session = Depends(session_scope),
) -> AnnotationQueue:
    """Get stratified pointwise annotation sample for a resume.

    Returns up to 100 vacancies sampled from five strata:
    - current_top (20): top by existing ranking
    - ltr_top (20): top by LTR ranking
    - rank_disagreement (30): largest rank disagreement
    - middle_rank (20): middle of ranking
    - random (10): random from remaining

    Excludes already pointwise-annotated vacancies.
    Pairwise-only annotated vacancies remain eligible.
    Sampling is deterministic (seed=42).
    """
    from app.storage.tables import CvFileRow
    import json as _json

    # Load features from DB (reuse existing data)
    features_list = _load_features_for_resume(session, user_id, resume_id)
    vacancy_meta = _load_vacancy_meta(session)

    # Load resume text and skills from DB
    cv = session.query(CvFileRow).filter(CvFileRow.id == resume_id).first()
    resume_text = cv.experience_summary if cv else ""
    resume_skills = []
    if cv and cv.skills:
        try:
            resume_skills = _json.loads(cv.skills) if isinstance(cv.skills, str) else list(cv.skills)
        except (TypeError, _json.JSONDecodeError):
            resume_skills = []

    return get_annotation_queue(
        session, user_id, resume_id, features_list,
        limit=limit, vacancy_meta=vacancy_meta,
        resume_text=resume_text or "",
        resume_skills=resume_skills,
    )


# ── Queue endpoint ─────────────────────────────────────────────────────

@router.get("/queue", response_model=AnnotationQueue)
def get_queue(
    user_id: str = Query(..., min_length=1),
    resume_id: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=200),
    session: Session = Depends(session_scope),
) -> AnnotationQueue:
    """Get prioritized annotation queue for a resume.

    Returns candidates ranked by review_priority (information gain),
    excluding already-labelled pairs.
    """
    from app.storage.tables import CvFileRow
    import json as _json

    # Load features from DB (reuse existing data)
    features_list = _load_features_for_resume(session, user_id, resume_id)
    vacancy_meta = _load_vacancy_meta(session)

    # Load resume text and skills from DB
    cv = session.query(CvFileRow).filter(CvFileRow.id == resume_id).first()
    resume_text = cv.experience_summary if cv else ""
    resume_skills = []
    if cv and cv.skills:
        try:
            resume_skills = _json.loads(cv.skills) if isinstance(cv.skills, str) else list(cv.skills)
        except (TypeError, _json.JSONDecodeError):
            resume_skills = []

    return get_annotation_queue(
        session, user_id, resume_id, features_list,
        limit=limit, vacancy_meta=vacancy_meta,
        resume_text=resume_text or "",
        resume_skills=resume_skills,
    )


# ── Pointwise submit ───────────────────────────────────────────────────

@router.post("/pointwise", response_model=FeedbackResponse)
def submit_pointwise_endpoint(
    request: PointwiseSubmitRequest,
    user_id: str = Query(..., min_length=1),
    session: Session = Depends(session_scope),
) -> FeedbackResponse:
    """Submit pointwise human feedback for a resume-vacancy pair."""
    result = submit_pointwise(
        session, user_id,
        request.resume_id, request.vacancy_id,
        request.label, request.reasons, request.comment,
    )
    if result.status == "forbidden":
        raise HTTPException(status_code=403, detail="You do not own this resume")
    if result.status == "invalid":
        detail = f"Invalid input: {result.feedback_id}" if result.feedback_id.startswith("invalid_reason:") else f"Invalid input: {result.label}"
        raise HTTPException(status_code=422, detail=detail)
    if result.status == "accepted":
        session.commit()
    return result


# ── Pairwise submit ────────────────────────────────────────────────────

@router.post("/pairwise", response_model=FeedbackResponse)
def submit_pairwise_endpoint(
    request: PairwiseSubmitRequest,
    user_id: str = Query(..., min_length=1),
    session: Session = Depends(session_scope),
) -> FeedbackResponse:
    """Submit pairwise human preference feedback."""
    result = submit_pairwise(
        session, user_id,
        request.resume_id,
        request.vacancy_a_id, request.vacancy_b_id,
        request.preference,
        request.a_reasons, request.b_reasons,
        request.comment,
    )
    if result.status == "forbidden":
        raise HTTPException(status_code=403, detail="You do not own this resume")
    if result.status == "invalid":
        raise HTTPException(status_code=422, detail=f"Invalid input: {result.label}")
    if result.status == "accepted":
        session.commit()
    return result


# ── Statistics ─────────────────────────────────────────────────────────

@router.get("/stats", response_model=AnnotationStats)
def get_stats(
    user_id: str | None = Query(default=None),
    session: Session = Depends(session_scope),
) -> AnnotationStats:
    """Get annotation statistics."""
    return get_annotation_stats(session, user_id)


# ── Readiness ──────────────────────────────────────────────────────────

@router.get("/readiness", response_model=DatasetReadiness)
def get_readiness(
    session: Session = Depends(session_scope),
) -> DatasetReadiness:
    """Get dataset readiness for training."""
    return get_dataset_readiness(session)


# ── Helper functions ───────────────────────────────────────────────────

def _load_features_for_resume(
    session: Session, user_id: str, resume_id: str,
) -> list[MatchFeatures]:
    """Load features for all vacancies for a given resume.

    Uses existing full_corpus_scores.jsonl data if available,
    otherwise builds from DB.
    """
    import json
    from pathlib import Path
    from collections import defaultdict

    # Try to load from pre-computed data
    corpus_path = Path("data/matching/full_corpus_scores.jsonl")
    req_path = Path("data/matching/req_matches.json")
    vacancies_path = Path("data/matching/vacancies.json")

    if not corpus_path.exists():
        return _load_features_from_db(session, user_id, resume_id)

    # Load corpus
    corpus = []
    with open(corpus_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    corpus.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    # Filter for this resume
    resume_rows = [r for r in corpus if r.get("resume_id") == resume_id]
    if not resume_rows:
        return []

    # Load requirement matches
    req_by_vacancy: dict[str, list[dict]] = defaultdict(list)
    if req_path.exists():
        with open(req_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rm = json.loads(line)
                        req_by_vacancy[rm.get("vacancy_id", "")].append(rm)
                    except json.JSONDecodeError:
                        pass

    # Build existing scores
    existing_scores: dict[str, dict] = {}
    for row in resume_rows:
        key = f"{resume_id}|{row['vacancy_id']}"
        existing_scores[key] = {
            "match_score": row.get("existing_match_score"),
            "reranker_score": row.get("existing_reranker_score"),
            "semantic_similarity": row.get("existing_semantic_similarity"),
        }

    # Build CE scores
    ce_columns = [k for k in (resume_rows[0] if resume_rows else {}).keys() if k.startswith("ce_")]
    ce_scores: dict[str, dict[str, float]] = {}
    for col in ce_columns:
        model_name = col.replace("ce_", "")
        scores = {}
        for row in resume_rows:
            key = f"{resume_id}|{row['vacancy_id']}"
            val = row.get(col)
            if val is not None:
                scores[key] = val
        ce_scores[model_name] = scores

    # Fit normalizer
    normalizer = ScoreNormalizer(method="rank")
    for model_name, scores in ce_scores.items():
        normalizer.fit(model_name, list(scores.values()))

    # Load vacancy meta
    vacancy_meta: dict[str, dict] = {}
    if vacancies_path.exists():
        with open(vacancies_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        v = json.loads(line)
                        vacancy_meta[v.get("id", "")] = v
                    except json.JSONDecodeError:
                        pass

    # Create extractor
    extractor = FeatureExtractor(
        requirement_matches=req_by_vacancy,
        existing_scores=existing_scores,
        vacancy_meta=vacancy_meta,
        cross_encoder_scores=ce_scores,
    )

    # Extract features
    features_list = []
    for row in resume_rows:
        vid = row["vacancy_id"]
        feat = extractor.extract(resume_id, vid)
        feat.ce_ettin_norm = normalizer.normalize("ettin-reranker-68m-v1", feat.ce_ettin_raw)
        feat.ce_mmbert_norm = normalizer.normalize("mmBERT-small", feat.ce_mmbert_raw)
        feat.ce_modernbert_norm = normalizer.normalize("multilingual-modernbert-small", feat.ce_modernbert_raw)
        features_list.append(feat)

    return features_list


def _load_vacancy_meta(session: Session) -> dict[str, dict]:
    """Load vacancy metadata from pre-computed file."""
    import json
    from pathlib import Path

    vacancies_path = Path("data/matching/vacancies.json")
    if not vacancies_path.exists():
        return {}

    meta: dict[str, dict] = {}
    with open(vacancies_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    v = json.loads(line)
                    meta[v.get("id", "")] = v
                except json.JSONDecodeError:
                    pass
    return meta

def _load_features_from_db(
    session: Session, user_id: str, resume_id: str
) -> list[MatchFeatures]:
    """Load features for a resume by building from database.

    Fallback when pre-computed corpus files are not available.
    """
    from collections import defaultdict
    from sqlalchemy import select
    from app.storage.tables import (
        ApplicationMatchResultRow,
        ApplicationRow,
        VacancyRow,
        RequirementMatchRow,
    )

    # Get match results for this resume
    stmt = (
        select(ApplicationMatchResultRow, ApplicationRow, VacancyRow)
        .join(ApplicationRow, ApplicationMatchResultRow.application_id == ApplicationRow.id)
        .join(VacancyRow, ApplicationRow.vacancy_id == VacancyRow.id)
        .where(ApplicationRow.selected_cv_file_id == resume_id)
    )
    results = session.execute(stmt).all()
    if not results:
        return []

    # Build existing scores from DB
    existing_scores: dict[str, dict] = {}
    vacancy_meta: dict[str, dict] = {}
    vacancy_ids = []
    for match_result, application, vacancy in results:
        key = f"{resume_id}|{vacancy.id}"
        existing_scores[key] = {
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
        vacancy_ids.append(vacancy.id)


    # No requirement matches available from DB without complex joins
    req_by_vacancy: dict[str, list[dict]] = defaultdict(list)

    # No CE scores available from DB
    ce_scores: dict[str, dict[str, float]] = {}

    # Create extractor
    extractor = FeatureExtractor(
        requirement_matches=req_by_vacancy,
        existing_scores=existing_scores,
        vacancy_meta=vacancy_meta,
        cross_encoder_scores=ce_scores,
    )

    # Extract features for each vacancy
    features_list = []
    for match_result, application, vacancy in results:
        feat = extractor.extract(resume_id, vacancy.id)
        features_list.append(feat)

    return features_list
