"""Tests for matching performance optimizations.

Covers:
- Entailment early exit (stop after a strong entailed result).
- Entailment candidate cap (evaluate at most N candidates per claim).
- Deterministic decomposition of simple single-skill requirements (no LLM call).
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.matching.cache import MatchingCache
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
from app.matching.model_evaluators import RouterRequirementDecomposer
from app.matching.semantic import RerankedCandidate, RetrievalCandidate
from app.storage.database import Base
from app.storage.tables import (
    ApplicationRow,
    CandidateEvidenceRow,
    CvFileRow,
    UserRow,
    VacancyRequirementRow,
    VacancyRow,
)

# ── Early exit helpers ───────────────────────────────────────────


class _CountingEvaluator:
    model_name = "counting"
    model_version = "1"

    def __init__(self) -> None:
        self.call_count = 0

    async def evaluate(self, *, claim_id, evidence_id, **kwargs) -> EntailmentResult:
        self.call_count += 1
        return EntailmentResult(
            claim_id=claim_id,
            evidence_id=evidence_id,
            relation=EntailmentRelation.ENTAILED,
            confidence=0.9,
            coverage=0.95,
            evidence_strength=0.95,
            evidence_strength_category=EvidenceStrengthCategory.EXPLICIT,
        )


class _IdentityReranker:
    model_name = "identity"
    model_revision = "1"

    async def rerank(self, requirement_text, candidates):
        return tuple(
            RerankedCandidate(
                candidate=c,
                raw_score=c.hybrid_score,
                normalized_score=c.hybrid_score,
            )
            for c in candidates
        )


class _SingleSkillDecomposer:
    model_name = "d"
    model_version = "1"
    schema_version = "1"

    async def decompose(self, *, requirement_id, requirement_text, **kwargs):
        return RequirementDecomposition(
            requirement_id=requirement_id,
            requirement_text=requirement_text,
            requirement_criticality=RequirementCriticality.REQUIRED,
            claims=[
                AtomicClaim(
                    id=f"{requirement_id}:c1",
                    requirement_id=requirement_id,
                    claim_type=ClaimType.SKILL,
                    subject="Python",
                    normalized_subject="python",
                    criticality=Criticality.REQUIRED,
                    logical_group=LogicalGroup.AND,
                    source_text=requirement_text,
                )
            ],
            is_composite=False,
        )


class _ListRetriever:
    def __init__(self, candidates):
        self._candidates = candidates

    async def retrieve(self, requirement_text, *, user_id, cv_file_id):
        return self._candidates


def test_early_exit_stops_after_strong_entailed():
    async def run() -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        sf = sessionmaker(engine, expire_on_commit=False)
        with sf() as session:
            user = UserRow(display_name="u")
            session.add(user)
            session.flush()
            cv = CvFileRow(
                user_id=user.id,
                original_filename="r.txt",
                storage_path="r.txt",
                content_type="text/plain",
                sha256="a" * 64,
                size_bytes=10,
                analyzed_at=datetime.now(UTC),
            )
            session.add(cv)
            session.flush()
            vacancy = VacancyRow(
                source_url="https://example.test/v",
                title="t",
                company="c",
                required_skills=["Python"],
            )
            session.add(vacancy)
            session.flush()
            app = ApplicationRow(
                user_id=user.id,
                vacancy_id=vacancy.id,
                selected_cv_file_id=cv.id,
                status="awaiting_review",
                match_score=0,
            )
            session.add(app)
            session.flush()
            req = VacancyRequirementRow(
                vacancy_id=vacancy.id,
                requirement_text="Python",
                normalized_text="python",
                requirement_type="hard_skill",
                importance="required",
                weight=1.0,
                is_blocker=False,
                alternatives_json=[],
                source_fragment="Python",
                extraction_model="f",
                extraction_model_version="1",
                extraction_schema_version="1",
                extraction_run_id="r",
                confidence=1.0,
            )
            session.add(req)
            session.flush()
            evidence_ids = []
            for i in range(4):
                e = CandidateEvidenceRow(
                    user_id=user.id,
                    cv_file_id=cv.id,
                    evidence_text=f"evidence {i}",
                    normalized_text=f"evidence {i}",
                    evidence_type="skill_statement",
                    skill_name="Python",
                    experience_level="production",
                    is_verified=True,
                    source_fragment=f"evidence {i}",
                    extraction_model="f",
                    extraction_model_version="1",
                    extraction_schema_version="1",
                    extraction_run_id="r",
                    confidence=0.9,
                )
                session.add(e)
                session.flush()
                evidence_ids.append(e.id)

            candidates = tuple(
                RetrievalCandidate(
                    evidence_id=eid,
                    evidence_text=f"evidence {i}",
                    lexical_score=0.5,
                    dense_score=0.5,
                    hybrid_score=0.5,
                )
                for i, eid in enumerate(evidence_ids)
            )
            evaluator = _CountingEvaluator()
            pipe = ClaimMatchPipeline(
                session=session,
                decomposer=_SingleSkillDecomposer(),
                evaluator=evaluator,
                retriever=_ListRetriever(candidates),
                reranker=_IdentityReranker(),
                entailment_max_candidates=3,
            )
            result = await pipe.match_requirements(app.id, user.id, cv.id, (req,))

            # Strong entailed on the first candidate → the evaluator must run exactly once.
            assert evaluator.call_count == 1
            assert len(result.assessments) == 1
            assert result.assessments[0].entailment_relation is EntailmentRelation.ENTAILED

    asyncio.run(run())


# ── Deterministic decomposition ──────────────────────────────────


class _RaisingRouter:
    async def route(self, request, output_schema):
        raise AssertionError("Router must not be called for a simple skill")


class _FakePromptRegistry:
    def render(self, name, variables, version=None):
        return "prompt"


class _FakeRouter:
    def __init__(self, result):
        self._result = result
        self.called = False

    async def route(self, request, output_schema):
        self.called = True
        return self._result


def _composite_decomposition(requirement_id: str, text: str) -> RequirementDecomposition:
    return RequirementDecomposition(
        requirement_id=requirement_id,
        requirement_text=text,
        requirement_criticality=RequirementCriticality.REQUIRED,
        claims=[
            AtomicClaim(
                id=f"{requirement_id}:python",
                requirement_id=requirement_id,
                claim_type=ClaimType.SKILL,
                subject="Python",
                normalized_subject="python",
                criticality=Criticality.REQUIRED,
                logical_group=LogicalGroup.AND,
                source_text=text,
            ),
            AtomicClaim(
                id=f"{requirement_id}:sql",
                requirement_id=requirement_id,
                claim_type=ClaimType.SKILL,
                subject="SQL",
                normalized_subject="sql",
                criticality=Criticality.REQUIRED,
                logical_group=LogicalGroup.AND,
                source_text=text,
            ),
        ],
        is_composite=True,
    )


def test_deterministic_decomposition_of_simple_skill():
    async def run() -> None:
        decomposer = RouterRequirementDecomposer(
            _RaisingRouter(),
            _FakePromptRegistry(),
        )
        result = await decomposer.decompose(
            requirement_id="r1",
            requirement_text="Docker",
            requirement_type="hard_skill",
            importance="required",
            is_blocker=False,
        )
        assert result.is_composite is False
        assert len(result.claims) == 1
        assert result.claims[0].claim_type is ClaimType.SKILL
        assert result.claims[0].subject == "Docker"

    asyncio.run(run())


def test_composite_requirement_still_uses_llm():
    async def run() -> None:
        router = _FakeRouter(_composite_decomposition("r2", "Python and SQL"))
        decomposer = RouterRequirementDecomposer(
            router,
            _FakePromptRegistry(),
        )
        result = await decomposer.decompose(
            requirement_id="r2",
            requirement_text="Python and SQL",
            requirement_type="hard_skill",
            importance="required",
            is_blocker=False,
        )
        assert router.called is True
        assert len(result.claims) == 2

    asyncio.run(run())


def test_cached_decomposition_remapped_to_new_requirement_id():
    async def run() -> None:
        router = _FakeRouter(_composite_decomposition("old-req", "Python and SQL"))
        decomposer = RouterRequirementDecomposer(
            router,
            _FakePromptRegistry(),
            cache=MatchingCache(),
        )
        first = await decomposer.decompose(
            requirement_id="old-req",
            requirement_text="Python and SQL",
            requirement_type="hard_skill",
            importance="required",
            is_blocker=False,
        )
        assert router.called is True
        # Same content, different requirement row id → served from the content-based
        # cache and rebound to the new id without another LLM call.
        router.called = False
        second = await decomposer.decompose(
            requirement_id="new-req",
            requirement_text="Python and SQL",
            requirement_type="hard_skill",
            importance="required",
            is_blocker=False,
        )
        assert router.called is False
        assert second.requirement_id == "new-req"
        assert all(claim.requirement_id == "new-req" for claim in second.claims)
        assert second.claims[0].id.startswith("new-req")
        assert first.claims[0].id.startswith("old-req")

    asyncio.run(run())
