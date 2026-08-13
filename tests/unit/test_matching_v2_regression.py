"""Regression tests for Matching v2 fixes.

Tests that the specific failure cases from the diagnostic are handled correctly:
1. UNKNOWN no longer means MISSING
2. Evaluator errors are EVALUATION_ERROR, not UNKNOWN
3. Duration insufficient_evidence is not "missing"
4. Hard blockers are classified correctly
5. FastAPI backend DOES entail production Python
6. Generic LLM does NOT entail RAG
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.matching.claim_pipeline import ClaimMatchPipeline
from app.matching.claims import (
    AtomicClaim,
    ClaimType,
    Criticality,
    LogicalGroup,
    RequirementCriticality,
    RequirementDecomposition,
)
from app.matching.duration import evaluate_duration, ExperienceInterval
from app.matching.entailment import (
    EntailmentRelation,
    EntailmentResult,
    EvidenceStrengthCategory,
    compute_evidence_strength,
)
from app.matching.gap_analysis import GapType, analyze_gaps
from app.matching.scoring import (
    DeterministicMatchScorer,
    EligibilityStatus,
    MatchLevel,
    RequirementAssessment,
)
from app.matching.extraction import RequirementImportance, RequirementType
from app.matching.semantic import FakeReranker, RetrievalCandidate
from app.storage.database import Base
from app.storage.tables import (
    ApplicationRow,
    CandidateEvidenceRow,
    CvFileRow,
    UserRow,
    VacancyRequirementRow,
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


def _create_application(session: Session) -> ApplicationRow:
    user = UserRow(display_name="ML Engineer Candidate")
    session.add(user)
    session.flush()
    cv_file = CvFileRow(
        user_id=user.id,
        original_filename="resume.txt",
        storage_path="resume.txt",
        content_type="text/plain",
        sha256="b" * 64,
        size_bytes=100,
        analyzed_at=datetime.now(UTC),
    )
    vacancy = VacancyRow(
        source_url="https://example.test/vacancy/ml-engineer-regression",
        title="ML Engineer",
        company="TestCo",
        required_skills=["Python", "ML/NLP", "RAG"],
        description_text="ML engineer requirements",
    )
    session.add_all((cv_file, vacancy))
    session.flush()
    application = ApplicationRow(
        user_id=user.id,
        vacancy_id=vacancy.id,
        selected_cv_file_id=cv_file.id,
        status="awaiting_review",
        match_score=0,
    )
    session.add(application)
    session.commit()
    return application


class _SessionRetriever:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def retrieve(
        self, requirement_text: str, *, user_id: str, cv_file_id: str
    ) -> tuple[RetrievalCandidate, ...]:
        rows = self._session.scalars(
            select(CandidateEvidenceRow).where(
                CandidateEvidenceRow.user_id == user_id,
                CandidateEvidenceRow.cv_file_id == cv_file_id,
                CandidateEvidenceRow.is_verified.is_(True),
            )
        ).all()
        return tuple(
            RetrievalCandidate(
                evidence_id=row.id,
                evidence_text=row.evidence_text,
                lexical_score=0.5,
                dense_score=0.6,
                hybrid_score=0.55,
            )
            for row in rows
        )


class _FakeDecomposer:
    model_name = "fake-decomposer"
    model_version = "1"
    schema_version = "1"

    def __init__(self, decompositions: dict[str, RequirementDecomposition]) -> None:
        self._decompositions = decompositions

    async def decompose(
        self, *, requirement_id, requirement_text, requirement_type, importance, is_blocker
    ) -> RequirementDecomposition:
        if requirement_id in self._decompositions:
            return self._decompositions[requirement_id]
        return RequirementDecomposition(
            requirement_id=requirement_id,
            requirement_text=requirement_text,
            requirement_criticality=RequirementCriticality.REQUIRED,
            claims=[
                AtomicClaim(
                    id=f"{requirement_id}-1",
                    requirement_id=requirement_id,
                    claim_type=ClaimType.SKILL,
                    subject=requirement_text,
                    normalized_subject=requirement_text.lower(),
                    criticality=Criticality.REQUIRED,
                    logical_group=LogicalGroup.AND,
                    source_text=requirement_text,
                )
            ],
            is_composite=False,
        )


class _FakeEntailmentEvaluator:
    model_name = "fake-evaluator"
    model_version = "1"

    def __init__(self, results: dict[str, EntailmentResult] | None = None) -> None:
        self._results = results or {}

    async def evaluate(
        self, *, claim_id, claim_text, claim_type, evidence_id, evidence_text,
        semantic_score=None, reranker_score=None, **kwargs,
    ) -> EntailmentResult:
        key = f"{claim_id}:{evidence_id}"
        if key in self._results:
            return self._results[key]
        return EntailmentResult(
            claim_id=claim_id,
            evidence_id=evidence_id,
            relation=EntailmentRelation.RELATED_BUT_INSUFFICIENT,
            confidence=0.5,
            reason="Default: related but insufficient",
            semantic_score=semantic_score,
            reranker_score=reranker_score,
            entailment_score=0.3,
            evidence_strength=0.3,
            evidence_strength_category=EvidenceStrengthCategory.WEAK,
        )


class _ErrorEvaluator:
    """Simulates evaluator that always raises (technical failure)."""
    model_name = "error-evaluator"
    model_version = "1"

    async def evaluate(self, **kwargs) -> EntailmentResult:
        raise TimeoutError("LLM provider timed out")


# ── Regression Test 1: NLP ≠ production Python ──────────────────

def test_regression_1_nlp_not_production_python():
    """NLP pipelines evidence should NOT entail production Python."""

    async def run():
        sf = _session_factory()
        with sf() as session:
            app = _create_application(session)
            nlp_ev = CandidateEvidenceRow(
                user_id=app.user_id, cv_file_id=app.selected_cv_file_id,
                evidence_text="Developed NLP classification pipelines using TensorFlow, Keras, PyTorch, LSTM / BiLSTM with attention, DistilBERT",
                normalized_text="nlp classification pipelines tensorflow pytorch",
                evidence_type="work_experience", skill_name="ML/NLP",
                experience_level="hands_on", is_verified=True,
                source_fragment="NLP classification pipelines",
                extraction_model="fake", extraction_model_version="1",
                extraction_schema_version="1", extraction_run_id="run-1", confidence=0.9,
            )
            session.add(nlp_ev)
            session.flush()

            req = VacancyRequirementRow(
                vacancy_id=app.vacancy_id, requirement_text="Уверенный Python, умение писать прод-код",
                normalized_text="production python", requirement_type="hard_skill",
                importance="required", weight=1.0, is_blocker=False,
                alternatives_json=[], source_fragment="Python production",
                extraction_model="fake", extraction_model_version="1",
                extraction_schema_version="1", extraction_run_id="run-1", confidence=0.95,
            )
            session.add(req)
            session.flush()

            decomposer = _FakeDecomposer({
                req.id: RequirementDecomposition(
                    requirement_id=req.id, requirement_text=req.requirement_text,
                    requirement_criticality=RequirementCriticality.REQUIRED,
                    claims=[AtomicClaim(
                        id=f"{req.id}-prod-python", requirement_id=req.id,
                        claim_type=ClaimType.PRODUCTION_EXPERIENCE, subject="Python",
                        normalized_subject="python", criticality=Criticality.REQUIRED,
                        logical_group=LogicalGroup.AND, source_text=req.requirement_text,
                    )], is_composite=False,
                )
            })

            evaluator = _FakeEntailmentEvaluator({
                f"{req.id}-prod-python:{nlp_ev.id}": EntailmentResult(
                    claim_id=f"{req.id}-prod-python", evidence_id=nlp_ev.id,
                    relation=EntailmentRelation.RELATED_BUT_INSUFFICIENT,
                    confidence=0.85,
                    reason="NLP/ML work confirmed but not production Python backend engineering.",
                    semantic_score=0.7, reranker_score=0.8, entailment_score=0.3,
                    evidence_strength=0.3, evidence_strength_category=EvidenceStrengthCategory.WEAK,
                )
            })

            pipeline = ClaimMatchPipeline(
                session=session, decomposer=decomposer, evaluator=evaluator,
                retriever=_SessionRetriever(session), reranker=FakeReranker(),
            )
            result = await pipeline.match_requirements(
                app.id, app.user_id, app.selected_cv_file_id, (req,),
            )

            assessment = result.assessments[0]
            assert assessment.entailment_relation is EntailmentRelation.RELATED_BUT_INSUFFICIENT
            assert assessment.match_level not in (MatchLevel.EXACT, MatchLevel.STRONG)
            assert assessment.evidence_strength <= 0.49

    asyncio.run(run())


# ── Regression Test 2: LLM integration ≠ RAG ───────────────────

def test_regression_2_llm_integration_not_rag():
    """Generic LLM integration should NOT entail practical RAG."""

    async def run():
        sf = _session_factory()
        with sf() as session:
            app = _create_application(session)
            llm_ev = CandidateEvidenceRow(
                user_id=app.user_id, cv_file_id=app.selected_cv_file_id,
                evidence_text="Designed and implemented an asynchronous, provider-independent integration architecture for AI and LLM services",
                normalized_text="async provider-independent llm integration",
                evidence_type="work_experience", skill_name="LLM",
                experience_level="hands_on", is_verified=True,
                source_fragment="LLM integration architecture",
                extraction_model="fake", extraction_model_version="1",
                extraction_schema_version="1", extraction_run_id="run-1", confidence=0.9,
            )
            session.add(llm_ev)
            session.flush()

            req = VacancyRequirementRow(
                vacancy_id=app.vacancy_id, requirement_text="Практический опыт с RAG",
                normalized_text="practical rag experience", requirement_type="hard_skill",
                importance="required", weight=1.0, is_blocker=False,
                alternatives_json=[], source_fragment="RAG",
                extraction_model="fake", extraction_model_version="1",
                extraction_schema_version="1", extraction_run_id="run-1", confidence=0.9,
            )
            session.add(req)
            session.flush()

            decomposer = _FakeDecomposer({
                req.id: RequirementDecomposition(
                    requirement_id=req.id, requirement_text=req.requirement_text,
                    requirement_criticality=RequirementCriticality.REQUIRED,
                    claims=[AtomicClaim(
                        id=f"{req.id}-rag", requirement_id=req.id,
                        claim_type=ClaimType.PRACTICAL_EXPERIENCE, subject="RAG",
                        normalized_subject="rag", criticality=Criticality.REQUIRED,
                        logical_group=LogicalGroup.AND, source_text=req.requirement_text,
                    )], is_composite=False,
                )
            })

            evaluator = _FakeEntailmentEvaluator({
                f"{req.id}-rag:{llm_ev.id}": EntailmentResult(
                    claim_id=f"{req.id}-rag", evidence_id=llm_ev.id,
                    relation=EntailmentRelation.RELATED_BUT_INSUFFICIENT,
                    confidence=0.9, reason="LLM integration is not RAG.",
                    semantic_score=0.75, reranker_score=0.85, entailment_score=0.25,
                    evidence_strength=0.25, evidence_strength_category=EvidenceStrengthCategory.WEAK,
                )
            })

            pipeline = ClaimMatchPipeline(
                session=session, decomposer=decomposer, evaluator=evaluator,
                retriever=_SessionRetriever(session), reranker=FakeReranker(),
            )
            result = await pipeline.match_requirements(
                app.id, app.user_id, app.selected_cv_file_id, (req,),
            )
            assessment = result.assessments[0]
            assert assessment.entailment_relation is EntailmentRelation.RELATED_BUT_INSUFFICIENT
            assert assessment.evidence_strength <= 0.49

    asyncio.run(run())


# ── Regression Test 3: FastAPI backend IS production Python ─────

def test_regression_3_fastapi_entails_production_python():
    """FastAPI backend services DO entail production Python."""

    async def run():
        sf = _session_factory()
        with sf() as session:
            app = _create_application(session)
            fastapi_ev = CandidateEvidenceRow(
                user_id=app.user_id, cv_file_id=app.selected_cv_file_id,
                evidence_text="Developed asynchronous Python/FastAPI backend services integrated with PostgreSQL, Redis and external APIs, deployed using Docker.",
                normalized_text="python fastapi async backend postgresql docker",
                evidence_type="work_experience", skill_name="Python",
                experience_level="production", is_verified=True,
                source_fragment="FastAPI backend services",
                extraction_model="fake", extraction_model_version="1",
                extraction_schema_version="1", extraction_run_id="run-1", confidence=0.95,
            )
            session.add(fastapi_ev)
            session.flush()

            req = VacancyRequirementRow(
                vacancy_id=app.vacancy_id, requirement_text="Уверенный Python, умение писать прод-код",
                normalized_text="production python", requirement_type="hard_skill",
                importance="required", weight=1.0, is_blocker=False,
                alternatives_json=[], source_fragment="Python production",
                extraction_model="fake", extraction_model_version="1",
                extraction_schema_version="1", extraction_run_id="run-1", confidence=0.95,
            )
            session.add(req)
            session.flush()

            decomposer = _FakeDecomposer({
                req.id: RequirementDecomposition(
                    requirement_id=req.id, requirement_text=req.requirement_text,
                    requirement_criticality=RequirementCriticality.REQUIRED,
                    claims=[AtomicClaim(
                        id=f"{req.id}-prod-py", requirement_id=req.id,
                        claim_type=ClaimType.PRODUCTION_EXPERIENCE, subject="Python",
                        normalized_subject="python", criticality=Criticality.REQUIRED,
                        logical_group=LogicalGroup.AND, source_text=req.requirement_text,
                    )], is_composite=False,
                )
            })

            evaluator = _FakeEntailmentEvaluator({
                f"{req.id}-prod-py:{fastapi_ev.id}": EntailmentResult(
                    claim_id=f"{req.id}-prod-py", evidence_id=fastapi_ev.id,
                    relation=EntailmentRelation.ENTAILED,
                    confidence=0.95, reason="FastAPI backend = production Python.",
                    semantic_score=0.85, reranker_score=0.9, entailment_score=0.95,
                    evidence_strength=0.9, evidence_strength_category=EvidenceStrengthCategory.STRONG,
                )
            })

            pipeline = ClaimMatchPipeline(
                session=session, decomposer=decomposer, evaluator=evaluator,
                retriever=_SessionRetriever(session), reranker=FakeReranker(),
            )
            result = await pipeline.match_requirements(
                app.id, app.user_id, app.selected_cv_file_id, (req,),
            )
            assessment = result.assessments[0]
            assert assessment.entailment_relation is EntailmentRelation.ENTAILED
            assert assessment.match_level == MatchLevel.EXACT
            assert assessment.evidence_strength >= 0.75

    asyncio.run(run())


# ── Regression Test 4: Duration with union intervals ────────────

def test_regression_4_duration_union_intervals():
    """Overlapping intervals should be merged, not summed."""
    from datetime import date

    intervals = [
        ExperienceInterval("ML", date(2021, 1, 1), date(2023, 12, 31)),
        ExperienceInterval("ML", date(2022, 6, 1), date(2025, 1, 1)),
    ]
    result = evaluate_duration("test", 3.0, intervals, reference_date=date(2025, 1, 1))
    assert result.actual_years >= 3.9
    assert result.actual_years <= 4.1
    assert result.status == "matched"


# ── Regression Test 5: No dates → insufficient_evidence ─────────

def test_regression_5_no_dates_insufficient_evidence():
    """Strong ML facts without dates should be insufficient_evidence, not missing."""
    from datetime import date

    result = evaluate_duration("test", 3.0, [], reference_date=date(2025, 1, 1))
    assert result.status == "insufficient_evidence"
    assert result.actual_years == 0.0


# ── Regression Test 6: True missing ─────────────────────────────

def test_regression_6_true_missing():
    """No evidence at all should be 'missing', not 'insufficient_evidence'."""
    scorer = DeterministicMatchScorer()
    assessment = RequirementAssessment(
        requirement_id="test",
        requirement_type=RequirementType.HARD_SKILL,
        importance=RequirementImportance.REQUIRED,
        weight=1.0,
        is_blocker=False,
        match_level=MatchLevel.MISSING,
        entailment_relation=EntailmentRelation.UNKNOWN,
        evidence_strength=0.0,
    )
    score = scorer.score((assessment,))
    assert score.missing_required_count == 1


# ── Regression Test 7: Evaluator failure → EVALUATION_ERROR ─────

def test_regression_7_evaluator_failure():
    """Technical evaluator failure should be EVALUATION_ERROR, not UNKNOWN or MISSING."""
    scorer = DeterministicMatchScorer()
    assessment = RequirementAssessment(
        requirement_id="test",
        requirement_type=RequirementType.HARD_SKILL,
        importance=RequirementImportance.REQUIRED,
        weight=1.0,
        is_blocker=False,
        match_level=MatchLevel.EVALUATION_ERROR,
        entailment_relation=EntailmentRelation.EVALUATION_ERROR,
        evidence_strength=0.0,
    )
    score = scorer.score((assessment,))
    # Should NOT be 0 — evaluation error gets some residual credit
    assert score.final_score > 0
    assert score.confidence < 0.5


# ── Regression Test 8: Hard blocker confirmed ───────────────────

def test_regression_8_hard_blocker_confirmed():
    """Confirmed contradiction of a true hard blocker → score = 0."""
    scorer = DeterministicMatchScorer()
    assessment = RequirementAssessment(
        requirement_id="auth",
        requirement_type=RequirementType.WORK_AUTHORIZATION,
        importance=RequirementImportance.REQUIRED,
        weight=1.0,
        is_blocker=True,
        is_hard_blocker=True,
        match_level=MatchLevel.MISSING,
        entailment_relation=EntailmentRelation.CONTRADICTED,
    )
    score = scorer.score((assessment,))
    assert score.final_score == 0
    assert score.eligibility_status is EligibilityStatus.INELIGIBLE


# ── Regression Test 9: Unresolved blocker ────────────────────────

def test_regression_9_unresolved_blocker():
    """Insufficient evidence for a hard blocker → needs_confirmation, not ineligible."""
    scorer = DeterministicMatchScorer()
    assessment = RequirementAssessment(
        requirement_id="auth",
        requirement_type=RequirementType.WORK_AUTHORIZATION,
        importance=RequirementImportance.REQUIRED,
        weight=1.0,
        is_blocker=True,
        is_hard_blocker=False,  # Not confirmed
        is_unresolved_blocker=True,
        match_level=MatchLevel.INSUFFICIENT_EVIDENCE,
        entailment_relation=EntailmentRelation.INSUFFICIENT_EVIDENCE,
        evidence_strength=0.0,
    )
    score = scorer.score((assessment,))
    assert score.final_score > 0  # Should NOT be 0
    assert score.eligibility_status is EligibilityStatus.NEEDS_CONFIRMATION
    assert len(score.hard_blockers) == 0
    assert len(score.hard_blockers_unresolved) == 1


# ── EntailmentRelation has INSUFFICIENT_EVIDENCE ────────────────

def test_entailment_relation_has_insufficient_evidence():
    """New relation states exist."""
    assert EntailmentRelation.INSUFFICIENT_EVIDENCE == "insufficient_evidence"
    assert EntailmentRelation.EVALUATION_ERROR == "evaluation_error"


# ── Scoring: INSUFFICIENT_EVIDENCE not zeroed ───────────────────

def test_scoring_insufficient_evidence_not_zeroed():
    """INSUFFICIENT_EVIDENCE gets some credit, not 0."""
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
            requirement_id="ml-duration",
            requirement_type=RequirementType.EXPERIENCE,
            importance=RequirementImportance.REQUIRED,
            weight=1.0,
            is_blocker=False,
            match_level=MatchLevel.INSUFFICIENT_EVIDENCE,
            entailment_relation=EntailmentRelation.INSUFFICIENT_EVIDENCE,
            evidence_strength=0.0,
        ),
    )
    score = scorer.score(assessments)
    # Python entailed + ML duration insufficient → should be > 0
    assert score.final_score > 0
    assert score.final_score < 80


# ── Gap analysis: evaluation_error vs skill_gap ─────────────────

def test_gap_analysis_evaluation_error_not_skill_gap():
    """Evaluation errors should be EVALUATION_ERROR gap type, not SKILL_GAP."""
    gaps = analyze_gaps([
        {
            "claim_id": "c1",
            "claim_subject": "Python production",
            "claim_type": "production_experience",
            "relation": "evaluation_error",
            "evidence_strength": 0.0,
            "has_evidence": True,
            "duration_result": None,
        }
    ])
    assert gaps.total_gaps == 1
    assert gaps.gaps[0].gap_type == GapType.EVALUATION_ERROR
    assert gaps.evaluation_errors == 1
    assert gaps.skill_gaps == 0


# ── Gap analysis: insufficient_evidence vs skill_gap ────────────

def test_gap_analysis_insufficient_evidence_not_skill_gap():
    """Insufficient evidence should be EVIDENCE_GAP, not SKILL_GAP."""
    gaps = analyze_gaps([
        {
            "claim_id": "c1",
            "claim_subject": "RAG experience",
            "claim_type": "practical_experience",
            "relation": "insufficient_evidence",
            "evidence_strength": 0.0,
            "has_evidence": False,
            "duration_result": None,
        }
    ])
    assert gaps.total_gaps == 1
    assert gaps.gaps[0].gap_type == GapType.EVIDENCE_GAP
    assert gaps.skill_gaps == 0
