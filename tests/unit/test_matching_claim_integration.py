"""Integration-level regression tests for claim-based matching.

Tests the full pipeline with mocked LLM (decomposer + evaluator) to verify
that the specific failure cases from the issue are handled correctly.
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
from app.matching.entailment import (
    EntailmentRelation,
    EntailmentResult,
    EvidenceStrengthCategory,
)
from app.matching.scoring import MatchLevel
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
        source_url="https://example.test/vacancy/ml-engineer",
        title="ML Engineer — RAG, VLM and on-prem inference",
        company="Neuroengineering",
        required_skills=["Python", "ML/NLP", "RAG"],
        description_text=(
            "ML-инженер — RAG, VLM и on-prem инференс для «Нейроинженер»\n"
            "Требования: ML/NLP от 3 лет, практический RAG, Python production code, "
            "async, Docker, Ollama/vLLM/TGI, PyTorch/Hugging Face/CUDA."
        ),
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
    """Returns evidence from the DB matching a skill keyword."""

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
        # Return all verified evidence (simulates semantic retrieval in tests)
        candidates = []
        for row in rows:
            candidates.append(
                RetrievalCandidate(
                    evidence_id=row.id,
                    evidence_text=row.evidence_text,
                    lexical_score=0.5,
                    dense_score=0.6,
                    hybrid_score=0.55,
                )
            )
        return tuple(candidates)


class _FakeDecomposer:
    """Returns pre-configured decompositions for testing."""

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
        # Default: single claim matching the requirement text
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
    """Returns pre-configured entailment results for testing."""

    model_name = "fake-evaluator"
    model_version = "1"

    def __init__(self, results: dict[str, EntailmentResult]) -> None:
        self._results = results

    async def evaluate(
        self,
        *,
        claim_id,
        claim_text,
        claim_type,
        evidence_id,
        evidence_text,
        semantic_score=None,
        reranker_score=None,
        **kwargs,
    ) -> EntailmentResult:
        key = f"{claim_id}:{evidence_id}"
        if key in self._results:
            return self._results[key]
        # Default: related_but_insufficient
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


# ── Regression: NLP pipelines ≠ production Python ────────────────


def test_regression_nlp_pipelines_not_entailed_as_production_python():
    """
    The original bug: evidence 'Developed NLP classification pipelines using
    TensorFlow, Keras, PyTorch' was shown as confirming 'production Python'.
    With entailment evaluation, this should be related_but_insufficient.
    """

    async def run():
        session_factory = _session_factory()
        with session_factory() as session:
            application = _create_application(session)

            # Add evidence: NLP pipelines (not production Python)
            nlp_evidence = CandidateEvidenceRow(
                user_id=application.user_id,
                cv_file_id=application.selected_cv_file_id,
                evidence_text=(
                    "Developed and evaluated multi-class and multi-output NLP "
                    "classification pipelines using TensorFlow, Keras, PyTorch, "
                    "LSTM / BiLSTM with attention, DistilBERT, and ruBERT-tiny2"
                ),
                normalized_text="nlp classification pipelines tensorflow pytorch",
                evidence_type="work_experience",
                skill_name="ML/NLP",
                experience_level="hands_on",
                is_verified=True,
                source_fragment="NLP classification pipelines",
                extraction_model="fake",
                extraction_model_version="1",
                extraction_schema_version="1",
                extraction_run_id="run-1",
                confidence=0.9,
            )
            session.add(nlp_evidence)
            session.flush()

            # Create requirement: "production Python"
            req = VacancyRequirementRow(
                vacancy_id=application.vacancy_id,
                requirement_text="Production Python experience",
                normalized_text="production python experience",
                requirement_type="hard_skill",
                importance="required",
                weight=1.0,
                is_blocker=False,
                alternatives_json=[],
                source_fragment="Production Python experience",
                extraction_model="fake",
                extraction_model_version="1",
                extraction_schema_version="1",
                extraction_run_id="run-1",
                confidence=0.95,
            )
            session.add(req)
            session.flush()

            # Configure decomposer: single claim for production Python
            decomposer = _FakeDecomposer(
                {
                    req.id: RequirementDecomposition(
                        requirement_id=req.id,
                        requirement_text=req.requirement_text,
                        requirement_criticality=RequirementCriticality.REQUIRED,
                        claims=[
                            AtomicClaim(
                                id=f"{req.id}-prod-python",
                                requirement_id=req.id,
                                claim_type=ClaimType.PRODUCTION_EXPERIENCE,
                                subject="Python",
                                normalized_subject="python",
                                criticality=Criticality.REQUIRED,
                                logical_group=LogicalGroup.AND,
                                source_text=req.requirement_text,
                            )
                        ],
                        is_composite=False,
                    )
                }
            )

            # Configure evaluator: NLP evidence → related_but_insufficient
            evaluator = _FakeEntailmentEvaluator(
                {
                    f"{req.id}-prod-python:{nlp_evidence.id}": EntailmentResult(
                        claim_id=f"{req.id}-prod-python",
                        evidence_id=nlp_evidence.id,
                        relation=EntailmentRelation.RELATED_BUT_INSUFFICIENT,
                        confidence=0.85,
                        reason=(
                            "Evidence confirms NLP/ML work but does not establish "
                            "production Python backend engineering experience."
                        ),
                        semantic_score=0.7,
                        reranker_score=0.8,
                        entailment_score=0.3,
                        evidence_strength=0.3,
                        evidence_strength_category=EvidenceStrengthCategory.WEAK,
                    )
                }
            )

            pipeline = ClaimMatchPipeline(
                session=session,
                decomposer=decomposer,
                evaluator=evaluator,
                retriever=_SessionRetriever(session),
                reranker=FakeReranker(),
            )

            result = await pipeline.match_requirements(
                application_id=application.id,
                user_id=application.user_id,
                cv_file_id=application.selected_cv_file_id,
                requirements=(req,),
            )

            # Verify: should NOT be entailed
            assert len(result.assessments) == 1
            assessment = result.assessments[0]
            assert assessment.entailment_relation is EntailmentRelation.RELATED_BUT_INSUFFICIENT
            assert assessment.match_level == MatchLevel.RELATED
            assert assessment.evidence_strength <= 0.49
            # Should NOT be scored as EXACT or STRONG
            assert assessment.match_level not in (MatchLevel.EXACT, MatchLevel.STRONG)

    asyncio.run(run())


# ── Regression: LLM integration ≠ RAG ──────────────────────────


def test_regression_llm_integration_not_entailed_as_rag():
    """
    The original bug: evidence 'Designed provider-independent LLM integrations'
    was shown as confirming 'practical RAG'. Should be related_but_insufficient.
    """

    async def run():
        session_factory = _session_factory()
        with session_factory() as session:
            application = _create_application(session)

            llm_evidence = CandidateEvidenceRow(
                user_id=application.user_id,
                cv_file_id=application.selected_cv_file_id,
                evidence_text=(
                    "Designed and implemented an asynchronous, provider-independent "
                    "integration architecture for AI and LLM services"
                ),
                normalized_text="async provider-independent llm integration",
                evidence_type="work_experience",
                skill_name="LLM",
                experience_level="hands_on",
                is_verified=True,
                source_fragment="LLM integration architecture",
                extraction_model="fake",
                extraction_model_version="1",
                extraction_schema_version="1",
                extraction_run_id="run-1",
                confidence=0.9,
            )
            session.add(llm_evidence)
            session.flush()

            req = VacancyRequirementRow(
                vacancy_id=application.vacancy_id,
                requirement_text="Practical RAG experience",
                normalized_text="practical rag experience",
                requirement_type="hard_skill",
                importance="required",
                weight=1.0,
                is_blocker=False,
                alternatives_json=[],
                source_fragment="Practical RAG",
                extraction_model="fake",
                extraction_model_version="1",
                extraction_schema_version="1",
                extraction_run_id="run-1",
                confidence=0.9,
            )
            session.add(req)
            session.flush()

            decomposer = _FakeDecomposer(
                {
                    req.id: RequirementDecomposition(
                        requirement_id=req.id,
                        requirement_text=req.requirement_text,
                        requirement_criticality=RequirementCriticality.REQUIRED,
                        claims=[
                            AtomicClaim(
                                id=f"{req.id}-rag",
                                requirement_id=req.id,
                                claim_type=ClaimType.PRACTICAL_EXPERIENCE,
                                subject="RAG",
                                normalized_subject="rag",
                                criticality=Criticality.REQUIRED,
                                logical_group=LogicalGroup.AND,
                                source_text=req.requirement_text,
                            )
                        ],
                        is_composite=False,
                    )
                }
            )

            evaluator = _FakeEntailmentEvaluator(
                {
                    f"{req.id}-rag:{llm_evidence.id}": EntailmentResult(
                        claim_id=f"{req.id}-rag",
                        evidence_id=llm_evidence.id,
                        relation=EntailmentRelation.RELATED_BUT_INSUFFICIENT,
                        confidence=0.9,
                        reason="LLM integration is not RAG. No retrieval or reranking.",
                        semantic_score=0.75,
                        reranker_score=0.85,
                        entailment_score=0.25,
                        evidence_strength=0.25,
                        evidence_strength_category=EvidenceStrengthCategory.WEAK,
                    )
                }
            )

            pipeline = ClaimMatchPipeline(
                session=session,
                decomposer=decomposer,
                evaluator=evaluator,
                retriever=_SessionRetriever(session),
                reranker=FakeReranker(),
            )

            result = await pipeline.match_requirements(
                application_id=application.id,
                user_id=application.user_id,
                cv_file_id=application.selected_cv_file_id,
                requirements=(req,),
            )

            assessment = result.assessments[0]
            assert assessment.entailment_relation is EntailmentRelation.RELATED_BUT_INSUFFICIENT
            assert assessment.evidence_strength <= 0.49

    asyncio.run(run())


# ── Positive: FastAPI backend IS production Python ──────────────


def test_positive_fastapi_entails_production_python():
    """
    Positive regression: 'Built Python/FastAPI backend services deployed with Docker'
    DOES entail 'production Python development'.
    """

    async def run():
        session_factory = _session_factory()
        with session_factory() as session:
            application = _create_application(session)

            fastapi_evidence = CandidateEvidenceRow(
                user_id=application.user_id,
                cv_file_id=application.selected_cv_file_id,
                evidence_text=(
                    "Developed asynchronous Python/FastAPI backend services "
                    "integrated with PostgreSQL, Redis and external APIs, "
                    "deployed using Docker."
                ),
                normalized_text="python fastapi async backend postgresql docker",
                evidence_type="work_experience",
                skill_name="Python",
                experience_level="production",
                is_verified=True,
                source_fragment="FastAPI backend services",
                extraction_model="fake",
                extraction_model_version="1",
                extraction_schema_version="1",
                extraction_run_id="run-1",
                confidence=0.95,
            )
            session.add(fastapi_evidence)
            session.flush()

            req = VacancyRequirementRow(
                vacancy_id=application.vacancy_id,
                requirement_text="Production Python experience",
                normalized_text="production python experience",
                requirement_type="hard_skill",
                importance="required",
                weight=1.0,
                is_blocker=False,
                alternatives_json=[],
                source_fragment="Production Python",
                extraction_model="fake",
                extraction_model_version="1",
                extraction_schema_version="1",
                extraction_run_id="run-1",
                confidence=0.95,
            )
            session.add(req)
            session.flush()

            decomposer = _FakeDecomposer(
                {
                    req.id: RequirementDecomposition(
                        requirement_id=req.id,
                        requirement_text=req.requirement_text,
                        requirement_criticality=RequirementCriticality.REQUIRED,
                        claims=[
                            AtomicClaim(
                                id=f"{req.id}-prod-py",
                                requirement_id=req.id,
                                claim_type=ClaimType.PRODUCTION_EXPERIENCE,
                                subject="Python",
                                normalized_subject="python",
                                criticality=Criticality.REQUIRED,
                                logical_group=LogicalGroup.AND,
                                source_text=req.requirement_text,
                            )
                        ],
                        is_composite=False,
                    )
                }
            )

            evaluator = _FakeEntailmentEvaluator(
                {
                    f"{req.id}-prod-py:{fastapi_evidence.id}": EntailmentResult(
                        claim_id=f"{req.id}-prod-py",
                        evidence_id=fastapi_evidence.id,
                        relation=EntailmentRelation.ENTAILED,
                        confidence=0.95,
                        reason="FastAPI backend services confirm production Python development.",
                        semantic_score=0.85,
                        reranker_score=0.9,
                        entailment_score=0.95,
                        evidence_strength=0.9,
                        evidence_strength_category=EvidenceStrengthCategory.STRONG,
                    )
                }
            )

            pipeline = ClaimMatchPipeline(
                session=session,
                decomposer=decomposer,
                evaluator=evaluator,
                retriever=_SessionRetriever(session),
                reranker=FakeReranker(),
            )

            result = await pipeline.match_requirements(
                application_id=application.id,
                user_id=application.user_id,
                cv_file_id=application.selected_cv_file_id,
                requirements=(req,),
            )

            assessment = result.assessments[0]
            assert assessment.entailment_relation is EntailmentRelation.ENTAILED
            assert assessment.match_level == MatchLevel.EXACT
            assert assessment.evidence_strength >= 0.75

    asyncio.run(run())


# ── Composite requirement decomposition ─────────────────────────


def test_composite_requirement_partial_match():
    """
    'ML/NLP от 3 лет, из них практический опыт с RAG' decomposed into
    duration + RAG. Duration matched, RAG unknown → partial.
    """

    async def run():
        session_factory = _session_factory()
        with session_factory() as session:
            application = _create_application(session)

            ml_evidence = CandidateEvidenceRow(
                user_id=application.user_id,
                cv_file_id=application.selected_cv_file_id,
                evidence_text="5 years of ML/NLP experience",
                normalized_text="ml nlp experience 5 years",
                evidence_type="work_experience",
                skill_name="ML/NLP",
                experience_level="production",
                years=5.0,
                is_verified=True,
                source_fragment="5 years ML/NLP",
                extraction_model="fake",
                extraction_model_version="1",
                extraction_schema_version="1",
                extraction_run_id="run-1",
                confidence=0.9,
            )
            session.add(ml_evidence)
            session.flush()

            req = VacancyRequirementRow(
                vacancy_id=application.vacancy_id,
                requirement_text="ML/NLP от 3 лет, из них практический опыт с RAG",
                normalized_text="ml/nlp 3 years rag",
                requirement_type="hard_skill",
                importance="required",
                weight=1.0,
                is_blocker=False,
                alternatives_json=[],
                source_fragment="ML/NLP от 3 лет, RAG",
                extraction_model="fake",
                extraction_model_version="1",
                extraction_schema_version="1",
                extraction_run_id="run-1",
                confidence=0.9,
            )
            session.add(req)
            session.flush()

            # Decompose into duration + RAG
            decomposer = _FakeDecomposer(
                {
                    req.id: RequirementDecomposition(
                        requirement_id=req.id,
                        requirement_text=req.requirement_text,
                        requirement_criticality=RequirementCriticality.REQUIRED,
                        claims=[
                            AtomicClaim(
                                id=f"{req.id}-duration",
                                requirement_id=req.id,
                                claim_type=ClaimType.EXPERIENCE_DURATION,
                                subject="ML/NLP",
                                normalized_subject="ml/nlp",
                                operator=">=",
                                required_value="3",
                                unit="years",
                                criticality=Criticality.REQUIRED,
                                logical_group=LogicalGroup.AND,
                                source_text=req.requirement_text,
                            ),
                            AtomicClaim(
                                id=f"{req.id}-rag",
                                requirement_id=req.id,
                                claim_type=ClaimType.PRACTICAL_EXPERIENCE,
                                subject="RAG",
                                normalized_subject="rag",
                                criticality=Criticality.REQUIRED,
                                logical_group=LogicalGroup.AND,
                                source_text=req.requirement_text,
                            ),
                        ],
                        is_composite=True,
                    )
                }
            )

            # Duration: entailed (5 years), RAG: unknown
            evaluator = _FakeEntailmentEvaluator({})

            pipeline = ClaimMatchPipeline(
                session=session,
                decomposer=decomposer,
                evaluator=evaluator,
                retriever=_SessionRetriever(session),
                reranker=FakeReranker(),
            )

            result = await pipeline.match_requirements(
                application_id=application.id,
                user_id=application.user_id,
                cv_file_id=application.selected_cv_file_id,
                requirements=(req,),
            )

            assessment = result.assessments[0]
            # Duration matched but RAG unknown → partial at best
            assert assessment.match_level in (MatchLevel.PARTIAL, MatchLevel.MISSING)
            # Not EXACT since RAG is not entailed
            assert assessment.match_level != MatchLevel.EXACT

    asyncio.run(run())


# ── OR requirement semantics ────────────────────────────────────


def test_or_requirement_one_sufficient():
    """
    'PostgreSQL или MySQL' — one being entailed is sufficient.
    """

    async def run():
        session_factory = _session_factory()
        with session_factory() as session:
            application = _create_application(session)

            pg_evidence = CandidateEvidenceRow(
                user_id=application.user_id,
                cv_file_id=application.selected_cv_file_id,
                evidence_text="3 years PostgreSQL production",
                normalized_text="postgresql production 3 years",
                evidence_type="work_experience",
                skill_name="PostgreSQL",
                experience_level="production",
                is_verified=True,
                source_fragment="PostgreSQL production",
                extraction_model="fake",
                extraction_model_version="1",
                extraction_schema_version="1",
                extraction_run_id="run-1",
                confidence=0.9,
            )
            session.add(pg_evidence)
            session.flush()

            req = VacancyRequirementRow(
                vacancy_id=application.vacancy_id,
                requirement_text="PostgreSQL или MySQL",
                normalized_text="postgresql or mysql",
                requirement_type="hard_skill",
                importance="required",
                weight=1.0,
                is_blocker=False,
                alternatives_json=["MySQL"],
                source_fragment="PostgreSQL или MySQL",
                extraction_model="fake",
                extraction_model_version="1",
                extraction_schema_version="1",
                extraction_run_id="run-1",
                confidence=0.9,
            )
            session.add(req)
            session.flush()

            decomposer = _FakeDecomposer(
                {
                    req.id: RequirementDecomposition(
                        requirement_id=req.id,
                        requirement_text=req.requirement_text,
                        requirement_criticality=RequirementCriticality.REQUIRED,
                        claims=[
                            AtomicClaim(
                                id=f"{req.id}-pg",
                                requirement_id=req.id,
                                claim_type=ClaimType.TECHNOLOGY,
                                subject="PostgreSQL",
                                normalized_subject="postgresql",
                                criticality=Criticality.REQUIRED,
                                logical_group=LogicalGroup.OR,
                                source_text=req.requirement_text,
                            ),
                            AtomicClaim(
                                id=f"{req.id}-mysql",
                                requirement_id=req.id,
                                claim_type=ClaimType.TECHNOLOGY,
                                subject="MySQL",
                                normalized_subject="mysql",
                                criticality=Criticality.REQUIRED,
                                logical_group=LogicalGroup.OR,
                                source_text=req.requirement_text,
                            ),
                        ],
                        is_composite=True,
                    )
                }
            )

            evaluator = _FakeEntailmentEvaluator(
                {
                    f"{req.id}-pg:{pg_evidence.id}": EntailmentResult(
                        claim_id=f"{req.id}-pg",
                        evidence_id=pg_evidence.id,
                        relation=EntailmentRelation.ENTAILED,
                        confidence=0.9,
                        reason="PostgreSQL production confirmed.",
                        semantic_score=0.9,
                        reranker_score=0.9,
                        entailment_score=0.95,
                        evidence_strength=0.9,
                        evidence_strength_category=EvidenceStrengthCategory.STRONG,
                    )
                }
            )

            pipeline = ClaimMatchPipeline(
                session=session,
                decomposer=decomposer,
                evaluator=evaluator,
                retriever=_SessionRetriever(session),
                reranker=FakeReranker(),
            )

            result = await pipeline.match_requirements(
                application_id=application.id,
                user_id=application.user_id,
                cv_file_id=application.selected_cv_file_id,
                requirements=(req,),
            )

            assessment = result.assessments[0]
            # PostgreSQL entailed → OR group satisfied
            assert assessment.entailment_relation is EntailmentRelation.ENTAILED
            assert assessment.match_level == MatchLevel.EXACT

    asyncio.run(run())
