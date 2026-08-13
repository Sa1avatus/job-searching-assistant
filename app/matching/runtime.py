from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.domain.resume_text import extract_resume_text
from app.llm.preferences import LlmPreferenceService
from app.llm.providers.anthropic import AnthropicMessagesProvider
from app.llm.providers.gemini import GeminiProvider
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
from app.matching.retrieval import HybridRetriever, SqlEvidenceRepository
from app.matching.scoring import DeterministicMatchScorer
from app.matching.semantic import Reranker
from app.prompts.registry import PromptRegistry
from app.storage.tables import ApplicationRow, CvFileRow, VacancyRow


class MatchingRuntime:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

    async def run(self, application_id: str) -> None:
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
            async with AsyncExitStack() as stack:
                llm_http = await stack.enter_async_context(
                    httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False)
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
                matching_cache = MatchingCache()
                claim_decomposer = RouterRequirementDecomposer(
                    router, prompt_registry, cache=matching_cache
                )
                evidence_evaluator = RouterEvidenceEvaluator(
                    router, prompt_registry, cache=matching_cache
                )
                pipeline = MatchingPipeline(
                    session,
                    RouterVacancyRequirementExtractor(router, prompt_registry),
                    RouterCandidateEvidenceExtractor(router, prompt_registry),
                    HybridRetriever(
                        search_index,
                        embedding_client,
                        SqlEvidenceRepository(session),
                    ),
                    reranker,
                    DeterministicMatchScorer(),
                    evidence_indexer=EvidenceReindexService(
                        session,
                        embedding_client,
                        search_index,
                    ),
                    claim_decomposer=claim_decomposer,
                    evidence_evaluator=evidence_evaluator,
                    retrieval_top_k=self._settings.matching_retrieval_top_k,
                    reranker_top_k=self._settings.matching_reranker_top_k,
                    shadow_mode=self._settings.matching_v2_shadow_mode,
                    fallback_enabled=self._settings.matching_v2_fallback_enabled,
                )
                await pipeline.match(
                    application.id,
                    vacancy_source_text=vacancy.description_text,
                    cv_source_text=cv_source_text,
                )


def _build_user_model_providers(
    http_client: httpx.AsyncClient,
    session: Session,
    user_id: str,
    settings: Settings,
) -> tuple[ModelProvider, ...]:
    encryption_key = (
        settings.browser_state_encryption_key.get_secret_value()
        if settings.browser_state_encryption_key is not None
        else None
    )
    if encryption_key is not None:
        preference = LlmPreferenceService(session, encryption_key=encryption_key).load(user_id)
        if preference is not None:
            if preference.provider == "anthropic":
                return (
                    AnthropicMessagesProvider(
                        http_client,
                        api_key=preference.api_key,
                        model=preference.model,
                    ),
                )
            return (
                GeminiProvider(
                    http_client,
                    api_key=preference.api_key,
                    model=preference.model,
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
