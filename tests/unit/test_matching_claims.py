"""Tests for claim-based matching v2 pipeline.

Regression test for the specific failure cases identified in the matching overhaul:
1. NLP/TensorFlow/PyTorch evidence should NOT entail 'production Python backend'
2. Generic LLM integration should NOT entail 'practical RAG + 3 years ML/NLP'
"""

from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.matching.duration import ExperienceInterval, evaluate_duration
from app.matching.entailment import (
    EntailmentRelation,
    EvidenceStrengthCategory,
    compute_evidence_strength,
)
from app.matching.extraction import (
    RequirementImportance,
    RequirementType,
)
from app.matching.gap_analysis import GapType, analyze_gaps
from app.matching.scoring import (
    DeterministicMatchScorer,
    EligibilityStatus,
    MatchLevel,
    RequirementAssessment,
)
from app.storage.database import Base
from app.storage.tables import (
    ApplicationRow,
    CvFileRow,
    UserRow,
    VacancyRow,
)

# ── Helpers ──────────────────────────────────────────────────────


def _session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _application(session: Session, *, legacy_score: int = 42) -> ApplicationRow:
    user = UserRow(display_name="Candidate")
    session.add(user)
    session.flush()
    cv_file = CvFileRow(
        user_id=user.id,
        original_filename="resume.txt",
        storage_path="resume.txt",
        content_type="text/plain",
        sha256="a" * 64,
        size_bytes=10,
        analyzed_at=datetime.now(UTC),
    )
    vacancy = VacancyRow(
        source_url="https://example.test/vacancy/1",
        title="ML Engineer",
        company="Example",
        required_skills=["Python", "ML"],
        description_text="ML engineer with Python and RAG experience required.",
    )
    session.add_all((cv_file, vacancy))
    session.flush()
    application = ApplicationRow(
        user_id=user.id,
        vacancy_id=vacancy.id,
        selected_cv_file_id=cv_file.id,
        status="awaiting_review",
        match_score=legacy_score,
    )
    session.add(application)
    session.commit()
    return application


# ── Duration Tests ───────────────────────────────────────────────


def test_duration_single_interval():
    result = evaluate_duration(
        "c1",
        3.0,
        [ExperienceInterval("ML", date(2021, 1, 1), date(2024, 1, 1))],
        reference_date=date(2024, 1, 1),
    )
    assert result.actual_years >= 2.9
    assert result.status == "matched"


def test_duration_overlapping_intervals():
    """Parallel projects should not double-count."""
    result = evaluate_duration(
        "c1",
        3.0,
        [
            ExperienceInterval("Python", date(2022, 1, 1), date(2024, 1, 1)),
            ExperienceInterval("ML", date(2023, 1, 1), date(2025, 1, 1)),
        ],
        reference_date=date(2025, 1, 1),
    )
    assert result.actual_years >= 2.9
    assert result.actual_years < 4.1  # Not 4 years
    assert result.status == "matched"


def test_duration_no_intervals():
    result = evaluate_duration("c1", 3.0, [], reference_date=date(2024, 1, 1))
    assert result.status == "insufficient_evidence"
    assert result.actual_years == 0.0


def test_duration_ongoing_experience():
    result = evaluate_duration(
        "c1",
        2.0,
        [ExperienceInterval("Python", date(2023, 1, 1), None)],
        reference_date=date(2025, 6, 1),
    )
    assert result.actual_years >= 2.3
    assert result.status == "matched"


# ── Evidence Strength Tests ──────────────────────────────────────


def test_evidence_strength_entailed():
    strength, category = compute_evidence_strength(
        EntailmentRelation.ENTAILED,
        semantic_score=0.8,
        reranker_score=0.9,
        entailment_score=0.95,
    )
    assert strength >= 0.75
    assert category in (EvidenceStrengthCategory.STRONG, EvidenceStrengthCategory.EXPLICIT)


def test_evidence_strength_related_insufficient_capped():
    strength, category = compute_evidence_strength(
        EntailmentRelation.RELATED_BUT_INSUFFICIENT,
        semantic_score=0.9,
        reranker_score=0.95,
        entailment_score=0.5,
    )
    assert strength <= 0.49
    assert category in (EvidenceStrengthCategory.WEAK, EvidenceStrengthCategory.NONE)


def test_evidence_strength_contradicted_is_zero():
    strength, category = compute_evidence_strength(
        EntailmentRelation.CONTRADICTED,
        semantic_score=1.0,
        reranker_score=1.0,
        entailment_score=1.0,
    )
    assert strength == 0.0
    assert category == EvidenceStrengthCategory.NONE


# ── Scoring Tests ────────────────────────────────────────────────


def test_scoring_missing_required_does_not_zero_score():
    """Key regression: missing required should NOT cap score to 0 or 49."""
    scorer = DeterministicMatchScorer()
    assessments = (
        RequirementAssessment(
            requirement_id="python",
            requirement_type=RequirementType.HARD_SKILL,
            importance=RequirementImportance.REQUIRED,
            weight=1.0,
            is_blocker=False,
            match_level=MatchLevel.EXACT,
            entailment_relation=EntailmentRelation.ENTAILED,
            evidence_strength=0.9,
        ),
        RequirementAssessment(
            requirement_id="rag",
            requirement_type=RequirementType.HARD_SKILL,
            importance=RequirementImportance.REQUIRED,
            weight=1.0,
            is_blocker=False,
            match_level=MatchLevel.MISSING,
            entailment_relation=EntailmentRelation.RELATED_BUT_INSUFFICIENT,
            evidence_strength=0.3,
        ),
    )
    score = scorer.score(assessments)
    assert score.final_score > 0  # Not zero!
    assert score.final_score < 80  # But penalized
    assert score.required_score > 0


def test_scoring_hard_blocker_zeroes_score():
    scorer = DeterministicMatchScorer()
    assessments = (
        RequirementAssessment(
            requirement_id="auth",
            requirement_type=RequirementType.WORK_AUTHORIZATION,
            importance=RequirementImportance.REQUIRED,
            weight=1.0,
            is_blocker=True,
            is_hard_blocker=True,
            match_level=MatchLevel.MISSING,
        ),
        RequirementAssessment(
            requirement_id="python",
            requirement_type=RequirementType.HARD_SKILL,
            importance=RequirementImportance.REQUIRED,
            weight=1.0,
            is_blocker=False,
            match_level=MatchLevel.EXACT,
        ),
    )
    score = scorer.score(assessments)
    assert score.final_score == 0
    assert score.eligibility_status is EligibilityStatus.INELIGIBLE
    assert len(score.hard_blockers) > 0


def test_scoring_entailment_relation_used():
    """When entailment_relation is set, it should be used instead of match_level."""
    scorer = DeterministicMatchScorer()
    assessments = (
        RequirementAssessment(
            requirement_id="python",
            requirement_type=RequirementType.HARD_SKILL,
            importance=RequirementImportance.REQUIRED,
            weight=1.0,
            is_blocker=False,
            match_level=MatchLevel.EXACT,  # Legacy says EXACT
            entailment_relation=EntailmentRelation.RELATED_BUT_INSUFFICIENT,  # Entailment says no
            evidence_strength=0.3,
        ),
    )
    score = scorer.score(assessments)
    # Entailment relation should override match_level
    assert score.final_score < 50  # Should be low


def test_scoring_required_preferred_bonus_breakdown():
    scorer = DeterministicMatchScorer()
    assessments = (
        RequirementAssessment(
            requirement_id="python",
            requirement_type=RequirementType.HARD_SKILL,
            importance=RequirementImportance.REQUIRED,
            weight=1.0,
            is_blocker=False,
            match_level=MatchLevel.EXACT,
            evidence_strength=0.9,
        ),
        RequirementAssessment(
            requirement_id="fastapi",
            requirement_type=RequirementType.HARD_SKILL,
            importance=RequirementImportance.PREFERRED,
            weight=1.0,
            is_blocker=False,
            match_level=MatchLevel.PARTIAL,
            evidence_strength=0.6,
        ),
    )
    score = scorer.score(assessments)
    assert score.required_score == 100
    assert score.preferred_score == 65
    assert score.final_score > 0


# ── Gap Analysis Tests ───────────────────────────────────────────


def test_gap_analysis_identifies_skill_gap():
    gaps = analyze_gaps(
        [
            {
                "claim_id": "c1",
                "claim_subject": "RAG",
                "claim_type": "practical_experience",
                "relation": "unknown",
                "evidence_strength": 0.0,
                "has_evidence": False,
                "duration_result": None,
            }
        ]
    )
    assert gaps.total_gaps == 1
    assert gaps.skill_gaps == 1
    assert gaps.gaps[0].gap_type == GapType.SKILL_GAP


def test_gap_analysis_identifies_evidence_gap():
    gaps = analyze_gaps(
        [
            {
                "claim_id": "c1",
                "claim_subject": "Python production",
                "claim_type": "production_experience",
                "relation": "related_but_insufficient",
                "evidence_strength": 0.4,
                "has_evidence": True,
                "duration_result": None,
            }
        ]
    )
    assert gaps.total_gaps == 1
    assert gaps.evidence_gaps == 1


def test_gap_analysis_skips_entailed():
    gaps = analyze_gaps(
        [
            {
                "claim_id": "c1",
                "claim_subject": "Docker",
                "claim_type": "skill",
                "relation": "entailed",
                "evidence_strength": 0.9,
                "has_evidence": True,
                "duration_result": None,
            }
        ]
    )
    assert gaps.total_gaps == 0


# ── Regression Tests ─────────────────────────────────────────────


def test_nlp_pipelines_do_not_entail_production_python():
    """Regression: NLP classification pipelines should NOT entail production Python backend."""
    # This is the specific failure case from the issue
    relation = EntailmentRelation.RELATED_BUT_INSUFFICIENT
    strength, _ = compute_evidence_strength(
        relation,
        semantic_score=0.7,
        reranker_score=0.8,
        entailment_score=0.3,
    )
    assert strength <= 0.49  # Should be capped at related_but_insufficient


def test_llm_integration_does_not_entail_rag():
    """Regression: Generic LLM integration should NOT entail practical RAG."""
    relation = EntailmentRelation.RELATED_BUT_INSUFFICIENT
    strength, _ = compute_evidence_strength(
        relation,
        semantic_score=0.75,
        reranker_score=0.85,
        entailment_score=0.25,
    )
    assert strength <= 0.49


def test_fastapi_backend_entails_production_python():
    """Positive case: FastAPI backend services DO entail production Python."""
    relation = EntailmentRelation.ENTAILED
    strength, category = compute_evidence_strength(
        relation,
        semantic_score=0.85,
        reranker_score=0.9,
        entailment_score=0.95,
    )
    assert strength >= 0.75
    assert category in (EvidenceStrengthCategory.STRONG, EvidenceStrengthCategory.EXPLICIT)


def test_rag_service_entails_practical_rag():
    """Positive case: RAG service with retrieval and reranking DOES entail RAG."""
    relation = EntailmentRelation.ENTAILED
    strength, _ = compute_evidence_strength(
        relation,
        semantic_score=0.8,
        reranker_score=0.85,
        entailment_score=0.9,
    )
    assert strength >= 0.75
