from __future__ import annotations

import structlog

from app.llm.router import ModelRequest, ModelRouter, ModelTaskClass
from app.matching.extraction import (
    CandidateEvidenceExtraction,
    VacancyExtraction,
)
from app.prompts.registry import PromptRegistry

logger = structlog.get_logger(__name__)

_MAX_SOURCE_CHARACTERS = 30_000


class UngroundedExtractionError(ValueError):
    pass


def _stem(word: str) -> str:
    """Minimal English stemmer for grounding tolerance.

    Handles common morphological variations: plurals (-s, -es, -ies),
    gerunds (-ing), past tense (-ed), comparatives (-er), nominalizations
    (-tion, -ment, -ness).  Not a full stemmer — just enough to prevent
    false rejections from minor LLM paraphrasing.
    """
    if len(word) <= 3:
        return word
    # -ies → -y (cities → city)
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    # -es → drop (processes → process, but not 'ares')
    if word.endswith("es") and len(word) > 4 and word[-3] not in "aeiou":
        return word[:-2]
    # -s → drop (agents → agent)
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    # -ing → drop (running → runn)
    if word.endswith("ing") and len(word) > 5:
        return word[:-3]
    # -ed → drop (tested → test)
    if word.endswith("ed") and len(word) > 4:
        return word[:-2]
    # -tion → (production → produc)
    if word.endswith("tion") and len(word) > 5:
        return word[:-4]
    # -ment → (deployment → deploy)
    if word.endswith("ment") and len(word) > 5:
        return word[:-4]
    # -ness → (awareness → aware)
    if word.endswith("ness") and len(word) > 5:
        return word[:-4]
    return word


def _normalized_source(source_text: str) -> str:
    return " ".join(source_text.casefold().split())


# Function words the model may freely add, drop, or reorder without breaking source
# grounding. Content words (nouns, verbs, adjectives, numbers) must still appear in
# the source text itself.
_FUNCTION_WORDS = frozenset(
    {
        # English
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
        "we",
        "you",
        # Russian
        "а",
        "в",
        "во",
        "для",
        "до",
        "же",
        "за",
        "и",
        "из",
        "или",
        "к",
        "ко",
        "на",
        "не",
        "но",
        "о",
        "об",
        "от",
        "по",
        "при",
        "с",
        "со",
        "у",
    }
)


def _is_grounded(source_text: str, source_fragment: str) -> bool:
    """Check that every content word of the fragment appears in the source text.

    Uses stemming to tolerate minor morphological variations (plurals,
    gerunds, past tense) that LLMs produce when paraphrasing fragments.
    Content words (nouns, verbs, adjectives, numbers) must still have
    a stem match in the source text.
    """
    source_stems = {_stem(token) for token in _normalized_source(source_text).split()}
    fragment_tokens = _normalized_source(source_fragment).split()
    content_tokens = [_stem(token) for token in fragment_tokens if token not in _FUNCTION_WORDS]
    return bool(content_tokens) and all(token in source_stems for token in content_tokens)


class RouterVacancyRequirementExtractor:
    model_name = "model-router"
    model_version = "provider-selected"
    schema_version = "1"

    def __init__(
        self,
        router: ModelRouter,
        prompt_registry: PromptRegistry,
        *,
        timeout_seconds: float = 60,
        context_size: int = 4096,
        max_output_tokens: int = 4096,
        model_identity: str | None = None,
    ) -> None:
        self._router = router
        self._prompt_registry = prompt_registry
        self._timeout_seconds = timeout_seconds
        self._context_size = context_size
        self._max_output_tokens = max_output_tokens
        # Instance attribute so extraction_run_id becomes model-aware: switching the
        # resolved LLM model invalidates cached extraction rows instead of reusing them.
        self.model_name = model_identity or self.model_name

    async def extract(self, *, vacancy_id: str, source_text: str) -> VacancyExtraction:
        truncated_text = source_text[:_MAX_SOURCE_CHARACTERS]
        prompt = self._prompt_registry.render(
            "extract_vacancy_requirements",
            {
                "vacancy_id": vacancy_id,
                "source_text": truncated_text,
            },
        )
        logger.debug(
            "extraction_request",
            vacancy_id=vacancy_id,
            source_text_len=len(source_text),
            source_text_preview=source_text[:500],
        )
        extraction = await self._router.route(
            ModelRequest(
                task_name="extract_vacancy_requirements",
                task_class=ModelTaskClass.LOW_COST,
                prompt=prompt,
                max_cost_usd=0.05,
                timeout_seconds=self._timeout_seconds,
                context_size=self._context_size,
                max_output_tokens=self._max_output_tokens,
            ),
            VacancyExtraction,
        )
        logger.debug(
            "extraction_response",
            vacancy_id=vacancy_id,
            requirement_count=len(extraction.requirements),
            requirements=[
                {
                    "text": r.text[:200],
                    "type": r.requirement_type,
                    "importance": r.importance,
                    "fragment": r.source_fragment[:200],
                }
                for r in extraction.requirements[:10]
            ],
        )
        ungrounded = [
            req
            for req in extraction.requirements
            if not _is_grounded(source_text, req.source_fragment)
        ]
        if ungrounded:
            logger.warning(
                "extraction_ungrounded_fragments_filtered",
                vacancy_id=vacancy_id,
                ungrounded_count=len(ungrounded),
                total_count=len(extraction.requirements),
                ungrounded_fragments=[r.source_fragment[:200] for r in ungrounded[:5]],
            )
            grounded_requirements = [
                req
                for req in extraction.requirements
                if _is_grounded(source_text, req.source_fragment)
            ]
            extraction = extraction.model_copy(update={"requirements": grounded_requirements})
        if not extraction.requirements:
            raise UngroundedExtractionError(
                f"All {len(ungrounded)} extracted requirements were ungrounded"
            )
        return extraction


class RouterCandidateEvidenceExtractor:
    model_name = "model-router"
    model_version = "provider-selected"
    schema_version = "1"

    def __init__(
        self,
        router: ModelRouter,
        prompt_registry: PromptRegistry,
        *,
        timeout_seconds: float = 60,
        context_size: int = 4096,
        max_output_tokens: int = 4096,
        model_identity: str | None = None,
    ) -> None:
        self._router = router
        self._prompt_registry = prompt_registry
        self._timeout_seconds = timeout_seconds
        self._context_size = context_size
        self._max_output_tokens = max_output_tokens
        # Instance attribute so extraction_run_id becomes model-aware (see above).
        self.model_name = model_identity or self.model_name

    async def extract(
        self,
        *,
        user_id: str,
        cv_file_id: str,
        source_text: str,
        source_is_verified: bool = False,
    ) -> CandidateEvidenceExtraction:
        prompt = self._prompt_registry.render(
            "extract_candidate_evidence",
            {
                "user_id": user_id,
                "cv_file_id": cv_file_id,
                "source_text": source_text[:_MAX_SOURCE_CHARACTERS],
            },
        )
        logger.debug(
            "candidate_evidence_extraction_request",
            user_id=user_id,
            cv_file_id=cv_file_id,
            source_text_len=len(source_text),
            source_text_preview=source_text[:500],
        )
        extraction = await self._router.route(
            ModelRequest(
                task_name="extract_candidate_evidence",
                task_class=ModelTaskClass.LOW_COST,
                prompt=prompt,
                max_cost_usd=0.05,
                timeout_seconds=self._timeout_seconds,
                context_size=self._context_size,
                max_output_tokens=self._max_output_tokens,
            ),
            CandidateEvidenceExtraction,
        )
        logger.debug(
            "candidate_evidence_extraction_response",
            user_id=user_id,
            cv_file_id=cv_file_id,
            evidence_count=len(extraction.evidence),
            evidence_preview=[
                {
                    "skill": e.skill_name,
                    "type": e.evidence_type,
                    "fragment": e.source_fragment[:200],
                }
                for e in extraction.evidence[:10]
            ],
        )
        ungrounded = [
            ev for ev in extraction.evidence if not _is_grounded(source_text, ev.source_fragment)
        ]
        if ungrounded:
            logger.warning(
                "candidate_extraction_ungrounded_filtered",
                user_id=user_id,
                cv_file_id=cv_file_id,
                ungrounded_count=len(ungrounded),
                total_count=len(extraction.evidence),
                ungrounded_fragments=[ev.source_fragment[:200] for ev in ungrounded[:5]],
            )
            grounded_evidence = [
                ev for ev in extraction.evidence if _is_grounded(source_text, ev.source_fragment)
            ]
            extraction = extraction.model_copy(update={"evidence": grounded_evidence})
        if not extraction.evidence:
            raise UngroundedExtractionError(
                f"All {len(ungrounded)} extracted evidence items were ungrounded"
            )
        return extraction.model_copy(
            update={
                "evidence": [
                    evidence.model_copy(update={"is_verified": source_is_verified})
                    for evidence in extraction.evidence
                ]
            }
        )
