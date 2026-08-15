"""Tests for batched entailment evaluation.

Covers:
- Coalescing concurrent entailment requests into batched LLM calls.
- Batch size 1 keeping the exact single-pair path.
- Cache interplay (hits never reach the LLM; batch results are persisted per pair).
- Whole-batch route failures → per-item EVALUATION_ERROR (never UNKNOWN).
- Result alignment by claim_id with order fallback; missing items → per-item error.
- Token-budget packing for oversized evidence.
- close() draining the pending queue and stopping the flusher.
- End-to-end pipeline run with a batched evaluator.
"""

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.llm.router import ModelRequest
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
from app.matching.entailment import EntailmentRelation
from app.matching.model_evaluators import RouterEvidenceEvaluator
from app.matching.semantic import RerankedCandidate, RetrievalCandidate
from app.prompts.registry import PromptRegistry
from app.storage.database import Base
from app.storage.tables import (
    ApplicationRow,
    CandidateEvidenceRow,
    CvFileRow,
    UserRow,
    VacancyRequirementRow,
    VacancyRow,
)


def _raw(claim_id: str = "", relation: str = "entailed") -> dict:
    return {
        "claim_id": claim_id,
        "relation": relation,
        "confidence": 0.9,
        "reason": "r",
        "entailment_score": 0.9,
        "coverage": 0.95,
        "evidence_type": "direct",
        "experience_level": "commercial_production",
    }


class _FakePromptRegistry:
    def render(self, name, variables, version=None):
        return "prompt"


class _ScriptedRouter:
    """Router stand-in: validates payloads like ModelRouter and records calls."""

    def __init__(self, responses=None):
        self._responses = list(responses) if responses else []
        self.calls: list[ModelRequest] = []

    async def route(self, request: ModelRequest, output_schema):
        self.calls.append(request)
        payload = self._responses.pop(0) if self._responses else self._auto_response(request)
        return output_schema.model_validate(payload)

    def _auto_response(self, request: ModelRequest) -> dict:
        if request.task_name == "evaluate_evidence_entailment_batch":
            ids = re.findall(r"claim_id: (\S+)", request.prompt)
            return {"evaluations": [_raw(cid) for cid in ids]}
        return _raw()


class _SlowRouter(_ScriptedRouter):
    async def route(self, request: ModelRequest, output_schema):
        await asyncio.sleep(0.05)
        return await super().route(request, output_schema)


def _make_evaluator(router, *, batch_size=5, cache=None, timeout_seconds=60.0):
    return RouterEvidenceEvaluator(
        router,
        _FakePromptRegistry(),
        cache=cache,
        timeout_seconds=timeout_seconds,
        entailment_batch_size=batch_size,
    )


def _evaluate_args(i: int) -> dict:
    return {
        "claim_id": f"cid{i}",
        "claim_text": f"claim {i}",
        "claim_type": "skill",
        "evidence_id": f"eid{i}",
        "evidence_text": f"evidence {i}",
        "semantic_score": 0.5,
        "reranker_score": 0.6,
    }


def test_batched_evaluator_coalesces_concurrent_requests():
    async def run() -> None:
        router = _ScriptedRouter([{"evaluations": [_raw(f"cid{i}") for i in range(5)]}])
        evaluator = _make_evaluator(router, batch_size=5)
        results = await asyncio.gather(*(evaluator.evaluate(**_evaluate_args(i)) for i in range(5)))
        assert len(router.calls) == 1
        assert router.calls[0].task_name == "evaluate_evidence_entailment_batch"
        assert all(r.relation is EntailmentRelation.ENTAILED for r in results)
        assert [r.claim_id for r in results] == [f"cid{i}" for i in range(5)]
        assert [r.evidence_id for r in results] == [f"eid{i}" for i in range(5)]
        assert all(r.semantic_score == 0.5 and r.reranker_score == 0.6 for r in results)
        assert evaluator.requests_made == 1
        await evaluator.close()

    asyncio.run(run())


def test_batch_size_one_keeps_single_request_path():
    async def run() -> None:
        router = _ScriptedRouter()
        evaluator = _make_evaluator(router, batch_size=1)
        for i in range(3):
            await evaluator.evaluate(**_evaluate_args(i))
        assert len(router.calls) == 3
        assert all(c.task_name == "evaluate_evidence_entailment" for c in router.calls)
        assert evaluator.requests_made == 3
        await evaluator.close()

    asyncio.run(run())


def test_batch_cache_hits_never_reach_llm():
    async def run() -> None:
        router = _ScriptedRouter([{"evaluations": [_raw(f"cid{i}") for i in range(3)]}])
        evaluator = _make_evaluator(router, batch_size=3, cache=MatchingCache())
        await asyncio.gather(*(evaluator.evaluate(**_evaluate_args(i)) for i in range(3)))
        assert len(router.calls) == 1
        # Second pass over identical pairs must be served from the per-pair cache.
        await asyncio.gather(*(evaluator.evaluate(**_evaluate_args(i)) for i in range(3)))
        assert len(router.calls) == 1
        assert evaluator.requests_made == 1
        await evaluator.close()

    asyncio.run(run())


def test_batch_route_failure_yields_evaluation_error_per_item():
    async def run() -> None:
        router = _ScriptedRouter()

        async def failing_route(request, output_schema):
            router.calls.append(request)
            raise RuntimeError("boom")

        router.route = failing_route
        evaluator = _make_evaluator(router, batch_size=5)
        results = await asyncio.gather(*(evaluator.evaluate(**_evaluate_args(i)) for i in range(5)))
        assert all(r.relation is EntailmentRelation.EVALUATION_ERROR for r in results)
        assert all(r.error_type == "RuntimeError" for r in results)
        assert all("batch technical failure" in r.reason for r in results)
        assert evaluator.requests_made == 0
        await evaluator.close()

    asyncio.run(run())


def test_batch_missing_items_get_error_result():
    async def run() -> None:
        router = _ScriptedRouter([{"evaluations": [_raw(f"cid{i}") for i in range(3)]}])
        evaluator = _make_evaluator(router, batch_size=5)
        results = await asyncio.gather(*(evaluator.evaluate(**_evaluate_args(i)) for i in range(5)))
        assert [r.relation for r in results[:3]] == [
            EntailmentRelation.ENTAILED,
        ] * 3
        assert [r.relation for r in results[3:]] == [
            EntailmentRelation.EVALUATION_ERROR,
        ] * 2
        assert results[3].error_type == "batch_missing_item"
        assert results[4].error_type == "batch_missing_item"
        await evaluator.close()

    asyncio.run(run())


def test_batch_maps_by_claim_id_when_reordered():
    async def run() -> None:
        router = _ScriptedRouter([{"evaluations": [_raw(f"cid{i}") for i in reversed(range(4))]}])
        evaluator = _make_evaluator(router, batch_size=4)
        results = await asyncio.gather(*(evaluator.evaluate(**_evaluate_args(i)) for i in range(4)))
        assert [r.claim_id for r in results] == [f"cid{i}" for i in range(4)]
        assert [r.evidence_id for r in results] == [f"eid{i}" for i in range(4)]
        await evaluator.close()

    asyncio.run(run())


def test_batch_packing_splits_oversized_evidence():
    async def run() -> None:
        router = _ScriptedRouter(
            [
                {"evaluations": [_raw(f"cid{i}") for i in range(4)]},
                _raw("cid4"),
            ]
        )
        evaluator = _make_evaluator(router, batch_size=5)
        args = [{**_evaluate_args(i), "evidence_text": "x" * 3000} for i in range(5)]
        results = await asyncio.gather(*(evaluator.evaluate(**a) for a in args))
        # 4 × ~750-token evidences fit the 4096 context; the 5th is packed alone.
        assert len(router.calls) == 2
        assert router.calls[0].task_name == "evaluate_evidence_entailment_batch"
        assert router.calls[1].task_name == "evaluate_evidence_entailment"
        # The batch output budget must be capped so input + output fit num_ctx.
        assert router.calls[0].max_output_tokens < 512 * 4
        assert all(r.relation is EntailmentRelation.ENTAILED for r in results)
        await evaluator.close()

    asyncio.run(run())


def test_close_drains_pending_and_stops_flusher():
    async def run() -> None:
        router = _SlowRouter(
            [
                _raw("cid0"),
                {"evaluations": [_raw("cid1"), _raw("cid2")]},
            ]
        )
        evaluator = _make_evaluator(router, batch_size=5)
        first = asyncio.create_task(evaluator.evaluate(**_evaluate_args(0)))
        await asyncio.sleep(0.02)  # flusher is mid-request on cid0
        rest = [asyncio.create_task(evaluator.evaluate(**_evaluate_args(i))) for i in (1, 2)]
        results = await asyncio.gather(first, *rest)
        # cid0 was already in flight when cid1/cid2 arrived → 2 calls, all resolved.
        assert len(router.calls) == 2
        assert all(r.relation is EntailmentRelation.ENTAILED for r in results)
        await evaluator.close()
        assert evaluator._flusher_task is None
        assert evaluator._queue is not None and evaluator._queue.qsize() == 0

    asyncio.run(run())


# ── Pipeline integration ─────────────────────────────────────────


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
                    subject=requirement_text,
                    normalized_subject=requirement_text.lower(),
                    criticality=Criticality.REQUIRED,
                    logical_group=LogicalGroup.AND,
                    source_text=requirement_text,
                )
            ],
            is_composite=False,
        )


class _IdentityReranker:
    async def rerank(self, requirement_text, candidates):
        return tuple(
            RerankedCandidate(
                candidate=c,
                raw_score=c.hybrid_score,
                normalized_score=c.hybrid_score,
            )
            for c in candidates
        )


class _ListRetriever:
    def __init__(self, candidates):
        self._candidates = candidates

    async def retrieve(self, requirement_text, *, user_id, cv_file_id):
        return self._candidates


def test_pipeline_with_batched_evaluator_completes_and_closes():
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
                required_skills=["Python", "SQL"],
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
            requirements = []
            for text in ("Python", "SQL"):
                req = VacancyRequirementRow(
                    vacancy_id=vacancy.id,
                    requirement_text=text,
                    normalized_text=text.lower(),
                    requirement_type="hard_skill",
                    importance="required",
                    weight=1.0,
                    is_blocker=False,
                    alternatives_json=[],
                    source_fragment=text,
                    extraction_model="f",
                    extraction_model_version="1",
                    extraction_schema_version="1",
                    extraction_run_id="r",
                    confidence=1.0,
                )
                session.add(req)
                session.flush()
                requirements.append(req)
            evidence_ids = []
            for i in range(3):
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

            registry = PromptRegistry.load(Path(__file__).parents[2] / "prompts" / "registry.json")
            router = _ScriptedRouter()
            evaluator = RouterEvidenceEvaluator(
                router,
                registry,
                entailment_batch_size=3,
            )
            pipe = ClaimMatchPipeline(
                session=session,
                decomposer=_SingleSkillDecomposer(),
                evaluator=evaluator,
                retriever=_ListRetriever(candidates),
                reranker=_IdentityReranker(),
                entailment_max_candidates=2,
            )
            result = await pipe.match_requirements(app.id, user.id, cv.id, tuple(requirements))
            assert len(result.assessments) == 2
            assert all(
                a.entailment_relation is EntailmentRelation.ENTAILED for a in result.assessments
            )
            # Both first-candidate evaluations coalesced into one batched call,
            # and each entailed at coverage 0.95 triggered early exit.
            assert len(router.calls) == 1
            assert router.calls[0].task_name == "evaluate_evidence_entailment_batch"
            # The pipeline closed the batched evaluator after matching.
            assert evaluator._closed is True

    asyncio.run(run())
