"""Regression test for Neuroengineer vacancy matching.

Verifies that the matching pipeline produces realistic coverage-based scores
for a ML/RAG/VLM profile against a complex vacancy.
Target overall score: 70-85% with current evidence set.
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
    ClaimExperienceLevel,
    EntailmentRelation,
    EntailmentResult,
    EvidenceStrengthCategory,
    EvidenceType,
)
from app.matching.scoring import (
    DeterministicMatchScorer,
    EligibilityStatus,
    MatchLevel,
)
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


def _session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _create_application(session: Session) -> ApplicationRow:
    user = UserRow(display_name="ML Engineer")
    session.add(user)
    session.flush()
    cv_file = CvFileRow(
        user_id=user.id,
        original_filename="resume_ml.txt",
        storage_path="resume_ml.txt",
        content_type="text/plain",
        sha256="a" * 64,
        size_bytes=200,
        analyzed_at=datetime.now(UTC),
    )
    vacancy = VacancyRow(
        source_url="https://example.test/vacancy/neuroengineer",
        title="ML-инженер — RAG, VLM и on-prem инференс",
        company="Нейроинженер",
        required_skills=["Python", "ML/NLP", "RAG", "VLM", "Docker"],
        description_text="ML engineer with RAG, VLM, self-hosted inference",
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


def test_neuroengineer_regression():
    """Regression: ML/RAG/VLM profile against Neuroengineer vacancy."""

    async def run():
        sf = _session_factory()
        with sf() as session:
            app = _create_application(session)

            # ── Create evidence rows ──
            ev_defs = {
                "ev-python": (
                    "Python/FastAPI backend, PostgreSQL, Redis, Docker.",
                    "Python", "production",
                ),
                "ev-ml": (
                    "NLP pipelines with PyTorch, Hugging Face transformers.",
                    "ML/NLP", "hands_on",
                ),
                "ev-rag": (
                    "RAG service with embeddings, OpenSearch, reranking.",
                    "RAG", "production",
                ),
                "ev-selfhosted": (
                    "Local LLM inference with Ollama, GGUF quantized models.",
                    "self_hosted_inference", "hands_on",
                ),
                "ev-docker": (
                    "Docker, docker-compose multi-service deployments.",
                    "Docker", "production",
                ),
                "ev-postgres": (
                    "PostgreSQL schemas with SQLAlchemy/Alembic.",
                    "PostgreSQL", "production",
                ),
                "ev-redis": (
                    "Redis for caching, session management, coordination.",
                    "Redis", "production",
                ),
                "ev-embeddings": (
                    "Multilingual-e5-small embeddings for search.",
                    "embeddings", "hands_on",
                ),
                "ev-reranking": (
                    "Cross-encoder reranking pipeline.",
                    "reranking", "production",
                ),
                "ev-llm": (
                    "LLM providers (Anthropic, Gemini, OpenAI-compatible).",
                    "LLM", "hands_on",
                ),
                "ev-vlm": (
                    "Experimented with Qwen-VL for document OCR.",
                    "VLM", "experiment",
                ),
                "ev-pytorch": (
                    "PyTorch model training, custom loss functions.",
                    "PyTorch", "hands_on",
                ),
                "ev-hf": (
                    "Hugging Face transformers and datasets.",
                    "Hugging Face", "hands_on",
                ),
                "ev-async": (
                    "Async Python services with asyncio.",
                    "async", "production",
                ),
            }
            ev_rows = {}
            for ev_id, (text, skill, exp_level) in ev_defs.items():
                row = CandidateEvidenceRow(
                    user_id=app.user_id,
                    cv_file_id=app.selected_cv_file_id,
                    evidence_text=text,
                    normalized_text=text.lower()[:200],
                    evidence_type="work_experience",
                    skill_name=skill,
                    experience_level=exp_level,
                    is_verified=True,
                    source_fragment=text[:100],
                    extraction_model="regression",
                    extraction_model_version="1",
                    extraction_schema_version="1",
                    extraction_run_id="regression-run",
                    confidence=0.9,
                )
                session.add(row)
                session.flush()
                ev_rows[ev_id] = row

            # ── Create requirement rows ──
            req_defs = [
                ("req-python", "Уверенный Python", "hard_skill", "required", 1.0),
                ("req-fastapi", "FastAPI", "hard_skill", "required", 0.8),
                ("req-docker", "Docker", "hard_skill", "required", 0.7),
                ("req-postgres", "PostgreSQL", "hard_skill", "required", 0.7),
                ("req-redis", "Redis", "hard_skill", "required", 0.5),
                ("req-ml", "ML/NLP опыт от 3 лет", "experience", "required", 1.0),
                ("req-rag", "Практический опыт RAG", "hard_skill", "required", 1.0),
                ("req-embeddings", "Embeddings", "hard_skill", "required", 0.6),
                ("req-reranking", "Reranking/cross-encoder", "hard_skill", "required", 0.7),
                ("req-llm", "LLM integration", "hard_skill", "required", 0.8),
                ("req-vlm", "VLM опыт", "hard_skill", "preferred", 0.6),
                ("req-selfhosted", "Self-hosted inference", "hard_skill", "preferred", 0.7),
                ("req-pytorch", "PyTorch", "hard_skill", "preferred", 0.6),
                ("req-hf", "Hugging Face", "hard_skill", "preferred", 0.5),
            ]
            req_rows = {}
            for req_id, text, rtype, importance, weight in req_defs:
                row = VacancyRequirementRow(
                    vacancy_id=app.vacancy_id,
                    requirement_text=text,
                    normalized_text=text.lower(),
                    requirement_type=rtype,
                    importance=importance,
                    weight=weight,
                    is_blocker=False,
                    alternatives_json=[],
                    source_fragment=text,
                    extraction_model="regression",
                    extraction_model_version="1",
                    extraction_schema_version="1",
                    extraction_run_id="regression-run",
                    confidence=0.95,
                )
                session.add(row)
                session.flush()
                req_rows[req_id] = row

            # ── Build decompositions keyed by DB row ID ──
            decompositions: dict[str, RequirementDecomposition] = {}

            def add_claim(
                req_key, claim_suffix, claim_type, subject,
                criticality=Criticality.REQUIRED,
            ):
                db_id = req_rows[req_key].id
                if db_id not in decompositions:
                    decompositions[db_id] = RequirementDecomposition(
                        requirement_id=db_id,
                        requirement_text=req_rows[req_key].requirement_text,
                        requirement_criticality=RequirementCriticality.REQUIRED,
                        claims=[],
                        is_composite=False,
                    )
                decompositions[db_id].claims.append(
                    AtomicClaim(
                        id=f"{db_id}-{claim_suffix}",
                        requirement_id=db_id,
                        claim_type=claim_type,
                        subject=subject,
                        normalized_subject=subject.lower(),
                        criticality=criticality,
                        logical_group=LogicalGroup.AND,
                        source_text=req_rows[req_key].requirement_text,
                    )
                )

            # ── Build evaluator results ──
            eval_results: dict[str, EntailmentResult] = {}

            def add_eval(
                req_key, ev_key, claim_suffix, relation, coverage,
                evidence_type, exp_level, strength,
            ):
                claim_id = f"{req_rows[req_key].id}-{claim_suffix}"
                ev = ev_rows[ev_key]
                cat = (
                    EvidenceStrengthCategory.STRONG if strength >= 0.75
                    else EvidenceStrengthCategory.PARTIAL if strength >= 0.55
                    else EvidenceStrengthCategory.WEAK
                )
                eval_results[f"{claim_id}:{ev.id}"] = EntailmentResult(
                    claim_id=claim_id,
                    evidence_id=ev.id,
                    relation=relation,
                    confidence=0.9,
                    reason=f"Regression: {relation.value}",
                    semantic_score=0.7,
                    reranker_score=0.8,
                    entailment_score=coverage,
                    evidence_strength=strength,
                    evidence_strength_category=cat,
                    coverage=coverage,
                    evidence_type=evidence_type,
                    experience_level=exp_level,
                )

            # Python → supported
            add_claim("req-python", "c1", ClaimType.SKILL, "Python")
            add_eval(
                "req-python", "ev-python", "c1",
                EntailmentRelation.ENTAILED, 0.95,
                EvidenceType.DIRECT,
                ClaimExperienceLevel.COMMERCIAL_PRODUCTION, 0.95,
            )

            # FastAPI → supported
            add_claim("req-fastapi", "c1", ClaimType.TECHNOLOGY, "FastAPI")
            add_eval(
                "req-fastapi", "ev-python", "c1",
                EntailmentRelation.ENTAILED, 0.95,
                EvidenceType.DIRECT,
                ClaimExperienceLevel.COMMERCIAL_PRODUCTION, 0.95,
            )

            # Docker → supported
            add_claim("req-docker", "c1", ClaimType.TECHNOLOGY, "Docker")
            add_eval(
                "req-docker", "ev-docker", "c1",
                EntailmentRelation.ENTAILED, 0.95,
                EvidenceType.DIRECT,
                ClaimExperienceLevel.COMMERCIAL_PRODUCTION, 0.95,
            )

            # PostgreSQL → supported
            add_claim("req-postgres", "c1", ClaimType.TECHNOLOGY, "PostgreSQL")
            add_eval(
                "req-postgres", "ev-postgres", "c1",
                EntailmentRelation.ENTAILED, 0.95,
                EvidenceType.DIRECT,
                ClaimExperienceLevel.COMMERCIAL_PRODUCTION, 0.95,
            )

            # Redis → supported
            add_claim("req-redis", "c1", ClaimType.TECHNOLOGY, "Redis")
            add_eval(
                "req-redis", "ev-redis", "c1",
                EntailmentRelation.ENTAILED, 0.85,
                EvidenceType.DIRECT,
                ClaimExperienceLevel.COMMERCIAL_PRODUCTION, 0.85,
            )

            # ML/NLP → supported
            add_claim("req-ml", "c1", ClaimType.PRACTICAL_EXPERIENCE, "ML/NLP")
            add_eval(
                "req-ml", "ev-ml", "c1",
                EntailmentRelation.ENTAILED, 0.90,
                EvidenceType.DIRECT,
                ClaimExperienceLevel.WORKING_PERSONAL_PROJECT, 0.90,
            )

            # RAG → supported
            add_claim("req-rag", "c1", ClaimType.PRACTICAL_EXPERIENCE, "RAG")
            add_eval(
                "req-rag", "ev-rag", "c1",
                EntailmentRelation.ENTAILED, 0.90,
                EvidenceType.DIRECT,
                ClaimExperienceLevel.COMMERCIAL_PRODUCTION, 0.90,
            )

            # Embeddings → supported
            add_claim("req-embeddings", "c1", ClaimType.TECHNOLOGY, "embeddings")
            add_eval(
                "req-embeddings", "ev-embeddings", "c1",
                EntailmentRelation.ENTAILED, 0.90,
                EvidenceType.DIRECT,
                ClaimExperienceLevel.WORKING_PERSONAL_PROJECT, 0.90,
            )

            # Reranking → supported
            add_claim("req-reranking", "c1", ClaimType.TECHNOLOGY, "reranking")
            add_eval(
                "req-reranking", "ev-reranking", "c1",
                EntailmentRelation.ENTAILED, 0.95,
                EvidenceType.DIRECT,
                ClaimExperienceLevel.COMMERCIAL_PRODUCTION, 0.95,
            )

            # LLM → supported
            add_claim(
                "req-llm", "c1", ClaimType.TECHNOLOGY, "LLM integration",
            )
            add_eval(
                "req-llm", "ev-llm", "c1",
                EntailmentRelation.ENTAILED, 0.90,
                EvidenceType.DIRECT,
                ClaimExperienceLevel.WORKING_PERSONAL_PROJECT, 0.90,
            )

            # VLM → partial (experiment only)
            add_claim("req-vlm", "c1", ClaimType.PRACTICAL_EXPERIENCE, "VLM")
            add_eval(
                "req-vlm", "ev-vlm", "c1",
                EntailmentRelation.PARTIAL, 0.30,
                EvidenceType.INDIRECT,
                ClaimExperienceLevel.EXPERIMENT, 0.30,
            )

            # Self-hosted → supported
            add_claim(
                "req-selfhosted", "c1",
                ClaimType.PRACTICAL_EXPERIENCE, "self-hosted inference",
            )
            add_eval(
                "req-selfhosted", "ev-selfhosted", "c1",
                EntailmentRelation.ENTAILED, 0.85,
                EvidenceType.DIRECT,
                ClaimExperienceLevel.WORKING_PERSONAL_PROJECT, 0.85,
            )

            # PyTorch → supported
            add_claim("req-pytorch", "c1", ClaimType.TECHNOLOGY, "PyTorch")
            add_eval(
                "req-pytorch", "ev-pytorch", "c1",
                EntailmentRelation.ENTAILED, 0.90,
                EvidenceType.DIRECT,
                ClaimExperienceLevel.WORKING_PERSONAL_PROJECT, 0.90,
            )

            # Hugging Face → supported
            add_claim("req-hf", "c1", ClaimType.TECHNOLOGY, "Hugging Face")
            add_eval(
                "req-hf", "ev-hf", "c1",
                EntailmentRelation.ENTAILED, 0.85,
                EvidenceType.DIRECT,
                ClaimExperienceLevel.WORKING_PERSONAL_PROJECT, 0.85,
            )

            # ── Build pipeline ──
            class Decomposer:
                model_name = "regression"
                model_version = "1"
                schema_version = "2"

                async def decompose(self, *, requirement_id, **kw):
                    return decompositions[requirement_id]

            class Evaluator:
                model_name = "regression"
                model_version = "1"

                async def evaluate(
                    self, *, claim_id, evidence_id, **kw,
                ):
                    key = f"{claim_id}:{evidence_id}"
                    if key in eval_results:
                        return eval_results[key]
                    return EntailmentResult(
                        claim_id=claim_id,
                        evidence_id=evidence_id,
                        relation=EntailmentRelation.RELATED_BUT_INSUFFICIENT,
                        confidence=0.3,
                        reason="No specific evaluation",
                        semantic_score=0.3,
                        reranker_score=0.3,
                        entailment_score=0.2,
                        evidence_strength=0.2,
                        evidence_strength_category=(
                            EvidenceStrengthCategory.WEAK
                        ),
                        coverage=0.0,
                        evidence_type=EvidenceType.NONE,
                        experience_level=ClaimExperienceLevel.NONE,
                    )

            class Retriever:
                async def retrieve(self, q, *, user_id, cv_file_id):
                    return [
                        RetrievalCandidate(
                            evidence_id=ev.id,
                            evidence_text=ev.evidence_text,
                            lexical_score=0.5,
                            dense_score=0.6,
                            hybrid_score=0.55,
                        )
                        for ev in ev_rows.values()
                    ]

            pipe = ClaimMatchPipeline(
                session=session,
                decomposer=Decomposer(),
                evaluator=Evaluator(),
                retriever=Retriever(),
                reranker=FakeReranker(),
                reranker_top_k=20,
                entailment_max_candidates=20,
            )

            req_tuple = tuple(req_rows.values())
            result = await pipe.match_requirements(
                app.id, app.user_id, app.selected_cv_file_id, req_tuple,
            )

            scorer = DeterministicMatchScorer()
            score = scorer.score(tuple(result.assessments))

            # ── Assertions ──
            assert score.final_score >= 70, (
                f"Expected final_score >= 70, got {score.final_score}"
            )
            assert score.scoring_version == "matching-v3.0"
            assert score.calibration_version == "identity"
            assert score.raw_score_before_blockers >= score.final_score
            assert score.required_score >= 60, (
                f"required_score={score.required_score}"
            )
            assert score.preferred_score >= 30, (
                f"preferred_score={score.preferred_score}"
            )
            assert score.eligibility_status in (
                EligibilityStatus.ELIGIBLE,
                EligibilityStatus.REVIEW,
            )
            assert score.confidence >= 0.5, (
                f"confidence={score.confidence}"
            )

            # Per-requirement checks
            assessment_by_id = {
                a.requirement_id: a for a in result.assessments
            }
            for req_key in [
                "req-python", "req-fastapi", "req-docker",
                "req-postgres", "req-rag",
            ]:
                a = assessment_by_id.get(req_rows[req_key].id)
                assert a is not None, f"Missing assessment for {req_key}"
                assert a.match_level in (
                    MatchLevel.EXACT, MatchLevel.STRONG,
                ), f"{req_key}: got {a.match_level}"

            vlm = assessment_by_id.get(req_rows["req-vlm"].id)
            assert vlm is not None
            assert vlm.match_level in (
                MatchLevel.PARTIAL,
                MatchLevel.RELATED,
                MatchLevel.THEORETICAL_ONLY,
            ), f"VLM: got {vlm.match_level}"

            # Gap analysis
            assert result.gap_analysis is not None
            print(
                "\n=== Neuroengineer Regression ==="
                f"\nFinal: {score.final_score}"
                f", Raw: {score.raw_score_before_blockers}"
                f"\nRequired: {score.required_score}"
                f", Preferred: {score.preferred_score}"
                f"\nConfidence: {score.confidence}"
                f", Eligibility: {score.eligibility_status}"
                f"\nGaps: {result.gap_analysis.total_gaps}"
            )

    asyncio.run(run())
