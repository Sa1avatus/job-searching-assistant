from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path

import httpx
import redis.asyncio as aioredis
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.domain.resume_text import extract_resume_text
from app.llm.preferences import LlmPreferencePurpose, resolve_model_identity, resolve_preference
from app.llm.providers.anthropic import AnthropicMessagesProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.llm.router import ModelProvider, ModelRouter
from app.matching.cache import MatchingCache
from app.matching.http_models import HttpEmbeddingClient, HttpReranker, UnavailableReranker
from app.matching.indexing import EvidenceReindexService
from app.matching.model_evaluators import RouterEvidenceEvaluator, RouterRequirementDecomposer
from app.matching.model_extractors import (
    RouterCandidateEvidenceExtractor,
    RouterVacancyRequirementExtractor,
)
from app.matching.opensearch_index import OpenSearchEvidenceIndex
from app.matching.pipeline import MatchingPipeline
from app.matching.rag_client import create_rag_client
from app.matching.retrieval import (
    HybridRetriever,
    RagAugmentedRetriever,
    SqlEvidenceRepository,
)
from app.matching.scoring import DeterministicMatchScorer
from app.matching.semantic import Reranker
from app.matching.vacancy_source import build_vacancy_matching_source
from app.prompts.registry import PromptRegistry
from app.storage.tables import ApplicationRow, CvFileRow, VacancyRow


class MatchingRuntime:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        redis_client: aioredis.Redis | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._redis_client = redis_client

    async def run(self, application_id: str) -> None:
        import structlog

        logger = structlog.get_logger(__name__)
        logger.info("matching_run_started", application_id=application_id)
        with self._session_factory() as session:
            application = session.get(ApplicationRow, application_id)
            if application is None:
                raise LookupError("Application not found")
            if application.selected_cv_file_id is None:
                raise ValueError("Application has no selected CV")
            cv_file = session.get(CvFileRow, application.selected_cv_file_id)
            vacancy = session.get(VacancyRow, application.vacancy_id)
            if cv_file is None or vacancy is None:
                raise LookupError("Matching source entity is unavailable")
            cv_path = Path(cv_file.storage_path)
            cv_source_text = extract_resume_text(
                cv_path.read_bytes(),
                extension=Path(cv_file.original_filename).suffix,
            )

            timeout = httpx.Timeout(self._settings.matching_model_timeout_seconds)
            encryption_key = (
                self._settings.browser_state_encryption_key.get_secret_value()
                if self._settings.browser_state_encryption_key is not None
                else None
            )
            model_identity = resolve_model_identity(
                session, application.user_id, encryption_key, purpose=LlmPreferencePurpose.MATCHING
            )
            async with AsyncExitStack() as stack:
                llm_http = await stack.enter_async_context(
                    httpx.AsyncClient(
                        timeout=timeout,
                        follow_redirects=False,
                        trust_env=False,
                    )
                )
                model_http = await stack.enter_async_context(
                    httpx.AsyncClient(
                        base_url=self._settings.resolved_embedding_service_url,
                        timeout=timeout,
                        follow_redirects=False,
                        trust_env=False,
                    )
                )
                opensearch_http = await stack.enter_async_context(
                    httpx.AsyncClient(
                        base_url=self._settings.opensearch_url,
                        timeout=30,
                        follow_redirects=False,
                        trust_env=False,
                    )
                )
                reranker: Reranker
                if (
                    self._settings.reranker_service_url is not None
                    and self._settings.reranker_api_key is not None
                ):
                    reranker_http = await stack.enter_async_context(
                        httpx.AsyncClient(
                            base_url=self._settings.reranker_service_url,
                            timeout=timeout,
                            follow_redirects=False,
                            trust_env=False,
                        )
                    )
                    reranker = HttpReranker(
                        reranker_http,
                        api_key=self._settings.reranker_api_key,
                    )
                else:
                    reranker = UnavailableReranker(
                        "Reranker service URL and API key must both be configured"
                    )
                router = ModelRouter(
                    _build_user_model_providers(
                        llm_http,
                        session,
                        application.user_id,
                        self._settings,
                    )
                )
                prompt_registry = PromptRegistry.load(
                    Path(__file__).parents[2] / "prompts" / "registry.json"
                )
                embedding_client = HttpEmbeddingClient(
                    model_http,
                    dimensions=self._settings.embedding_dimensions,
                )
                search_index = OpenSearchEvidenceIndex(
                    opensearch_http,
                    index_prefix=self._settings.opensearch_evidence_index_prefix,
                    read_alias=self._settings.opensearch_evidence_read_alias,
                    write_alias=self._settings.opensearch_evidence_write_alias,
                    dimensions=self._settings.embedding_dimensions,
                )
                await search_index.ensure_index()
                # Create claim decomposer and evidence evaluator
                matching_cache = MatchingCache(
                    redis=self._redis_client,
                    ttl_seconds=self._settings.matching_cache_ttl_seconds,
                )
                claim_decomposer = RouterRequirementDecomposer(
                    router,
                    prompt_registry,
                    cache=matching_cache,
                    timeout_seconds=self._settings.matching_model_timeout_seconds,
                    model_identity=model_identity,
                    decompose_max_tokens=self._settings.matching_decompose_max_tokens,
                    decompose_context_size=self._settings.matching_decompose_context_size,
                )
                evidence_evaluator = RouterEvidenceEvaluator(
                    router,
                    prompt_registry,
                    cache=matching_cache,
                    timeout_seconds=self._settings.matching_model_timeout_seconds,
                    model_identity=model_identity,
                    entailment_max_tokens=self._settings.matching_entailment_max_tokens,
                    entailment_context_size=self._settings.matching_entailment_context_size,
                    entailment_batch_size=self._settings.matching_entailment_batch_size,
                    entailment_model=self._settings.matching_entailment_model or None,
                )
                rag_client = create_rag_client(
                    service_url=self._settings.rag_service_url,
                    api_key=(
                        self._settings.rag_api_key.get_secret_value()
                        if self._settings.rag_api_key is not None
                        else None
                    ),
                    project_id=self._settings.rag_project_id,
                    collection="vacancies",
                    timeout_seconds=self._settings.rag_timeout_seconds,
                    enabled=self._settings.rag_enabled,
                )
                local_retriever = HybridRetriever(
                    search_index,
                    embedding_client,
                    SqlEvidenceRepository(session),
                    retrieval_limit=self._settings.matching_retrieval_top_k,
                )
                retriever = (
                    RagAugmentedRetriever(
                        local_retriever,
                        rag_client,
                        retrieval_limit=self._settings.matching_retrieval_top_k,
                    )
                    if self._settings.rag_enabled
                    else local_retriever
                )
                pipeline = MatchingPipeline(
                    session,
                    RouterVacancyRequirementExtractor(
                        router,
                        prompt_registry,
                        timeout_seconds=self._settings.matching_model_timeout_seconds,
                        context_size=self._settings.matching_extraction_context_size,
                        max_output_tokens=self._settings.matching_extraction_max_tokens,
                        model_identity=model_identity,
                    ),
                    RouterCandidateEvidenceExtractor(
                        router,
                        prompt_registry,
                        timeout_seconds=self._settings.matching_model_timeout_seconds,
                        context_size=self._settings.matching_extraction_context_size,
                        max_output_tokens=self._settings.matching_extraction_max_tokens,
                        model_identity=model_identity,
                    ),
                    retriever,
                    reranker,
                    DeterministicMatchScorer(),
                    evidence_indexer=EvidenceReindexService(
                        session,
                        embedding_client,
                        search_index,
                    ),
                    rag_client=rag_client,
                    claim_decomposer=claim_decomposer,
                    evidence_evaluator=evidence_evaluator,
                    retrieval_top_k=self._settings.matching_retrieval_top_k,
                    reranker_top_k=self._settings.matching_reranker_top_k,
                    shadow_mode=self._settings.matching_v2_shadow_mode,
                    fallback_enabled=self._settings.matching_v2_fallback_enabled,
                    llm_concurrency=self._settings.matching_llm_concurrency,
                    entailment_max_candidates=self._settings.matching_entailment_max_candidates,
                )
                await pipeline.match(
                    application.id,
                    vacancy_source_text=build_vacancy_matching_source(vacancy),
                    cv_source_text=cv_source_text,
                )


def _build_user_model_providers(
    http_client: httpx.AsyncClient,
    session: Session,
    user_id: str,
    settings: Settings,
    purpose: str | LlmPreferencePurpose = LlmPreferencePurpose.MATCHING,
) -> tuple[ModelProvider, ...]:
    encryption_key = (
        settings.browser_state_encryption_key.get_secret_value()
        if settings.browser_state_encryption_key is not None
        else None
    )
    if encryption_key is not None:
        preference = resolve_preference(session, user_id, encryption_key, purpose)
        if preference is not None:
            if preference.provider == "anthropic":
                return (
                    AnthropicMessagesProvider(
                        http_client,
                        api_key=preference.api_key,
                        model=preference.model,
                    ),
                )
            if preference.provider == "gemini":
                return (
                    GeminiProvider(
                        http_client,
                        api_key=preference.api_key,
                        model=preference.model,
                    ),
                )
            if preference.base_url is None:
                raise ValueError("OpenAI-compatible base URL is required")
            return (
                OpenAICompatibleProvider(
                    http_client,
                    api_key=preference.api_key,
                    model=preference.model,
                    base_url=preference.base_url,
                ),
            )
    providers: list[ModelProvider] = []
    if settings.anthropic_api_key is not None:
        providers.append(
            AnthropicMessagesProvider(
                http_client,
                api_key=settings.anthropic_api_key.get_secret_value(),
                model=settings.anthropic_model,
            )
        )
    if settings.gemini_api_key is not None:
        providers.append(
            GeminiProvider(
                http_client,
                api_key=settings.gemini_api_key.get_secret_value(),
                model=settings.gemini_model,
            )
        )
    return tuple(providers)
