from __future__ import annotations

from app.llm.router import ModelRequest, ModelRouter, ModelTaskClass
from app.matching.extraction import (
    CandidateEvidenceExtraction,
    VacancyExtraction,
)
from app.prompts.registry import PromptRegistry

_MAX_SOURCE_CHARACTERS = 30_000


class UngroundedExtractionError(ValueError):
    pass


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

    The historical exact-substring check rejected valid extractions from weaker local
    models that slightly paraphrase fragments (function-word changes, punctuation,
    minor reordering). Grounding is still enforced: a fragment is accepted only when
    all of its significant words exist in the source, so the model cannot introduce
    skills or facts the source never mentions.
    """
    source_tokens = set(_normalized_source(source_text).split())
    fragment_tokens = _normalized_source(source_fragment).split()
    content_tokens = [token for token in fragment_tokens if token not in _FUNCTION_WORDS]
    return bool(content_tokens) and all(token in source_tokens for token in content_tokens)


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
        prompt = self._prompt_registry.render(
            "extract_vacancy_requirements",
            {
                "vacancy_id": vacancy_id,
                "source_text": source_text[:_MAX_SOURCE_CHARACTERS],
            },
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
        ungrounded_fragments = [
            requirement.source_fragment
            for requirement in extraction.requirements
            if not _is_grounded(source_text, requirement.source_fragment)
        ]
        if ungrounded_fragments:
            raise UngroundedExtractionError(
                f"Vacancy extraction returned {len(ungrounded_fragments)} ungrounded fragments"
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
        ungrounded_fragments = [
            evidence.source_fragment
            for evidence in extraction.evidence
            if not _is_grounded(source_text, evidence.source_fragment)
        ]
        if ungrounded_fragments:
            raise UngroundedExtractionError(
                f"Candidate extraction returned {len(ungrounded_fragments)} ungrounded fragments"
            )
        return extraction.model_copy(
            update={
                "evidence": [
                    evidence.model_copy(update={"is_verified": source_is_verified})
                    for evidence in extraction.evidence
                ]
            }
        )
