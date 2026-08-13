import asyncio
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.matching.extraction import (
    CandidateEvidence,
    CandidateEvidenceExtraction,
    EvidenceType,
    ExperienceLevel,
    FakeCandidateEvidenceExtractor,
    FakeVacancyRequirementExtractor,
    RequirementImportance,
    RequirementType,
    VacancyExtraction,
    VacancyRequirement,
)
from app.matching.pipeline import MatchingPipeline
from app.matching.scoring import DeterministicMatchScorer
from app.matching.semantic import FakeReranker, RetrievalCandidate
from app.storage.database import Base
from app.storage.tables import (
    ApplicationMatchResultRow,
    ApplicationRow,
    CandidateEvidenceRow,
    CvFileRow,
    RequirementMatchRow,
    UserRow,
    VacancyRequirementRow,
    VacancyRow,
)


class _SessionRetriever:
    def __init__(self, session: Session, *, return_evidence: bool = True) -> None:
        self._session = session
        self._return_evidence = return_evidence

    async def retrieve(
        self,
        requirement_text: str,
        *,
        user_id: str,
        cv_file_id: str,
    ) -> tuple[RetrievalCandidate, ...]:
        if not self._return_evidence:
            return ()
        evidence = self._session.scalar(
            select(CandidateEvidenceRow).where(
                CandidateEvidenceRow.user_id == user_id,
                CandidateEvidenceRow.cv_file_id == cv_file_id,
            )
        )
        assert evidence is not None
        return (
            RetrievalCandidate(
                evidence_id=evidence.id,
                evidence_text=evidence.evidence_text,
                lexical_score=1,
                dense_score=1,
                hybrid_score=1,
            ),
        )


class _UnavailableReranker:
    model_name = "external-reranker"
    model_revision = "unavailable"

    async def rerank(self, requirement_text, candidates):
        raise RuntimeError("service unavailable")


class _ForbiddenRetriever:
    async def retrieve(self, requirement_text, *, user_id, cv_file_id):
        raise AssertionError("hard-rejected vacancy must not reach retrieval")


class _ForbiddenReranker:
    model_name = "forbidden-reranker"
    model_revision = "forbidden"

    async def rerank(self, requirement_text, candidates):
        raise AssertionError("hard-rejected vacancy must not reach reranking")


class _ForbiddenIndexer:
    async def index_cv(self, *, user_id, cv_file_id):
        raise AssertionError("hard-rejected vacancy must not reach indexing")


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
        title="Python Engineer",
        company="Example",
        required_skills=["Python"],
        preferred_skills=[],
        description_text="Production Python experience is required.",
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


def _vacancy_extractor(
    vacancy_id: str,
    *,
    blocker: bool = False,
) -> FakeVacancyRequirementExtractor:
    requirement = VacancyRequirement(
        text="Production Python experience",
        normalized_text="production python experience",
        requirement_type=(
            RequirementType.WORK_AUTHORIZATION if blocker else RequirementType.HARD_SKILL
        ),
        importance=RequirementImportance.REQUIRED,
        is_blocker=blocker,
        source_fragment="Production Python experience is required.",
        confidence=0.95,
    )
    return FakeVacancyRequirementExtractor(
        {
            vacancy_id: VacancyExtraction(
                requirements=[requirement],
                confidence=0.95,
            )
        }
    )


def _candidate_extraction(cv_file_id: str) -> FakeCandidateEvidenceExtractor:
    return FakeCandidateEvidenceExtractor(
        {
            cv_file_id: CandidateEvidenceExtraction(
                evidence=[
                    CandidateEvidence(
                        text="Built production Python services",
                        normalized_text="production python services",
                        evidence_type=EvidenceType.WORK_EXPERIENCE,
                        skill_name="Python",
                        experience_level=ExperienceLevel.PRODUCTION,
                        source_fragment="Built production Python services",
                        confidence=0.95,
                    )
                ],
                confidence=0.95,
            )
        }
    )


def test_pipeline_persists_explainable_shadow_result_without_changing_legacy_score() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            application = _application(session)
            vacancy_extractor = _vacancy_extractor(application.vacancy_id)
            pipeline = MatchingPipeline(
                session,
                vacancy_extractor,
                _candidate_extraction(application.selected_cv_file_id or ""),
                _SessionRetriever(session),
                FakeReranker(),
                DeterministicMatchScorer(),
                shadow_mode=True,
            )

            aggregate = await pipeline.match(
                application.id,
                vacancy_source_text="Production Python experience is required.",
                cv_source_text="Built production Python services",
            )

            session.refresh(application)
            requirement_match = session.scalar(select(RequirementMatchRow))
            assert application.match_score == 42
            assert aggregate.application_id == application.id
            assert aggregate.final_score > 0
            assert aggregate.scoring_version == "matching-v2.3"
            assert aggregate.explanation_json["hard_gate"]["decision"] == "review"
            assert requirement_match is not None
            assert requirement_match.reranker_score is not None
            assert requirement_match.explanation

    asyncio.run(run())


def test_pipeline_can_synchronize_legacy_score_after_rollout() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            application = _application(session, legacy_score=1)
            extractor = _vacancy_extractor(application.vacancy_id)
            pipeline = MatchingPipeline(
                session,
                extractor,
                _candidate_extraction(application.selected_cv_file_id or ""),
                _SessionRetriever(session),
                FakeReranker(),
                DeterministicMatchScorer(),
                shadow_mode=False,
            )

            aggregate = await pipeline.match(
                application.id,
                vacancy_source_text="Production Python experience is required.",
                cv_source_text="Built production Python services",
            )

            session.refresh(application)
            assert application.match_score == aggregate.final_score

    asyncio.run(run())


def test_pipeline_reuses_unchanged_versioned_extractions() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            application = _application(session)
            pipeline = MatchingPipeline(
                session,
                _vacancy_extractor(application.vacancy_id),
                _candidate_extraction(application.selected_cv_file_id or ""),
                _SessionRetriever(session),
                FakeReranker(),
                DeterministicMatchScorer(),
            )

            for _ in range(2):
                await pipeline.match(
                    application.id,
                    vacancy_source_text="Production Python experience is required.",
                    cv_source_text="Built production Python services",
                )

            assert len(session.scalars(select(VacancyRequirementRow)).all()) == 1
            assert len(session.scalars(select(CandidateEvidenceRow)).all()) == 1
            assert len(session.scalars(select(RequirementMatchRow)).all()) == 1

    asyncio.run(run())


def test_pipeline_persists_blocker_when_no_evidence_is_retrieved() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            application = _application(session)
            extractor = _vacancy_extractor(application.vacancy_id, blocker=True)
            pipeline = MatchingPipeline(
                session,
                extractor,
                _candidate_extraction(application.selected_cv_file_id or ""),
                _SessionRetriever(session, return_evidence=False),
                FakeReranker(),
                DeterministicMatchScorer(),
            )

            aggregate = await pipeline.match(
                application.id,
                vacancy_source_text="Production Python experience is required.",
                cv_source_text="Built production Python services",
            )

            assert aggregate.final_score <= 20
            assert aggregate.eligibility_status == "ineligible"
            assert aggregate.blocker_count == 1
            assert session.scalar(select(ApplicationMatchResultRow)) is not None

    asyncio.run(run())


def test_pipeline_uses_hybrid_score_when_reranker_is_unavailable() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            application = _application(session)
            pipeline = MatchingPipeline(
                session,
                _vacancy_extractor(application.vacancy_id),
                _candidate_extraction(application.selected_cv_file_id or ""),
                _SessionRetriever(session),
                _UnavailableReranker(),
                DeterministicMatchScorer(),
            )

            aggregate = await pipeline.match(
                application.id,
                vacancy_source_text="Production Python experience is required.",
                cv_source_text="Built production Python services",
            )

            requirement_match = session.scalar(select(RequirementMatchRow))
            assert aggregate.status == "scored"
            assert requirement_match is not None
            assert requirement_match.hybrid_score == 1
            assert requirement_match.reranker_score is None
            assert (
                requirement_match.retrieval_model_versions_json["reranker"]["status"]
                == "reranker_unavailable"
            )

    asyncio.run(run())


def test_pipeline_rejects_unrelated_role_before_retrieval_and_reranking() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            application = _application(session)
            vacancy_extractor = FakeVacancyRequirementExtractor(
                {
                    application.vacancy_id: VacancyExtraction(
                        role="Sous Chef",
                        requirements=[
                            VacancyRequirement(
                                text="Sous Chef",
                                normalized_text="culinary_hospitality",
                                requirement_type=RequirementType.ROLE,
                                importance=RequirementImportance.REQUIRED,
                                is_blocker=True,
                                source_fragment="We are hiring a Sous Chef.",
                                confidence=0.99,
                            )
                        ],
                        confidence=0.99,
                    )
                }
            )
            candidate_extractor = FakeCandidateEvidenceExtractor(
                {
                    application.selected_cv_file_id or "": CandidateEvidenceExtraction(
                        evidence=[
                            CandidateEvidence(
                                text="Software Engineer",
                                normalized_text="software_engineering",
                                evidence_type=EvidenceType.ROLE,
                                skill_name="software_engineering",
                                source_fragment="Software Engineer",
                                confidence=0.99,
                            )
                        ],
                        confidence=0.99,
                    )
                }
            )
            pipeline = MatchingPipeline(
                session,
                vacancy_extractor,
                candidate_extractor,
                _ForbiddenRetriever(),
                _ForbiddenReranker(),
                DeterministicMatchScorer(),
                evidence_indexer=_ForbiddenIndexer(),
            )

            aggregate = await pipeline.match(
                application.id,
                vacancy_source_text="We are hiring a Sous Chef.",
                cv_source_text="Software Engineer",
            )

            assert aggregate.status == "scored"
            assert aggregate.eligibility_status == "ineligible"
            assert aggregate.final_score <= 20
            assert aggregate.explanation_json["hard_gate"]["decision"] == "reject"
            assert "NON_TECHNICAL_ROLE" in aggregate.explanation_json["hard_gate"]["reason_codes"]
            assert session.scalar(select(RequirementMatchRow)) is None

    asyncio.run(run())


def test_hard_rejection_removes_matches_from_a_previous_successful_run() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            application = _application(session)
            successful_pipeline = MatchingPipeline(
                session,
                _vacancy_extractor(application.vacancy_id),
                _candidate_extraction(application.selected_cv_file_id or ""),
                _SessionRetriever(session),
                FakeReranker(),
                DeterministicMatchScorer(),
            )
            await successful_pipeline.match(
                application.id,
                vacancy_source_text="Production Python experience is required.",
                cv_source_text="Built production Python services",
            )
            assert session.scalar(select(RequirementMatchRow)) is not None

            rejecting_pipeline = MatchingPipeline(
                session,
                FakeVacancyRequirementExtractor(
                    {
                        application.vacancy_id: VacancyExtraction(
                            role="Sous Chef",
                            requirements=[
                                VacancyRequirement(
                                    text="Sous Chef",
                                    normalized_text="culinary_hospitality",
                                    requirement_type=RequirementType.ROLE,
                                    importance=RequirementImportance.REQUIRED,
                                    is_blocker=True,
                                    source_fragment="We are hiring a Sous Chef.",
                                    confidence=0.99,
                                )
                            ],
                            confidence=0.99,
                        )
                    }
                ),
                FakeCandidateEvidenceExtractor(
                    {
                        application.selected_cv_file_id or "": CandidateEvidenceExtraction(
                            evidence=[
                                CandidateEvidence(
                                    text="Software Engineer",
                                    normalized_text="software_engineering",
                                    evidence_type=EvidenceType.ROLE,
                                    skill_name="software_engineering",
                                    source_fragment="Software Engineer",
                                    confidence=0.99,
                                )
                            ],
                            confidence=0.99,
                        )
                    }
                ),
                _ForbiddenRetriever(),
                _ForbiddenReranker(),
                DeterministicMatchScorer(),
            )
            aggregate = await rejecting_pipeline.match(
                application.id,
                vacancy_source_text="We are hiring a Sous Chef.",
                cv_source_text="Software Engineer",
            )

            assert aggregate.eligibility_status == "ineligible"
            assert session.scalars(select(RequirementMatchRow)).all() == []

    asyncio.run(run())
