"""LLM-backed claim decomposer and evidence evaluator.

Uses the existing ModelRouter to perform structured LLM calls
for requirement decomposition and evidence entailment evaluation.
Results are cached with content-based keys (including the resolved model
identity), so a smart recalculation reuses prior LLM work across runs.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from app.llm.router import ModelRequest, ModelRouter, ModelTaskClass
from app.matching.cache import (
    MatchingCache,
    build_decomposition_cache_key,
    build_entailment_cache_key,
)
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
    EvidenceType,
    compute_evidence_strength,
)
from app.matching.extraction import StrictExtractionModel
from app.matching.normalization import SkillNormalizer
from app.prompts.registry import PromptRegistry

_PROMPT_VERSION_DECOMPOSE = "2"
_PROMPT_VERSION_ENTAIL = "3"
_PROMPT_NAME_ENTAIL_BATCH = "evaluate_evidence_entailment_batch"

# Batched entailment knobs. The batch prompt evaluates several (claim, evidence)
# pairs in one LLM call; Ollama then generates the whole batch in a single
# request, amortizing per-request overhead. max_tokens is a cap, not a target,
# so the output budget below is a conservative estimate used only for packing.
_ESTIMATED_OUTPUT_TOKENS_PER_ITEM = 160
_ESTIMATED_CHARS_PER_TOKEN = 4

# Base entailment scores when LLM doesn't provide one
_RELATION_BASE_SCORE = {
    EntailmentRelation.ENTAILED: 0.95,
    EntailmentRelation.PARTIAL: 0.6,
    EntailmentRelation.RELATED_BUT_INSUFFICIENT: 0.3,
    EntailmentRelation.INSUFFICIENT_EVIDENCE: 0.0,
    EntailmentRelation.CONTRADICTED: 0.0,
    EntailmentRelation.EVALUATION_ERROR: 0.0,
    EntailmentRelation.UNKNOWN: 0.0,
}

# Deterministic decomposition of obvious single-skill requirements. A single-skill
# requirement (e.g. "PostgreSQL", "Python", "Docker") does not need an LLM decomposition
# call — it is already a single atomic claim. Composite requirements (conjunctions,
# quantifiers, durations, "knowledge of X and Y") still go through the LLM.
_SIMPLE_SKILL_REQUIREMENT_TYPES = frozenset({"hard_skill", "technology", "domain"})
_MAX_SIMPLE_SKILL_WORDS = 4
_MAX_SIMPLE_SKILL_LENGTH = 80
_COMPOSITE_MARKER = re.compile(
    r"[,;/()]|\b(and|or|или|plus|experience|опыт|опыта|years|лет|год|минимум|minimum|"
    r"at least|knowledge|знание|знания|уровень|level)\b",
    re.IGNORECASE,
)


def _criticality_for(importance: str, is_blocker: bool) -> Criticality:
    if is_blocker:
        return Criticality.HARD_BLOCKER
    if importance == "required":
        return Criticality.REQUIRED
    return Criticality.PREFERRED


def _requirement_criticality_for(importance: str, is_blocker: bool) -> RequirementCriticality:
    if is_blocker:
        return RequirementCriticality.HARD_BLOCKER
    if importance == "required":
        return RequirementCriticality.REQUIRED
    return RequirementCriticality.PREFERRED


class RouterRequirementDecomposer:
    """Decomposes vacancy requirements into atomic claims via LLM."""

    model_name = "model-router"
    model_version = "provider-selected"
    schema_version = "2"

    def __init__(
        self,
        router: ModelRouter,
        prompt_registry: PromptRegistry,
        *,
        skill_normalizer: SkillNormalizer | None = None,
        cache: MatchingCache | None = None,
        timeout_seconds: float = 60,
        model_identity: str | None = None,
        decompose_max_tokens: int = 2048,
        decompose_context_size: int = 4096,
    ) -> None:
        self._router = router
        self._prompt_registry = prompt_registry
        self._skill_normalizer = skill_normalizer or SkillNormalizer()
        self._cache = cache
        self._timeout_seconds = timeout_seconds
        self._model_identity = model_identity or self.model_name
        self._decompose_max_tokens = decompose_max_tokens
        self._decompose_context_size = decompose_context_size

    async def decompose(
        self,
        *,
        requirement_id: str,
        requirement_text: str,
        requirement_type: str,
        importance: str,
        is_blocker: bool,
    ) -> RequirementDecomposition:
        deterministic = self._deterministic_single_skill_claim(
            requirement_id,
            requirement_text,
            requirement_type,
            importance,
            is_blocker,
        )
        if deterministic is not None:
            return deterministic

        cache_key: str | None = None
        if self._cache is not None:
            cache_key = build_decomposition_cache_key(
                vacancy_id="",
                requirement_text=requirement_text,
                model_name=self._model_identity,
                prompt_version=_PROMPT_VERSION_DECOMPOSE,
                requirement_type=requirement_type,
                importance=importance,
                is_blocker=str(is_blocker),
            )
            cached = await self._cache.get_decomposition(cache_key)
            if cached is not None:
                return self._remap_decomposition(cached, requirement_id)

        prompt = self._prompt_registry.render(
            "decompose_requirement",
            {
                "requirement_id": requirement_id,
                "requirement_text": requirement_text,
                "requirement_type": requirement_type,
                "importance": importance,
                "is_blocker": str(is_blocker),
            },
        )
        result = await self._router.route(
            ModelRequest(
                task_name="decompose_requirement",
                task_class=ModelTaskClass.LOW_COST,
                prompt=prompt,
                max_cost_usd=0.05,
                timeout_seconds=self._timeout_seconds,
                max_output_tokens=self._decompose_max_tokens,
                context_size=self._decompose_context_size,
            ),
            RequirementDecomposition,
        )
        normalized_claims = []
        for claim in result.claims:
            normalized = self._skill_normalizer.normalize(claim.subject)
            normalized_claims.append(
                claim.model_copy(update={"normalized_subject": normalized.canonical})
            )
        result = result.model_copy(update={"claims": normalized_claims})

        if self._cache is not None and cache_key is not None:
            await self._cache.set_decomposition(cache_key, result)

        return result

    def _deterministic_single_skill_claim(
        self,
        requirement_id: str,
        requirement_text: str,
        requirement_type: str,
        importance: str,
        is_blocker: bool,
    ) -> RequirementDecomposition | None:
        if requirement_type not in _SIMPLE_SKILL_REQUIREMENT_TYPES:
            return None
        text = " ".join(requirement_text.split())
        if not text or len(text) > _MAX_SIMPLE_SKILL_LENGTH:
            return None
        if len(text.split()) > _MAX_SIMPLE_SKILL_WORDS:
            return None
        if _COMPOSITE_MARKER.search(text):
            return None
        normalized = self._skill_normalizer.normalize(text).canonical
        claim = AtomicClaim(
            id=f"{requirement_id}:skill",
            requirement_id=requirement_id,
            claim_type=ClaimType.SKILL,
            subject=text,
            normalized_subject=normalized,
            criticality=_criticality_for(importance, is_blocker),
            logical_group=LogicalGroup.AND,
            source_text=text,
        )
        return RequirementDecomposition(
            requirement_id=requirement_id,
            requirement_text=requirement_text,
            requirement_criticality=_requirement_criticality_for(importance, is_blocker),
            claims=[claim],
            is_composite=False,
            decomposition_confidence=1.0,
        )

    @staticmethod
    def _remap_decomposition(
        decomposition: RequirementDecomposition,
        requirement_id: str,
    ) -> RequirementDecomposition:
        """Rebind a cached decomposition to the current requirement row id.

        Claim ids generated by the LLM are prefixed with the requirement id, so the
        remap rewrites both the decomposition owner and every claim id/owner.
        """
        if decomposition.requirement_id == requirement_id:
            return decomposition
        old_id = decomposition.requirement_id
        remapped_claims = []
        for claim in decomposition.claims:
            claim_id = claim.id
            if old_id and claim_id.startswith(old_id):
                claim_id = requirement_id + claim_id[len(old_id) :]
            remapped_claims.append(
                claim.model_copy(update={"id": claim_id, "requirement_id": requirement_id})
            )
        return decomposition.model_copy(
            update={"requirement_id": requirement_id, "claims": remapped_claims}
        )


class RouterEvidenceEvaluator:
    """Evaluates evidence entailment against claims via LLM.

    Key design: technical failures (timeout, invalid JSON, provider error)
    are reported as EVALUATION_ERROR, never as UNKNOWN or MISSING.
    """

    model_name = "model-router"
    model_version = "provider-selected"

    def __init__(
        self,
        router: ModelRouter,
        prompt_registry: PromptRegistry,
        *,
        cache: MatchingCache | None = None,
        timeout_seconds: float = 60,
        model_identity: str | None = None,
        entailment_max_tokens: int = 512,
        entailment_context_size: int = 4096,
        entailment_batch_size: int = 1,
        entailment_model: str | None = None,
    ) -> None:
        self._router = router
        self._prompt_registry = prompt_registry
        self._cache = cache
        self._timeout_seconds = timeout_seconds
        self._model_identity = model_identity or self.model_name
        self._entailment_max_tokens = entailment_max_tokens
        self._entailment_context_size = entailment_context_size
        # 1 disables batching entirely and keeps the exact single-pair behavior.
        self._entailment_batch_size = max(1, entailment_batch_size)
        # Optional per-task model override (e.g. a small local model for
        # classification). Cache keys must reflect it, otherwise entailment
        # results from different models would be silently reused.
        self._entailment_model = (entailment_model or "").strip() or None
        if self._entailment_model is not None and self._entailment_model != self._model_identity:
            self._entailment_identity = f"{self._model_identity}|entail:{self._entailment_model}"
        else:
            self._entailment_identity = self._model_identity
        self._queue: asyncio.Queue[_PendingEntailment | None] | None = None
        self._flusher_task: asyncio.Task[None] | None = None
        self._closed = False
        # Number of actual LLM requests issued (batches count as one request).
        self._requests_made = 0

    @property
    def requests_made(self) -> int:
        """Actual LLM requests issued, including batched calls as one request each."""
        return self._requests_made

    async def evaluate(
        self,
        *,
        claim_id: str,
        claim_text: str,
        claim_type: str,
        evidence_id: str,
        evidence_text: str,
        semantic_score: float | None = None,
        reranker_score: float | None = None,
        claim_subject: str | None = None,
        claim_criticality: str | None = None,
        source_requirement: str | None = None,
    ) -> EntailmentResult:
        request = _EntailmentRequest(
            claim_id=claim_id,
            claim_text=claim_text,
            claim_type=claim_type,
            evidence_id=evidence_id,
            evidence_text=evidence_text,
            semantic_score=semantic_score,
            reranker_score=reranker_score,
            claim_subject=claim_subject,
            claim_criticality=claim_criticality,
            source_requirement=source_requirement,
        )
        cache_key = self._cache_key_for(request)
        if cache_key is not None:
            cached = await self._cache_get(cache_key, request)
            if cached is not None:
                return cached
        if self._entailment_batch_size <= 1:
            return await self._evaluate_single_uncached(request)
        return await self._enqueue(request, cache_key)

    # ── Cache helpers ───────────────────────────────────────────

    def _cache_key_for(self, request: _EntailmentRequest) -> str | None:
        if self._cache is None:
            return None
        return build_entailment_cache_key(
            claim_text=request.claim_text,
            evidence_text=request.evidence_text,
            claim_type=request.claim_type,
            model_name=self._entailment_identity,
            prompt_version=_PROMPT_VERSION_ENTAIL,
            source_requirement=request.source_requirement or "",
        )

    async def _cache_get(
        self,
        key: str,
        request: _EntailmentRequest,
    ) -> EntailmentResult | None:
        assert self._cache is not None
        cached = await self._cache.get_entailment(key)
        if cached is None:
            return None
        return cached.model_copy(
            update={
                "claim_id": request.claim_id,
                "evidence_id": request.evidence_id,
                "semantic_score": request.semantic_score,
                "reranker_score": request.reranker_score,
            }
        )

    async def _cache_set(self, key: str, result: EntailmentResult) -> None:
        assert self._cache is not None
        await self._cache.set_entailment(key, result)

    # ── Prompt helpers ──────────────────────────────────────────

    def _claim_context_for(self, request: _EntailmentRequest) -> str:
        claim_context_parts = [f"Claim: {request.claim_text}", f"Type: {request.claim_type}"]
        if request.claim_subject:
            claim_context_parts.append(f"Subject: {request.claim_subject}")
        if request.claim_criticality:
            claim_context_parts.append(f"Criticality: {request.claim_criticality}")
        if request.source_requirement:
            claim_context_parts.append(f"Source requirement: {request.source_requirement}")
        return "\n".join(claim_context_parts)

    def _prompt_for(self, request: _EntailmentRequest) -> str:
        return self._prompt_registry.render(
            "evaluate_evidence_entailment",
            {
                "claim_id": request.claim_id,
                "claim_text": self._claim_context_for(request),
                "claim_type": request.claim_type,
                "evidence_id": request.evidence_id,
                "evidence_text": request.evidence_text,
            },
        )

    # ── Single-pair path (exact previous behavior) ──────────────

    async def _evaluate_single_uncached(self, request: _EntailmentRequest) -> EntailmentResult:
        prompt = self._prompt_for(request)

        # Technical failures → EVALUATION_ERROR, never UNKNOWN
        try:
            raw_result = await self._router.route(
                ModelRequest(
                    task_name="evaluate_evidence_entailment",
                    task_class=ModelTaskClass.LOW_COST,
                    prompt=prompt,
                    max_cost_usd=0.03,
                    timeout_seconds=self._timeout_seconds,
                    max_output_tokens=self._entailment_max_tokens,
                    context_size=self._entailment_context_size,
                    model_override=self._entailment_model,
                ),
                _RawEntailmentResult,
            )
        except Exception as error:
            error_type = type(error).__name__
            return self._build_error_result(
                request.claim_id,
                request.evidence_id,
                request.semantic_score,
                request.reranker_score,
                error_type=error_type,
                reason=f"Evaluator technical failure ({error_type})",
            )
        self._requests_made += 1

        result = self._finalize_result(
            raw_result,
            claim_id=request.claim_id,
            evidence_id=request.evidence_id,
            semantic_score=request.semantic_score,
            reranker_score=request.reranker_score,
        )

        cache_key = self._cache_key_for(request)
        if cache_key is not None:
            await self._cache_set(cache_key, result)

        return result

    def _finalize_result(
        self,
        raw: _RawEntailmentResult,
        *,
        claim_id: str,
        evidence_id: str,
        semantic_score: float | None,
        reranker_score: float | None,
    ) -> EntailmentResult:
        # Parse relation — invalid LLM output → EVALUATION_ERROR, not UNKNOWN
        try:
            relation = EntailmentRelation(raw.relation)
        except ValueError:
            return self._build_error_result(
                claim_id,
                evidence_id,
                semantic_score,
                reranker_score,
                error_type="invalid_relation_value",
                reason="Evaluator returned an unsupported relation",
            )

        entailment_score = raw.entailment_score
        if entailment_score == 0.0:
            entailment_score = _RELATION_BASE_SCORE.get(relation, 0.0)

        # Parse evidence_type and experience_level with fallback
        try:
            evidence_type = EvidenceType(raw.evidence_type)
        except ValueError:
            evidence_type = EvidenceType.NONE
        try:
            experience_level = ClaimExperienceLevel(raw.experience_level)
        except ValueError:
            experience_level = ClaimExperienceLevel.NONE

        coverage = raw.coverage
        # If evaluator didn't provide coverage, derive from relation + entailment_score
        if coverage == 0.0 and relation in (
            EntailmentRelation.ENTAILED,
            EntailmentRelation.PARTIAL,
        ):
            coverage = (
                entailment_score
                if entailment_score > 0
                else _RELATION_BASE_SCORE.get(relation, 0.0)
            )

        strength, category = compute_evidence_strength(
            relation,
            semantic_score,
            reranker_score,
            entailment_score,
            coverage=coverage,
            evidence_type=evidence_type,
            experience_level=experience_level,
        )

        return EntailmentResult(
            claim_id=claim_id,
            evidence_id=evidence_id,
            relation=relation,
            confidence=raw.confidence,
            reason=raw.reason,
            semantic_score=semantic_score,
            reranker_score=reranker_score,
            entailment_score=round(entailment_score, 4),
            evidence_strength=strength,
            evidence_strength_category=category,
            coverage=round(coverage, 4),
            evidence_type=evidence_type,
            experience_level=experience_level,
        )

    # ── Batched path ────────────────────────────────────────────

    async def _enqueue(
        self,
        request: _EntailmentRequest,
        cache_key: str | None,
    ) -> EntailmentResult:
        # Defensive: after close() nothing may enqueue; fall back to the single path.
        if self._closed:
            return await self._evaluate_single_uncached(request)
        if self._queue is None or self._flusher_task is None:
            self._queue = asyncio.Queue()
            self._flusher_task = asyncio.create_task(self._flush_loop())
        future: asyncio.Future[EntailmentResult] = asyncio.get_running_loop().create_future()
        await self._queue.put(
            _PendingEntailment(request=request, cache_key=cache_key, future=future)
        )
        return await future

    async def _flush_loop(self) -> None:
        """Drain the pending queue in batches of at most batch_size pairs.

        The queue is drained greedily: as soon as one item arrives it is processed
        together with everything else already queued (up to batch_size), so sparse
        traffic is not artificially delayed waiting for a full batch.
        """
        assert self._queue is not None
        while True:
            item = await self._queue.get()
            if item is None:
                return
            drain: list[_PendingEntailment] = [item]
            while len(drain) < self._entailment_batch_size:
                try:
                    candidate = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if candidate is not None:
                    drain.append(candidate)
            for sub_batch in self._pack_by_budget(drain):
                await self._process_batch(sub_batch)

    def _pack_by_budget(
        self,
        items: list[_PendingEntailment],
    ) -> list[list[_PendingEntailment]]:
        """Split a drained batch so every sub-batch fits the model context.

        The context window (num_ctx) is shared between the input prompt and the
        generated output, and Ollama truncates input that overflows it. Packing
        keeps batches within the budget instead of silently truncating pairs.
        """
        batches: list[list[_PendingEntailment]] = []
        current: list[_PendingEntailment] = []
        input_estimate = 0
        for item in items:
            item_tokens = self._estimate_request_tokens(item.request)
            if current and (
                input_estimate
                + item_tokens
                + _ESTIMATED_OUTPUT_TOKENS_PER_ITEM * (len(current) + 1)
                > self._entailment_context_size
            ):
                batches.append(current)
                current = []
                input_estimate = 0
            current.append(item)
            input_estimate += item_tokens
        if current:
            batches.append(current)
        return batches

    def _estimate_request_tokens(self, request: _EntailmentRequest) -> int:
        claim_context = self._claim_context_for(request)
        return (
            self._estimate_tokens(claim_context)
            + self._estimate_tokens(request.evidence_text)
            + 40  # separators, ids, template framing
        )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text) // _ESTIMATED_CHARS_PER_TOKEN)

    def _render_batch_items(
        self,
        pending: list[_PendingEntailment],
    ) -> tuple[str, int]:
        blocks: list[str] = []
        input_estimate = 0
        for idx, item in enumerate(pending, start=1):
            request = item.request
            claim_context = self._claim_context_for(request)
            blocks.append(
                f"Item {idx} (claim_id: {request.claim_id}):\n"
                f"{claim_context}\n"
                f"Evidence (ID: {request.evidence_id}):\n"
                f"{request.evidence_text}"
            )
            input_estimate += (
                self._estimate_tokens(claim_context)
                + self._estimate_tokens(request.evidence_text)
                + 40
            )
        return "\n---\n".join(blocks), input_estimate

    def _batch_output_tokens(self, batch_len: int, input_tokens: int) -> int:
        """Cap the batch output budget so input + output fit the context window."""
        budget = max(64, self._entailment_context_size - input_tokens - 64)
        return min(self._entailment_max_tokens * batch_len, budget)

    async def _process_batch(
        self,
        pending: list[_PendingEntailment],
    ) -> None:
        if not pending:
            return
        # Single-pair batches reuse the exact single-pair path so that the batch
        # mode never degrades the common "last remaining pair" case.
        if len(pending) == 1:
            result = await self._evaluate_single_uncached(pending[0].request)
            await self._resolve_pending([(pending[0], result)])
            return

        items_block, input_estimate = self._render_batch_items(pending)
        try:
            raw_batch = await self._router.route(
                ModelRequest(
                    task_name="evaluate_evidence_entailment_batch",
                    task_class=ModelTaskClass.LOW_COST,
                    prompt=self._prompt_registry.render(
                        _PROMPT_NAME_ENTAIL_BATCH,
                        {"items": items_block},
                    ),
                    max_cost_usd=0.05,
                    timeout_seconds=min(600.0, self._timeout_seconds * len(pending)),
                    max_output_tokens=self._batch_output_tokens(
                        len(pending),
                        input_estimate,
                    ),
                    context_size=self._entailment_context_size,
                    model_override=self._entailment_model,
                ),
                _RawBatchEntailmentResult,
            )
        except Exception as error:
            error_type = type(error).__name__
            await self._resolve_pending(
                [
                    (
                        item,
                        self._build_error_result(
                            item.request.claim_id,
                            item.request.evidence_id,
                            item.request.semantic_score,
                            item.request.reranker_score,
                            error_type=error_type,
                            reason=f"Evaluator batch technical failure ({error_type})",
                        ),
                    )
                    for item in pending
                ]
            )
            return
        self._requests_made += 1

        matched = self._match_raws_to_items(pending, raw_batch.evaluations)
        resolved: list[tuple[_PendingEntailment, EntailmentResult]] = []
        for item, raw in zip(pending, matched, strict=True):
            if raw is None:
                resolved.append(
                    (
                        item,
                        self._build_error_result(
                            item.request.claim_id,
                            item.request.evidence_id,
                            item.request.semantic_score,
                            item.request.reranker_score,
                            error_type="batch_missing_item",
                            reason="Evaluator returned fewer evaluations than requested",
                        ),
                    )
                )
                continue
            resolved.append(
                (
                    item,
                    self._finalize_result(
                        raw,
                        claim_id=item.request.claim_id,
                        evidence_id=item.request.evidence_id,
                        semantic_score=item.request.semantic_score,
                        reranker_score=item.request.reranker_score,
                    ),
                )
            )
        await self._resolve_pending(resolved)

    @staticmethod
    def _match_raws_to_items(
        pending: list[_PendingEntailment],
        raws: list[_RawEntailmentResult],
    ) -> list[_RawEntailmentResult | None]:
        """Align model output to requests by claim_id, falling back to order.

        Prefers exact claim_id matching (the batch prompt asks the model to echo
        each item's claim_id); when ids are missing or duplicated, the remaining
        raw items are consumed in order. Unresolved requests map to None.
        """
        by_claim_id: dict[str, int] = {}
        for idx, raw in enumerate(raws):
            if raw.claim_id and raw.claim_id not in by_claim_id:
                by_claim_id[raw.claim_id] = idx
        used = [False] * len(raws)
        next_unused = 0
        matched: list[_RawEntailmentResult | None] = []
        for item in pending:
            match_idx = by_claim_id.get(item.request.claim_id)
            if match_idx is None or used[match_idx]:
                while next_unused < len(raws) and used[next_unused]:
                    next_unused += 1
                match_idx = next_unused if next_unused < len(raws) else None
                if match_idx is not None:
                    next_unused += 1
            if match_idx is not None and not used[match_idx]:
                used[match_idx] = True
                matched.append(raws[match_idx])
            else:
                matched.append(None)
        return matched

    async def _resolve_pending(
        self,
        resolved: list[tuple[_PendingEntailment, EntailmentResult]],
    ) -> None:
        """Resolve pending futures and persist per-pair cache entries."""
        for item, result in resolved:
            if not item.future.done():
                item.future.set_result(result)
        if self._cache is not None:
            for item, result in resolved:
                if item.cache_key is not None:
                    await self._cache_set(item.cache_key, result)

    async def close(self) -> None:
        """Flush the pending queue and stop the background flusher.

        The claim pipeline calls this after all requirements are matched. Safe to
        call multiple times and a no-op when batching is disabled or nothing was
        ever enqueued.
        """
        if self._closed:
            return
        self._closed = True
        if self._queue is None or self._flusher_task is None:
            return
        await self._queue.put(None)
        try:
            await self._flusher_task
        except asyncio.CancelledError:
            pass
        finally:
            self._flusher_task = None

    @staticmethod
    def _build_error_result(
        claim_id: str,
        evidence_id: str,
        semantic_score: float | None,
        reranker_score: float | None,
        *,
        error_type: str,
        reason: str,
    ) -> EntailmentResult:
        """Build an EntailmentResult for a technical evaluation failure."""
        strength, category = compute_evidence_strength(
            EntailmentRelation.EVALUATION_ERROR,
            semantic_score,
            reranker_score,
            0.0,
        )
        return EntailmentResult(
            claim_id=claim_id,
            evidence_id=evidence_id,
            relation=EntailmentRelation.EVALUATION_ERROR,
            confidence=0.0,
            reason=reason,
            semantic_score=semantic_score,
            reranker_score=reranker_score,
            entailment_score=0.0,
            evidence_strength=strength,
            evidence_strength_category=category,
            coverage=0.0,
            evidence_type=EvidenceType.NONE,
            experience_level=ClaimExperienceLevel.NONE,
            error_type=error_type,
        )


@dataclass
class _EntailmentRequest:
    """Typed entailment evaluation request (one claim-evidence pair)."""

    claim_id: str
    claim_text: str
    claim_type: str
    evidence_id: str
    evidence_text: str
    semantic_score: float | None = None
    reranker_score: float | None = None
    claim_subject: str | None = None
    claim_criticality: str | None = None
    source_requirement: str | None = None


@dataclass
class _PendingEntailment:
    """Queue entry awaiting a batched LLM evaluation."""

    request: _EntailmentRequest
    cache_key: str | None
    future: asyncio.Future[EntailmentResult]


class _RawEntailmentResult(StrictExtractionModel):
    """Internal schema for LLM entailment evaluation output."""

    # Echoed by the model only in batch mode; used to align results to requests.
    claim_id: str = ""
    relation: str
    confidence: float = 0.5
    reason: str = ""
    entailment_score: float = 0.0
    coverage: float = 0.0
    evidence_type: str = "none"
    experience_level: str = "none"


class _RawBatchEntailmentResult(StrictExtractionModel):
    """Internal schema for batched entailment evaluation output."""

    evaluations: list[_RawEntailmentResult]
