import asyncio
import json
import secrets
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import urlsplit

# Playwright launches the browser as a subprocess. On Windows, asyncio's default
# SelectorEventLoop (which uvicorn ends up using in some run configurations, notably
# `--reload`) does not implement subprocess support at all and raises NotImplementedError from
# deep inside asyncio the first time a browser-based route runs. ProactorEventLoop does support
# it and has been the Windows default policy since Python 3.8, but something further up this
# process's stack can still end up creating a Selector-backed loop before this module is
# imported. Setting the policy here is a best-effort second guard; if you still hit
# NotImplementedError from `_make_subprocess_transport`, use `python scripts/run_server.py`
# instead of `python -m uvicorn ...` — that script sets this before uvicorn ever creates a loop,
# which is the version of this fix that is guaranteed to still be in effect.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import httpx
import redis.asyncio as redis
import structlog
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from adapters.job_boards.browser_apply_common import ApplyBlocked, CaptchaChallenge
from adapters.job_boards.contracts import ADAPTER_CAPABILITIES
from adapters.job_boards.greenhouse_api import GreenhouseJobBoardApi
from adapters.job_boards.linkedin_reference import LinkedInJobReference
from app.api.schemas import (
    ActiveCvFileRequest,
    ApplicationMatchDetailsResponse,
    ApplicationMaterialsResponse,
    ApplicationMaterialsUpdateRequest,
    ApplicationResponse,
    ApplicationStatusUpdateRequest,
    AssessmentRequest,
    AssessmentResponse,
    AutofillValueCreateRequest,
    AutofillValueResponse,
    AutofillValueUpdateRequest,
    BrowserApplySubmitRequest,
    BrowserAuthorizationResponse,
    BrowserHandoffRequest,
    BrowserHandoffResponse,
    BrowserReviewRequest,
    BrowserSessionStatusResponse,
    ClearMatchingQueueResponse,
    CompanyBlacklistRequest,
    CompanyBlacklistResponse,
    ConfirmedProfileFactResponse,
    ConfirmProfileFactsRequest,
    ConfirmResumeProfileRequest,
    ConnectorCapabilityResponse,
    CvFileResponse,
    DiscoverGreenhouseVacanciesRequest,
    DiscoverHeadHunterVacanciesRequest,
    DiscoverLinkedInVacanciesRequest,
    DiscoverVacanciesStreamRequest,
    DiscoveryOutcomeResponse,
    EffectiveValueResponse,
    EmailIntegrationResponse,
    EmailIntegrationUpdateRequest,
    EmailReviewCandidateResponse,
    EmailReviewItemResponse,
    EmailReviewResolveRequest,
    EmploymentTypeName,
    EvidenceArtifactResponse,
    ExtractedProfileResponse,
    ExtractFromResumeRequest,
    FactImportBatchResponse,
    FactImportResultResponse,
    GreenhouseImportRequest,
    HeadHunterImportRequest,
    HealthResponse,
    HumanActionCheckpointResponse,
    HumanActionResumeRequest,
    ImportedVacancyResponse,
    LinkedInReferenceImportRequest,
    LlmModelsRequest,
    LlmModelsResponse,
    LlmPreferenceResponse,
    LlmPreferenceUpdateRequest,
    MatchingQueuePauseResponse,
    MatchingQueueResponse,
    MatchingQueueTaskResponse,
    PrepareApplicationRequest,
    ProfileFactDetailResponse,
    ProfileFactRequest,
    ProfileFactResponse,
    RequirementMatchDetailResponse,
    RerankerStatusResponse,
    ResumeRagSyncResponse,
    ReviewDecisionRequest,
    ReviewItemResponse,
    SavedVacancyPageResponse,
    SavedVacancyResponse,
    ScreeningAnswerResponse,
    SiteDefinitionCreateRequest,
    SiteDefinitionResponse,
    SiteDefinitionUpdateRequest,
    SiteFieldDiscoveryRequest,
    SiteFieldMappingResponse,
    SiteFieldMappingUpdateRequest,
    SiteFieldResponse,
    SiteValueOverrideRequest,
    SiteValueOverrideResponse,
    TaskTransitionResponse,
    UserRequest,
    UserResponse,
    VacancyRequest,
    VacancyResponse,
    WorkflowTaskResponse,
    WorkFormat,
)
from app.api.statistics_schemas import (
    ApplicationEmailSyncResponse,
    ApplicationStatisticsResponse,
    ApplicationSyncResponse,
)
from app.browser.session_probe import probe_browser_session
from app.browser.session_store import InvalidBrowserState, delete_browser_state_file
from app.config import Settings, get_settings
from app.domain.autofill_keys import InvalidAutofillKey
from app.domain.autofill_sensitivity import evaluate_autofill_usage
from app.domain.effective_values import (
    EffectiveValueBlocked,
    EffectiveValueCandidate,
    EffectiveValueNotFound,
    apply_effective_value_transformation,
    resolve_effective_autofill_value,
)
from app.domain.forms import FormField, FormFieldType
from app.domain.models import ProfileFact, Vacancy
from app.domain.policy import SENSITIVE_CATEGORIES, assess_vacancy
from app.domain.resume_text import UnreadableResumeError, extract_resume_text
from app.domain.vacancy_attributes import EMPLOYMENT_TYPE_ORDER
from app.llm.preferences import (
    DEFAULT_LLM_PURPOSE,
    InvalidLlmPreference,
    LlmModelDiscoveryError,
    LlmPreferenceNotFound,
    LlmPreferencePurpose,
    LlmPreferenceService,
    fetch_available_models,
    resolve_preference,
)
from app.llm.providers.anthropic import AnthropicMessagesProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.llm.router import ModelProvider, ModelRouter, NoModelAvailableError
from app.matching.jobs import (
    BACKGROUND_MATCHING_PRIORITY,
    MATCHING_QUEUE_NAME,
    MatchingJobNotReadyError,
    MatchingJobService,
)
from app.matching.queue_admin import (
    clear_matching_queue,
    matching_queue_is_paused,
    set_matching_queue_paused,
)
from app.matching.rag_client import create_rag_client
from app.matching.rag_collections import VACANCY_COLLECTION
from app.observability.logging import configure_logging
from app.observability.metrics import metrics
from app.prompts.registry import PromptRegistry
from app.security.autofill_decryption import decrypt_autofill_value
from app.security.autofill_encryption import InvalidAutofillValueEncryption
from app.services.application_email_events import ApplicationEmailEventService
from app.services.application_email_sync import (
    ApplicationEmailProvider,
    ApplicationEmailSyncService,
)
from app.services.application_sync import ApplicationStatusSyncService, ApplicationSubmissionProbe
from app.services.autofill_value_delete import delete_autofill_value
from app.services.autofill_value_list import list_autofill_values
from app.services.autofill_value_update import update_autofill_value
from app.services.autofill_values import InvalidAutofillValue, create_autofill_value
from app.services.browser_authorization import (
    KNOWN_AUTHORIZATION_SITES,
    BrowserAuthorizationSite,
)
from app.services.browser_handoff import create_browser_handoff
from app.services.browser_worker_client import BrowserWorkerClient
from app.services.company_blacklist import CompanyBlacklistService
from app.services.email_classification import EmailClassifier
from app.services.email_file_import import (
    MAX_EMAIL_IMPORT_BYTES,
    MAX_EMAIL_IMPORT_FILES,
    UploadedEmailFile,
    UploadedEmailImportProvider,
)
from app.services.email_integrations import EmailIntegrationService, InvalidEmailIntegration
from app.services.email_vacancy_matcher import EmailVacancyMatcher
from app.services.http_adapters import HttpHeadHunterAdapter, HttpLinkedInAdapter
from app.services.imap_email_provider import ImapApplicationEmailProvider
from app.services.job_discovery import (
    DiscoveryOutcome,
    GreenhouseDiscoveryError,
    JobDiscoveryService,
    LinkedInSessionRequiredError,
    NoSearchKeywordsError,
)
from app.services.materials_generation import (
    MaterialsGenerationService,
    MaterialsLanguageMismatchError,
    NoVerifiedFactsError,
    cover_letter_matches_vacancy_language,
    detect_vacancy_language,
)
from app.services.rag_sync import RagSyncService
from app.services.recruitment import (
    DuplicateEntityError,
    EntityNotFoundError,
    RecruitmentService,
)
from app.services.reranker_status import RerankerStatusProbe
from app.services.resume_intake import ResumeIntakeService
from app.services.resume_rag_jobs import (
    ResumeRagJobNotReadyError,
    ResumeRagJobService,
    ResumeRagJobStatus,
)
from app.services.site_definition_archive import archive_site_definition
from app.services.site_definition_update import (
    InvalidSiteDefinitionUpdate,
    update_site_definition,
)
from app.services.site_definitions import InvalidSiteDefinition, create_site_definition
from app.services.site_fields import (
    InvalidSiteField,
    save_discovered_site_fields,
    upsert_site_field_mapping,
    upsert_site_value_override,
)
from app.services.vacancy_catalog import VacancyCatalogService
from app.services.vacancy_metadata import (
    detect_work_format,
    extract_key_skills,
    summarize_vacancy,
)
from app.storage.database import SessionFactory, session_scope
from app.storage.documents import DocumentStorage, InvalidDocumentError
from app.storage.evidence_artifacts import EvidenceArtifactStorage, InvalidEvidenceArtifact
from app.storage.tables import (
    ApplicationEmailEventRow,
    ApplicationMatchResultRow,
    ApplicationRow,
    AutofillValueRow,
    BrowserSessionRow,
    CandidateEvidenceRow,
    CvFileRow,
    EmailIntegrationRow,
    LlmPreferenceRow,
    ProfileFactRow,
    RequirementMatchRow,
    SiteDefinitionRow,
    SiteFieldMappingRow,
    SiteFieldRow,
    SiteValueOverrideRow,
    UserRow,
    VacancyRequirementRow,
    VacancyRow,
    WorkerHeartbeatRow,
    WorkflowTaskRow,
)
from app.workers.browser_worker import create_session_store

configure_logging()
logger = structlog.get_logger(__name__)
app = FastAPI(title="Job Searching Assistant", version="1.4.1")
REVIEW_UI_PATH = Path(__file__).parents[1] / "static" / "review.html"
DASHBOARD_UI_PATH = Path(__file__).parents[1] / "static" / "dashboard.html"


def get_application_email_provider(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> ApplicationEmailProvider:
    if session.get(UserRow, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    encryption_key = _configured_secret(get_settings().browser_state_encryption_key)
    if encryption_key is None:
        raise HTTPException(status_code=503, detail="Encrypted storage is not configured")
    try:
        integration = EmailIntegrationService(
            session,
            encryption_key=encryption_key,
        ).load(user_id)
    except InvalidEmailIntegration as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if integration is None or not integration.enabled:
        raise HTTPException(status_code=503, detail="Email integration is not configured")
    return ImapApplicationEmailProvider(integration)


def serialize_discovery_outcomes(
    outcomes: list[DiscoveryOutcome],
) -> list[DiscoveryOutcomeResponse]:
    return [DiscoveryOutcomeResponse(**asdict(outcome)) for outcome in outcomes]


def serialize_discovery_outcome(outcome: DiscoveryOutcome) -> dict[str, object]:
    return DiscoveryOutcomeResponse(**asdict(outcome)).model_dump(mode="json")


def response_work_format(vacancy: VacancyRow) -> WorkFormat:
    if vacancy.work_format == "remote":
        return "remote"
    if vacancy.work_format == "hybrid":
        return "hybrid"
    if vacancy.work_format == "office":
        return "office"
    return detect_work_format(vacancy.title, vacancy.location, vacancy.description_text)


def response_employment_types(vacancy: VacancyRow) -> list[EmploymentTypeName]:
    stored_employment_types = set(vacancy.employment_types or ())
    return [
        employment_type
        for employment_type in EMPLOYMENT_TYPE_ORDER
        if employment_type in stored_employment_types
    ]


def serialize_cv_file(
    cv_file: CvFileRow,
    *,
    active_cv_file_id: str | None,
    rag_status: ResumeRagJobStatus | None = None,
) -> CvFileResponse:
    rag_status = rag_status or ResumeRagJobStatus(None, "not_scheduled", 0, None, None, None)
    return CvFileResponse(
        id=cv_file.id,
        user_id=cv_file.user_id,
        original_filename=cv_file.original_filename,
        content_type=cv_file.content_type,
        sha256=cv_file.sha256,
        size_bytes=cv_file.size_bytes,
        skills=cv_file.skills,
        experience_summary=cv_file.experience_summary,
        search_keywords=cv_file.search_keywords,
        years_of_experience=cv_file.years_of_experience,
        analyzed_at=cv_file.analyzed_at,
        is_active=cv_file.id == active_cv_file_id,
        rag_sync_status=rag_status.status,
        rag_sync_attempts=rag_status.attempt_number,
        rag_sync_failure_code=rag_status.failure_code,
        rag_synced_at=rag_status.synced_at,
    )


def resume_rag_status(session: Session, cv_file_id: str) -> ResumeRagJobStatus:
    settings = get_settings()
    if not (
        settings.rag_enabled
        and settings.rag_service_url
        and settings.rag_api_key
        and settings.rag_project_id
    ):
        return ResumeRagJobStatus(None, "not_configured", 0, None, None, None)
    return ResumeRagJobService(session).status(cv_file_id)


def required_mapping_string(data: dict[str, object], key: str, *, default: str = "") -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"Browser worker returned invalid {key}")
    return value


def required_mapping_string_list(data: dict[str, object], key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list | tuple) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Browser worker returned invalid {key}")
    return [item for item in value if isinstance(item, str)]


def required_api_scope(method: str, path: str) -> str:
    if path == "/v1/assessments":
        return "assessments:write"
    if path == "/v1/users":
        return "profiles:write"
    if path.startswith("/v1/users/"):
        return "profiles:delete" if method == "DELETE" else "profiles:write"
    if path.startswith("/v1/connectors"):
        return "connectors:read"
    if path.startswith("/v1/vacancies"):
        return "vacancies:write"
    if method == "GET" and path.startswith("/v1/evidence/"):
        return "review:read"
    if method == "GET" and path.startswith("/v1/workflow-tasks/"):
        return "review:read"
    if path == "/v1/applications/prepare":
        return "applications:write"
    if path.startswith("/v1/applications/") and (
        path.endswith("/apply-headhunter") or path.endswith("/apply-linkedin")
    ):
        return "applications:submit_real"
    if path.startswith("/v1/applications/") and (
        path.endswith("/decision")
        or path.endswith("/status")
        or path.endswith("/reject-vacancy")
        or path.endswith("/retry")
        or path.endswith("/materials")
        or path.endswith("/generate-materials")
        or path.endswith("/resume")
        or path.endswith("/prepare-browser-review")
    ):
        return "review:write"
    if method == "GET" and path.startswith("/v1/applications/"):
        return "review:read"
    if path == "/v1/review-queue":
        return "review:read"
    return "api:access"


async def greenhouse_http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        yield client


async def headhunter_http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
        yield client


async def llm_http_client() -> AsyncIterator[httpx.AsyncClient]:
    settings = get_settings()
    timeout = httpx.Timeout(settings.materials_generation_timeout_seconds + 10)
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, trust_env=False
    ) as client:
        yield client


async def reranker_http_client() -> AsyncIterator[httpx.AsyncClient | None]:
    settings = get_settings()
    if settings.reranker_service_url is None or settings.reranker_api_key is None:
        yield None
        return
    async with httpx.AsyncClient(
        base_url=settings.reranker_service_url,
        timeout=settings.matching_model_timeout_seconds,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        yield client


async def matching_queue_redis() -> AsyncIterator[redis.Redis]:
    settings = get_settings()
    client = redis.Redis.from_url(settings.redis_url, decode_responses=False)
    try:
        yield client
    finally:
        await client.aclose()


def _configured_secret(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value().strip()
    return value or None


# --- Vacancy hybrid search singleton ---

_vacancy_retriever_instance: object | None = None  # VacancyHybridRetriever | None
_vacancy_retriever_init_attempted = False
_vacancy_index_ensured = False


async def _get_vacancy_retriever():  # type: ignore[return]
    """Lazy-initialise the vacancy hybrid retriever.

    Returns VacancyHybridRetriever if OpenSearch + embedding service are
    configured and reachable, None otherwise.  The instance is cached for
    the process lifetime.
    """
    from app.matching.http_models import HttpEmbeddingClient
    from app.matching.vacancy_index import OpenSearchVacancyIndex
    from app.matching.vacancy_retriever import VacancyHybridRetriever

    global _vacancy_retriever_instance, _vacancy_retriever_init_attempted, _vacancy_index_ensured
    if _vacancy_retriever_init_attempted:
        if _vacancy_retriever_instance is not None and not _vacancy_index_ensured:
            retriever = _vacancy_retriever_instance
            try:
                await retriever._vacancy_index.ensure_index()
                _vacancy_index_ensured = True
            except Exception:
                pass
        return _vacancy_retriever_instance
    _vacancy_retriever_init_attempted = True

    settings = get_settings()
    try:
        opensearch_http = httpx.AsyncClient(
            base_url=settings.opensearch_url,
            timeout=30,
            follow_redirects=False,
        )
        # Quick health check
        health = await opensearch_http.get("/_cluster/health")
        if health.status_code >= 400:
            await opensearch_http.aclose()
            return None

        vacancy_index = OpenSearchVacancyIndex(
            opensearch_http,
            index_prefix=settings.opensearch_vacancy_index_prefix,
            read_alias=settings.opensearch_vacancy_read_alias,
            write_alias=settings.opensearch_vacancy_write_alias,
            dimensions=settings.embedding_dimensions,
        )
        await vacancy_index.ensure_index()
        _vacancy_index_ensured = True

        embedding_client: HttpEmbeddingClient | None = None
        if settings.resolved_embedding_service_url:
            try:
                model_http = httpx.AsyncClient(
                    base_url=settings.resolved_embedding_service_url,
                    timeout=30,
                    follow_redirects=False,
                )
                embedding_client = HttpEmbeddingClient(
                    model_http,
                    dimensions=settings.embedding_dimensions,
                )
            except Exception:
                embedding_client = None

        retriever = VacancyHybridRetriever(
            vacancy_index,
            embedding_client,
            top_k=settings.vacancy_search_top_k,
            rrf_k=settings.vacancy_search_rrf_k,
        )
        _vacancy_retriever_instance = retriever
        return retriever
    except Exception:
        return None


def llm_is_configured(settings: Settings) -> bool:
    return bool(
        _configured_secret(settings.anthropic_api_key)
        or _configured_secret(settings.gemini_api_key)
    )


def build_model_providers(
    http_client: httpx.AsyncClient, settings: Settings
) -> tuple[ModelProvider, ...]:
    """Build every configured provider, in preference order (Anthropic first, then Gemini).

    Both are optional and independent: set one, the other, or both. ModelRouter tries them in
    order and falls through to the next on failure/budget-exceeded, so configuring both gives a
    fallback for free.
    """
    providers: list[ModelProvider] = []
    anthropic_api_key = _configured_secret(settings.anthropic_api_key)
    gemini_api_key = _configured_secret(settings.gemini_api_key)
    if anthropic_api_key is not None:
        providers.append(
            AnthropicMessagesProvider(
                http_client,
                api_key=anthropic_api_key,
                model=settings.anthropic_model,
            )
        )
    if gemini_api_key is not None:
        providers.append(
            GeminiProvider(
                http_client,
                api_key=gemini_api_key,
                model=settings.gemini_model,
            )
        )
    return tuple(providers)


def _llm_preference_service(session: Session, settings: Settings) -> LlmPreferenceService:
    encryption_key = _configured_secret(settings.browser_state_encryption_key)
    if encryption_key is None:
        raise InvalidLlmPreference("APP_BROWSER_STATE_ENCRYPTION_KEY is required")
    return LlmPreferenceService(session, encryption_key=encryption_key)


def build_user_model_providers(
    http_client: httpx.AsyncClient,
    session: Session,
    user_id: str,
    settings: Settings,
    purpose: str | LlmPreferencePurpose = DEFAULT_LLM_PURPOSE,
) -> tuple[ModelProvider, ...]:
    encryption_key = _configured_secret(settings.browser_state_encryption_key)
    if encryption_key is None:
        return build_model_providers(http_client, settings)
    preference = resolve_preference(session, user_id, encryption_key, purpose)
    if preference is None:
        return build_model_providers(http_client, settings)
    if preference.provider == "anthropic":
        return (
            AnthropicMessagesProvider(
                http_client, api_key=preference.api_key, model=preference.model
            ),
        )
    if preference.provider == "gemini":
        return (GeminiProvider(http_client, api_key=preference.api_key, model=preference.model),)
    if preference.base_url is None:
        raise InvalidLlmPreference("OpenAI-compatible base URL is required")
    return (
        OpenAICompatibleProvider(
            http_client,
            api_key=preference.api_key,
            model=preference.model,
            base_url=preference.base_url,
        ),
    )


def build_email_sync_service(
    session: Session,
    http_client: httpx.AsyncClient,
    user_id: str,
    settings: Settings,
) -> ApplicationEmailSyncService:
    """Build the email sync service with the user's LLM router and RAG matcher."""
    providers = build_user_model_providers(http_client, session, user_id, settings)
    router = ModelRouter(providers)
    prompt_registry = PromptRegistry.load(Path(__file__).parents[2] / "prompts" / "registry.json")
    classifier = EmailClassifier(router, prompt_registry)
    rag_client = create_rag_client(
        service_url=settings.rag_service_url,
        api_key=(
            settings.rag_api_key.get_secret_value() if settings.rag_api_key is not None else None
        ),
        project_id=settings.rag_project_id,
        collection=VACANCY_COLLECTION,
        timeout_seconds=settings.rag_timeout_seconds,
        enabled=settings.rag_enabled,
    )
    matcher = EmailVacancyMatcher(session, rag_client)
    return ApplicationEmailSyncService(session, classifier=classifier, matcher=matcher)


async def model_router() -> AsyncIterator[ModelRouter]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False) as client:
        yield ModelRouter(build_model_providers(client, settings))


def document_storage() -> DocumentStorage:
    settings = get_settings()
    return DocumentStorage(
        settings.artifact_directory / "documents",
        max_document_bytes=settings.max_document_bytes,
    )


def evidence_artifact_storage() -> EvidenceArtifactStorage:
    settings = get_settings()
    return EvidenceArtifactStorage(
        settings.artifact_directory,
        max_artifact_bytes=settings.max_evidence_bytes,
    )


@app.middleware("http")
async def secure_and_observe_request(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    metrics.increment("http_requests_total")
    settings = get_settings()
    api_clients = settings.api_clients()
    if request.url.path.startswith("/v1/") and api_clients:
        supplied_key = request.headers.get("x-api-key", "")
        granted_scopes = next(
            (
                scopes
                for expected_key, scopes in api_clients.items()
                if secrets.compare_digest(supplied_key, expected_key)
            ),
            None,
        )
        if granted_scopes is None:
            metrics.increment("http_authentication_failures_total")
            return Response(status_code=status.HTTP_401_UNAUTHORIZED)
        required_scope = required_api_scope(request.method, request.url.path)
        if "*" not in granted_scopes and required_scope not in granted_scopes:
            metrics.increment("http_authorization_failures_total")
            return Response(status_code=status.HTTP_403_FORBIDDEN)
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = request.headers.get(
        "x-correlation-id", str(uuid.uuid4())
    )
    return response


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", submission_mode=get_settings().submission_mode.value)


@app.get("/v1/debug/logs", include_in_schema=False)
def debug_logs(
    level: str = "debug",
    limit: int = 200,
    since: str | None = None,
    search: str | None = None,
) -> Response:
    """Return recent structured log entries for the debug tab."""
    from app.observability.log_buffer import get_log_buffer

    valid_levels = {"verbose", "debug", "info", "warning", "error", "critical", "major"}
    if level not in valid_levels:
        valid_str = ", ".join(sorted(valid_levels))
        raise HTTPException(status_code=422, detail=f"Invalid level. Use: {valid_str}")
    if not 1 <= limit <= 5000:
        raise HTTPException(status_code=422, detail="limit must be 1..5000")

    entries = get_log_buffer().query(
        min_level=level,
        limit=limit,
        since=since,
        search=search,
    )
    import json

    payload = {
        "count": len(entries),
        "level_filter": level,
        "entries": [
            {
                "timestamp": e.timestamp,
                "level": e.level,
                "event": e.event,
                "logger": e.logger,
                "message": e.message,
                "fields": e.fields,
            }
            for e in entries
        ],
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False, default=str),
        media_type="application/json",
    )


@app.post("/v1/debug/logs/clear", include_in_schema=False)
def debug_logs_clear() -> dict[str, str]:
    """Clear the in-process log buffer."""
    from app.observability.log_buffer import get_log_buffer

    get_log_buffer().clear()
    return {"status": "cleared"}


@app.get("/review", include_in_schema=False)
def review_interface() -> FileResponse:
    return FileResponse(
        REVIEW_UI_PATH,
        media_type="text/html",
        headers={
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                "img-src 'self' blob:; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/dashboard", include_in_schema=False)
def dashboard_interface() -> FileResponse:
    return FileResponse(
        DASHBOARD_UI_PATH,
        media_type="text/html",
        headers={
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                "img-src 'self' blob:; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get(
    "/v1/users/{user_id}/vacancies",
    response_model=SavedVacancyPageResponse,
)
async def list_saved_vacancies(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
    query: str = "",
    source: str = "all",
    status_filter: str = "all",
    location: str = "",
    min_match_score: int = 0,
    published_from: date | None = None,
    published_to: date | None = None,
    work_format: str = "all",
    employment_type: str = "all",
    page: int = 1,
    page_size: int = 20,
) -> SavedVacancyPageResponse:
    if source not in {"all", "headhunter", "linkedin", "greenhouse", "registry", "other"}:
        raise HTTPException(status_code=422, detail="Unsupported vacancy source")
    if work_format not in {"all", "remote", "hybrid", "office", "unspecified"}:
        raise HTTPException(status_code=422, detail="Unsupported work format")
    if employment_type not in {
        "all",
        "full_time",
        "part_time",
        "contract",
        "project",
        "temporary",
        "internship",
    }:
        raise HTTPException(status_code=422, detail="Unsupported employment type")
    if not 0 <= min_match_score <= 100 or page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(status_code=422, detail="Invalid vacancy pagination or score filter")
    if published_from is not None and published_to is not None and published_from > published_to:
        raise HTTPException(status_code=422, detail="Invalid vacancy publication date range")

    # Hybrid search: BM25 + vector + RRF when query is provided
    vacancy_ids: tuple[str, ...] | None = None
    normalized_query = query.strip()
    if normalized_query:
        retriever = await _get_vacancy_retriever()
        if retriever is not None:
            try:
                vacancy_ids = tuple(await retriever.search(normalized_query))
            except Exception:
                # Hybrid search failed — fall back to ILIKE
                vacancy_ids = None

    try:
        vacancy_page = VacancyCatalogService(session).list_saved_vacancies(
            user_id,
            query=query,
            source=source,
            status=status_filter,
            location=location,
            min_match_score=min_match_score,
            published_from=published_from,
            published_to=published_to,
            work_format=work_format,
            employment_type=employment_type,
            page=page,
            page_size=page_size,
            vacancy_ids=vacancy_ids,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return SavedVacancyPageResponse(
        items=[SavedVacancyResponse(**asdict(item)) for item in vacancy_page.items],
        total=vacancy_page.total,
        page=vacancy_page.page,
        page_size=vacancy_page.page_size,
        total_pages=vacancy_page.total_pages,
    )


@app.get(
    "/v1/users/{user_id}/company-blacklist",
    response_model=list[CompanyBlacklistResponse],
)
def list_company_blacklist(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> list[CompanyBlacklistResponse]:
    try:
        entries = CompanyBlacklistService(session).list_entries(user_id)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return [
        CompanyBlacklistResponse.model_validate(entry, from_attributes=True) for entry in entries
    ]


@app.post(
    "/v1/users/{user_id}/company-blacklist",
    response_model=CompanyBlacklistResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_company_blacklist(
    user_id: str,
    request: CompanyBlacklistRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> CompanyBlacklistResponse:
    try:
        entry = CompanyBlacklistService(session).add(user_id, request.company)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (DuplicateEntityError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return CompanyBlacklistResponse.model_validate(entry, from_attributes=True)


@app.delete(
    "/v1/users/{user_id}/company-blacklist/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_company_blacklist(
    user_id: str,
    entry_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> Response:
    try:
        CompanyBlacklistService(session).remove(user_id, entry_id)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/v1/evidence/{artifact_id}", response_class=FileResponse)
def get_evidence_artifact(
    artifact_id: str,
    session: Annotated[Session, Depends(session_scope)],
    storage: Annotated[EvidenceArtifactStorage, Depends(evidence_artifact_storage)],
) -> FileResponse:
    try:
        artifact = storage.resolve(session, artifact_id)
    except InvalidEvidenceArtifact as error:
        raise HTTPException(status_code=404, detail="Evidence artifact unavailable") from error
    return FileResponse(
        artifact.path,
        media_type=artifact.row.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics(session: Annotated[Session, Depends(session_scope)]) -> Response:
    task_queue_depth = session.scalar(
        select(func.count())
        .select_from(WorkflowTaskRow)
        .where(WorkflowTaskRow.state.in_(("pending", "scheduled", "retry_scheduled")))
    )
    review_queue_depth = session.scalar(
        select(func.count())
        .select_from(ApplicationRow)
        .where(ApplicationRow.status == "awaiting_review")
    )
    metrics.set_gauge("workflow_task_queue_depth", float(task_queue_depth or 0))
    metrics.set_gauge("application_review_queue_depth", float(review_queue_depth or 0))
    for heartbeat in session.scalars(select(WorkerHeartbeatRow)).all():
        metrics.set_gauge(
            f"worker_{heartbeat.worker_name.replace('-', '_')}_last_seen_seconds",
            heartbeat.last_seen_at.timestamp(),
        )
    return Response(metrics.render_prometheus(), media_type="text/plain; version=0.0.4")


@app.get("/ready", response_model=HealthResponse)
def ready(session: Annotated[Session, Depends(session_scope)]) -> HealthResponse:
    try:
        session.execute(text("SELECT 1"))
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable"
        ) from error
    return health()


@app.get("/api/v1/admin/reranker/status", response_model=RerankerStatusResponse)
async def get_reranker_status(
    http_client: Annotated[httpx.AsyncClient | None, Depends(reranker_http_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RerankerStatusResponse:
    api_key = (
        settings.reranker_api_key.get_secret_value()
        if settings.reranker_api_key is not None
        else None
    )
    partially_configured = (settings.reranker_service_url is None) != (api_key is None)
    result = await RerankerStatusProbe().probe(
        http_client,
        api_key=api_key,
        partially_configured=partially_configured,
    )
    return RerankerStatusResponse.model_validate(asdict(result))


@app.get("/v1/connectors", response_model=list[ConnectorCapabilityResponse])
def list_connectors() -> list[ConnectorCapabilityResponse]:
    return [
        ConnectorCapabilityResponse.model_validate(asdict(capability))
        for capability in ADAPTER_CAPABILITIES
    ]


@app.post("/v1/connectors/browser-handoff", response_model=BrowserHandoffResponse)
def browser_handoff(request: BrowserHandoffRequest) -> BrowserHandoffResponse:
    try:
        handoff = create_browser_handoff(str(request.source_url))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return BrowserHandoffResponse.model_validate(asdict(handoff))


@app.post("/v1/assessments", response_model=AssessmentResponse)
def create_assessment(request: AssessmentRequest) -> AssessmentResponse:
    vacancy = Vacancy(
        source_url=str(request.vacancy.source_url),
        title=request.vacancy.title,
        company=request.vacancy.company,
        required_skills=frozenset(request.vacancy.required_skills),
        preferred_skills=frozenset(request.vacancy.preferred_skills),
    )
    facts = [ProfileFact(**fact.model_dump()) for fact in request.profile_facts]
    assessment = assess_vacancy(vacancy, facts)
    return AssessmentResponse(
        score=assessment.score,
        matched_required_skills=list(assessment.matched_required_skills),
        missing_required_skills=list(assessment.missing_required_skills),
        matched_preferred_skills=list(assessment.matched_preferred_skills),
        recommendation=assessment.recommendation,
    )


@app.post("/v1/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    request: UserRequest, session: Annotated[Session, Depends(session_scope)]
) -> UserResponse:
    user = RecruitmentService(session).create_user(request.display_name)
    return UserResponse(id=user.id, display_name=user.display_name)


@app.get(
    "/v1/users/{user_id}/application-statistics",
    response_model=ApplicationStatisticsResponse,
)
def get_application_statistics(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> ApplicationStatisticsResponse:
    try:
        service = RecruitmentService(session)
        total, counts = service.get_application_statistics(user_id)
        email_counts = service.get_application_email_statistics(user_id)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return ApplicationStatisticsResponse(total=total, **counts, **email_counts)


@app.post(
    "/v1/users/{user_id}/application-sync",
    response_model=ApplicationSyncResponse,
)
async def synchronize_application_statuses(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> ApplicationSyncResponse:
    settings = get_settings()
    if session.get(UserRow, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    if settings.browser_state_encryption_key is None:
        raise HTTPException(
            status_code=503,
            detail="APP_BROWSER_STATE_ENCRYPTION_KEY is required",
        )
    probes: dict[str, ApplicationSubmissionProbe] = {}
    browser_client = BrowserWorkerClient(settings.browser_worker_url)
    headhunter_probe = await browser_client.probe(user_id=user_id, site_key="headhunter")
    if headhunter_probe.valid:
        probes["headhunter"] = HttpHeadHunterAdapter(browser_client, user_id)
    if settings.enable_linkedin_apply:
        linkedin_probe = await browser_client.probe(user_id=user_id, site_key="linkedin")
        if linkedin_probe.valid:
            probes["linkedin-reference"] = HttpLinkedInAdapter(browser_client, user_id)
    try:
        summary = await ApplicationStatusSyncService(session).synchronize(user_id, probes)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return ApplicationSyncResponse.model_validate(asdict(summary))


@app.post(
    "/v1/users/{user_id}/application-email-sync",
    response_model=ApplicationEmailSyncResponse,
)
async def synchronize_application_emails(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
    provider: Annotated[ApplicationEmailProvider, Depends(get_application_email_provider)],
    http_client: Annotated[httpx.AsyncClient, Depends(llm_http_client)],
) -> ApplicationEmailSyncResponse:
    try:
        service = build_email_sync_service(session, http_client, user_id, get_settings())
        summary = await service.synchronize(user_id, provider)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return ApplicationEmailSyncResponse.model_validate(asdict(summary))


@app.post(
    "/v1/users/{user_id}/application-email-import",
    response_model=ApplicationEmailSyncResponse,
)
async def import_application_emails(
    user_id: str,
    files: Annotated[
        list[UploadFile],
        File(description="EML files, an mbox mailbox, or ZIP archives containing EML files"),
    ],
    session: Annotated[Session, Depends(session_scope)],
    http_client: Annotated[httpx.AsyncClient, Depends(llm_http_client)],
) -> ApplicationEmailSyncResponse:
    if not files:
        raise HTTPException(status_code=422, detail="At least one email file is required")
    if len(files) > MAX_EMAIL_IMPORT_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_EMAIL_IMPORT_FILES} email files can be imported at once",
        )
    uploaded_files: list[UploadedEmailFile] = []
    total_bytes = 0
    try:
        for upload in files:
            remaining = MAX_EMAIL_IMPORT_BYTES - total_bytes
            content = await upload.read(remaining + 1)
            if len(content) > remaining:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Email import exceeds the total size limit",
                )
            total_bytes += len(content)
            uploaded_files.append(
                UploadedEmailFile(
                    filename=upload.filename or "",
                    content=content,
                )
            )
    finally:
        for upload in files:
            await upload.close()
    try:
        provider = UploadedEmailImportProvider(uploaded_files)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        service = build_email_sync_service(session, http_client, user_id, get_settings())
        summary = await service.synchronize(user_id, provider)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return ApplicationEmailSyncResponse.model_validate(asdict(summary))


def _email_review_response(event: ApplicationEmailEventRow) -> EmailReviewItemResponse:
    candidates = [
        EmailReviewCandidateResponse(
            application_id=str(item.get("application_id", "")),
            vacancy_id=str(item.get("vacancy_id", "")),
            company=str(item.get("company", "")),
            title=str(item.get("title", "")),
            score=float(cast(Any, item.get("score", 0.0))),
        )
        for item in (event.candidates or [])
    ]
    return EmailReviewItemResponse(
        id=event.id,
        subject=event.subject,
        body=event.body,
        category=event.category,
        confidence=event.confidence,
        outcome=event.outcome,
        application_id=event.application_id,
        candidates=candidates,
        processed_at=event.processed_at,
    )


@app.get("/v1/users/{user_id}/email-review", response_model=list[EmailReviewItemResponse])
def list_email_review(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> list[EmailReviewItemResponse]:
    return [
        _email_review_response(event)
        for event in ApplicationEmailEventService(session).list_review_items(user_id)
    ]


@app.post(
    "/v1/users/{user_id}/email-review/{event_id}/resolve",
    response_model=EmailReviewItemResponse,
)
def resolve_email_review(
    user_id: str,
    event_id: str,
    request: EmailReviewResolveRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> EmailReviewItemResponse:
    try:
        event = ApplicationEmailEventService(session).resolve(
            user_id,
            event_id,
            action=request.action,
            application_id=request.application_id,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _email_review_response(event)


@app.post("/v1/llm/models", response_model=LlmModelsResponse)
async def list_llm_models(
    request: LlmModelsRequest,
    http_client: Annotated[httpx.AsyncClient, Depends(headhunter_http_client)],
) -> LlmModelsResponse:
    try:
        models = await fetch_available_models(
            http_client,
            provider=request.provider,
            api_key=request.api_key.get_secret_value(),
            base_url=request.base_url,
        )
    except (InvalidLlmPreference, LlmModelDiscoveryError, httpx.HTTPError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return LlmModelsResponse(models=models)


@app.get(
    "/v1/users/{user_id}/llm-preference",
    response_model=LlmPreferenceResponse | None,
)
def get_llm_preference(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
    purpose: str = "materials",
) -> LlmPreferenceResponse | None:
    row = session.get(LlmPreferenceRow, (user_id, purpose))
    if row is None:
        return None
    return LlmPreferenceResponse.model_validate(row, from_attributes=True)


@app.get(
    "/v1/users/{user_id}/email-integration",
    response_model=EmailIntegrationResponse | None,
)
def get_email_integration(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> EmailIntegrationResponse | None:
    if session.get(UserRow, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    row = session.get(EmailIntegrationRow, user_id)
    if row is None:
        return None
    return EmailIntegrationResponse(
        host=row.host,
        port=row.port,
        username=row.username,
        use_ssl=row.use_ssl,
        mailbox=row.mailbox,
        enabled=row.enabled,
    )


@app.get(
    "/v1/users/{user_id}/autofill-values",
    response_model=list[AutofillValueResponse],
)
def get_autofill_values(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> list[AutofillValueResponse]:
    encryption_key = _configured_secret(get_settings().browser_state_encryption_key)
    if encryption_key is None:
        raise HTTPException(
            status_code=503,
            detail="APP_BROWSER_STATE_ENCRYPTION_KEY is required",
        )
    try:
        values = list_autofill_values(session, user_id=user_id, encryption_key=encryption_key)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidAutofillValueEncryption as error:
        raise HTTPException(
            status_code=503, detail="Autofill values cannot be decrypted"
        ) from error
    return [AutofillValueResponse.model_validate(value, from_attributes=True) for value in values]


@app.post(
    "/v1/users/{user_id}/autofill-values",
    response_model=AutofillValueResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_autofill_value(
    user_id: str,
    request: AutofillValueCreateRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> AutofillValueResponse:
    encryption_key = _configured_secret(get_settings().browser_state_encryption_key)
    if encryption_key is None:
        raise HTTPException(
            status_code=503,
            detail="APP_BROWSER_STATE_ENCRYPTION_KEY is required",
        )
    try:
        row = create_autofill_value(
            session,
            user_id=user_id,
            key=request.key,
            label=request.label,
            value_type=request.value_type,
            serialized_value=request.serialized_value,
            is_sensitive=request.is_sensitive,
            encryption_key=encryption_key,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (InvalidAutofillKey, InvalidAutofillValue, InvalidAutofillValueEncryption) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    usage_policy = evaluate_autofill_usage(is_sensitive=row.is_sensitive)
    return AutofillValueResponse(
        id=row.id,
        user_id=row.user_id,
        key=row.key,
        label=row.label,
        value_type=row.value_type,
        serialized_value=request.serialized_value,
        is_sensitive=row.is_sensitive,
        requires_review=usage_policy.requires_review,
        may_send_to_llm=usage_policy.may_send_to_llm,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@app.put(
    "/v1/users/{user_id}/autofill-values/{key:path}",
    response_model=AutofillValueResponse,
)
def replace_autofill_value(
    user_id: str,
    key: str,
    request: AutofillValueUpdateRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> AutofillValueResponse:
    encryption_key = _configured_secret(get_settings().browser_state_encryption_key)
    if encryption_key is None:
        raise HTTPException(
            status_code=503,
            detail="APP_BROWSER_STATE_ENCRYPTION_KEY is required",
        )
    try:
        row = update_autofill_value(
            session,
            user_id=user_id,
            key=key,
            serialized_value=request.serialized_value,
            encryption_key=encryption_key,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (InvalidAutofillKey, InvalidAutofillValueEncryption) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    usage_policy = evaluate_autofill_usage(is_sensitive=row.is_sensitive)
    return AutofillValueResponse(
        id=row.id,
        user_id=row.user_id,
        key=row.key,
        label=row.label,
        value_type=row.value_type,
        serialized_value=request.serialized_value,
        is_sensitive=row.is_sensitive,
        requires_review=usage_policy.requires_review,
        may_send_to_llm=usage_policy.may_send_to_llm,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@app.delete(
    "/v1/users/{user_id}/autofill-values/{key:path}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_autofill_value(
    user_id: str,
    key: str,
    session: Annotated[Session, Depends(session_scope)],
) -> Response:
    try:
        delete_autofill_value(session, user_id=user_id, key=key)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidAutofillKey as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.put("/v1/users/{user_id}/llm-preference", response_model=LlmPreferenceResponse)
def update_llm_preference(
    user_id: str,
    request: LlmPreferenceUpdateRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> LlmPreferenceResponse:
    settings = get_settings()
    try:
        row = _llm_preference_service(session, settings).save(
            user_id=user_id,
            provider=request.provider,
            model=request.model,
            api_key=request.api_key.get_secret_value() if request.api_key is not None else None,
            base_url=request.base_url,
            purpose=request.purpose,
        )
    except LlmPreferenceNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidLlmPreference as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return LlmPreferenceResponse.model_validate(row, from_attributes=True)


@app.put(
    "/v1/users/{user_id}/email-integration",
    response_model=EmailIntegrationResponse,
)
def update_email_integration(
    user_id: str,
    request: EmailIntegrationUpdateRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> EmailIntegrationResponse:
    encryption_key = _configured_secret(get_settings().browser_state_encryption_key)
    if encryption_key is None:
        raise HTTPException(status_code=503, detail="Encrypted storage is not configured")
    try:
        row = EmailIntegrationService(session, encryption_key=encryption_key).save(
            user_id=user_id,
            host=request.host,
            port=request.port,
            username=request.username,
            password=(
                request.password.get_secret_value() if request.password is not None else None
            ),
            use_ssl=request.use_ssl,
            mailbox=request.mailbox,
            enabled=request.enabled,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidEmailIntegration as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return EmailIntegrationResponse(
        host=row.host,
        port=row.port,
        username=row.username,
        use_ssl=row.use_ssl,
        mailbox=row.mailbox,
        enabled=row.enabled,
    )


def _site_definition_response(row: SiteDefinitionRow) -> SiteDefinitionResponse:
    return SiteDefinitionResponse(
        id=row.id,
        site_key=row.site_key,
        name=row.name,
        login_url=row.login_url,
        allowed_hosts=list(row.allowed_hosts),
        authorization_rules=dict(row.authorization_rules),
        is_archived=row.archived_at is not None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _custom_authorization_site(row: SiteDefinitionRow) -> BrowserAuthorizationSite:
    raw_markers = row.authorization_rules.get("login_path_markers", [])
    if not isinstance(raw_markers, list):
        raw_markers = []
    markers = tuple(
        marker
        for marker in raw_markers
        if isinstance(marker, str) and marker.startswith("/") and len(marker) <= 500
    )
    if not markers:
        login_path = urlsplit(row.login_url).path or "/"
        markers = (login_path,)
    return BrowserAuthorizationSite(
        site_key=row.site_key,
        login_url=row.login_url,
        allowed_hosts=tuple(row.allowed_hosts),
        login_path_markers=markers,
    )


def _authorization_site(
    session: Session,
    *,
    user_id: str,
    site_key: str,
) -> BrowserAuthorizationSite:
    known = KNOWN_AUTHORIZATION_SITES.get(site_key)
    if known is not None:
        return known
    row = session.scalar(
        select(SiteDefinitionRow).where(
            SiteDefinitionRow.user_id == user_id,
            SiteDefinitionRow.site_key == site_key,
            SiteDefinitionRow.archived_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Site definition not found")
    return _custom_authorization_site(row)


def _authorization_site_payload(site: BrowserAuthorizationSite) -> dict[str, object]:
    return {
        "site_key": site.site_key,
        "login_url": site.login_url,
        "allowed_hosts": list(site.allowed_hosts),
        "login_path_markers": list(site.login_path_markers),
        "allow_subdomains": site.allow_subdomains,
    }


def _worker_http_error(error: httpx.HTTPStatusError) -> HTTPException:
    try:
        detail = error.response.json().get("detail", error.response.text)
    except Exception:
        detail = error.response.text
    return HTTPException(status_code=error.response.status_code, detail=str(detail))


async def _browser_login_is_waiting(user_id: str, site_key: str) -> bool:
    try:
        client = BrowserWorkerClient(get_settings().browser_worker_url)
        return await client.login_is_waiting(user_id=user_id, site_key=site_key)
    except Exception:
        return False


@app.get(
    "/v1/users/{user_id}/site-definitions",
    response_model=list[SiteDefinitionResponse],
)
def get_site_definitions(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
    include_archived: bool = False,
) -> list[SiteDefinitionResponse]:
    if session.get(UserRow, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    statement = select(SiteDefinitionRow).where(SiteDefinitionRow.user_id == user_id)
    if not include_archived:
        statement = statement.where(SiteDefinitionRow.archived_at.is_(None))
    rows = session.scalars(statement.order_by(SiteDefinitionRow.name, SiteDefinitionRow.site_key))
    return [_site_definition_response(row) for row in rows]


@app.post(
    "/v1/users/{user_id}/site-definitions",
    response_model=SiteDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_site_definition(
    user_id: str,
    request: SiteDefinitionCreateRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> SiteDefinitionResponse:
    if request.site_key.strip().casefold() in KNOWN_AUTHORIZATION_SITES:
        raise HTTPException(status_code=409, detail="Site key is reserved")
    try:
        row = create_site_definition(
            session,
            user_id=user_id,
            site_key=request.site_key,
            name=request.name,
            login_url=request.login_url,
            allowed_hosts=request.allowed_hosts,
            authorization_rules=request.authorization_rules,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (InvalidSiteDefinition, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _site_definition_response(row)


@app.put(
    "/v1/users/{user_id}/site-definitions/{site_definition_id}",
    response_model=SiteDefinitionResponse,
)
def replace_site_definition(
    user_id: str,
    site_definition_id: str,
    request: SiteDefinitionUpdateRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> SiteDefinitionResponse:
    try:
        row = update_site_definition(
            session,
            user_id=user_id,
            site_definition_id=site_definition_id,
            name=request.name,
            login_url=request.login_url,
            allowed_hosts=request.allowed_hosts,
            authorization_rules=request.authorization_rules,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (InvalidSiteDefinitionUpdate, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _site_definition_response(row)


@app.post(
    "/v1/users/{user_id}/site-definitions/{site_definition_id}/archive",
    response_model=SiteDefinitionResponse,
)
async def archive_user_site_definition(
    user_id: str,
    site_definition_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> SiteDefinitionResponse:
    try:
        row = archive_site_definition(
            session,
            user_id=user_id,
            site_definition_id=site_definition_id,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    client = BrowserWorkerClient(get_settings().browser_worker_url)
    with suppress(httpx.HTTPError):
        await client.login_cancel(user_id=user_id, site_key=row.site_key)
    return _site_definition_response(row)


def _site_field_response(session: Session, row: SiteFieldRow) -> SiteFieldResponse:
    mapping = session.scalar(
        select(SiteFieldMappingRow).where(SiteFieldMappingRow.site_field_id == row.id)
    )
    mapping_response = (
        SiteFieldMappingResponse(
            id=mapping.id,
            value_key=mapping.value_key,
            transformation=dict(mapping.transformation),
            review_required=mapping.review_required,
        )
        if mapping is not None
        else None
    )
    overrides = session.scalars(
        select(SiteValueOverrideRow).where(
            SiteValueOverrideRow.site_definition_id == row.site_definition_id,
            SiteValueOverrideRow.value_key == (mapping.value_key if mapping else ""),
        )
    ).all()
    return SiteFieldResponse(
        id=row.id,
        site_definition_id=row.site_definition_id,
        field_key=row.field_key,
        semantic_key=row.semantic_key,
        label=row.label,
        field_type=row.field_type,
        is_required=row.is_required,
        options=list(row.options),
        selector_candidates=list(row.selector_candidates),
        mapping=mapping_response,
        has_site_override=any(override.scope_key == "*" for override in overrides),
        has_field_override=any(override.scope_key == row.id for override in overrides),
    )


@app.post(
    "/v1/users/{user_id}/site-definitions/{site_definition_id}/fields/discovery",
    response_model=list[SiteFieldResponse],
)
def save_site_field_discovery(
    user_id: str,
    site_definition_id: str,
    request: SiteFieldDiscoveryRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> list[SiteFieldResponse]:
    fields = tuple(
        FormField(
            field_id=item.field_key,
            label=item.label,
            field_type=FormFieldType(item.field_type),
            is_required=item.is_required,
            options=tuple(item.options),
            semantic_category=item.semantic_key,
            source_locator=item.selector_candidates[0],
            locator_candidates=tuple(item.selector_candidates),
        )
        for item in request.fields
    )
    try:
        rows = save_discovered_site_fields(
            session,
            user_id=user_id,
            site_definition_id=site_definition_id,
            fields=fields,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (InvalidSiteField, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return [_site_field_response(session, row) for row in rows]


@app.get(
    "/v1/users/{user_id}/site-definitions/{site_definition_id}/fields",
    response_model=list[SiteFieldResponse],
)
def get_site_fields(
    user_id: str,
    site_definition_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> list[SiteFieldResponse]:
    site = session.scalar(
        select(SiteDefinitionRow).where(
            SiteDefinitionRow.id == site_definition_id,
            SiteDefinitionRow.user_id == user_id,
            SiteDefinitionRow.archived_at.is_(None),
        )
    )
    if site is None:
        raise HTTPException(status_code=404, detail="Site definition not found")
    rows = session.scalars(
        select(SiteFieldRow)
        .where(SiteFieldRow.site_definition_id == site.id)
        .order_by(SiteFieldRow.label, SiteFieldRow.field_key)
    )
    return [_site_field_response(session, row) for row in rows]


@app.put(
    "/v1/users/{user_id}/site-fields/{site_field_id}/mapping",
    response_model=SiteFieldMappingResponse,
)
def put_site_field_mapping(
    user_id: str,
    site_field_id: str,
    request: SiteFieldMappingUpdateRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> SiteFieldMappingResponse:
    try:
        row = upsert_site_field_mapping(
            session,
            user_id=user_id,
            site_field_id=site_field_id,
            value_key=request.value_key,
            transformation=request.transformation,
            review_required=request.review_required,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return SiteFieldMappingResponse(
        id=row.id,
        value_key=row.value_key,
        transformation=dict(row.transformation),
        review_required=row.review_required,
    )


@app.put(
    "/v1/users/{user_id}/site-definitions/{site_definition_id}/overrides",
    response_model=SiteValueOverrideResponse,
)
def put_site_value_override(
    user_id: str,
    site_definition_id: str,
    request: SiteValueOverrideRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> SiteValueOverrideResponse:
    encryption_key = _configured_secret(get_settings().browser_state_encryption_key)
    if encryption_key is None:
        raise HTTPException(status_code=503, detail="Encrypted storage is not configured")
    try:
        row = upsert_site_value_override(
            session,
            user_id=user_id,
            site_definition_id=site_definition_id,
            site_field_id=request.site_field_id,
            value_key=request.value_key,
            serialized_value=request.serialized_value,
            is_sensitive=request.is_sensitive,
            encryption_key=encryption_key,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return SiteValueOverrideResponse(
        id=row.id,
        site_definition_id=row.site_definition_id,
        site_field_id=row.site_field_id,
        value_key=row.value_key,
        is_sensitive=row.is_sensitive,
    )


@app.get(
    "/v1/users/{user_id}/site-fields/{site_field_id}/effective-value",
    response_model=EffectiveValueResponse,
)
def get_site_field_effective_value(
    user_id: str,
    site_field_id: str,
    session: Annotated[Session, Depends(session_scope)],
    allow_sensitive: bool = False,
) -> EffectiveValueResponse:
    field = session.scalar(
        select(SiteFieldRow)
        .join(SiteDefinitionRow, SiteDefinitionRow.id == SiteFieldRow.site_definition_id)
        .where(
            SiteFieldRow.id == site_field_id,
            SiteDefinitionRow.user_id == user_id,
            SiteDefinitionRow.archived_at.is_(None),
        )
    )
    if field is None:
        raise HTTPException(status_code=404, detail="Site field not found")
    mapping = session.scalar(
        select(SiteFieldMappingRow).where(SiteFieldMappingRow.site_field_id == field.id)
    )
    if mapping is None:
        raise HTTPException(status_code=404, detail="Site field mapping not found")
    encryption_key = _configured_secret(get_settings().browser_state_encryption_key)
    if encryption_key is None:
        raise HTTPException(status_code=503, detail="Encrypted storage is not configured")

    overrides = session.scalars(
        select(SiteValueOverrideRow).where(
            SiteValueOverrideRow.site_definition_id == field.site_definition_id,
            SiteValueOverrideRow.value_key == mapping.value_key,
        )
    ).all()
    override_candidates = {
        row.scope_key: EffectiveValueCandidate(
            decrypt_autofill_value(row.encrypted_value, encryption_key=encryption_key),
            source_record_id=row.id,
            is_sensitive=row.is_sensitive,
        )
        for row in overrides
    }
    global_row = session.scalar(
        select(AutofillValueRow).where(
            AutofillValueRow.user_id == user_id,
            AutofillValueRow.key == mapping.value_key,
        )
    )
    global_candidate = (
        EffectiveValueCandidate(
            decrypt_autofill_value(global_row.encrypted_value, encryption_key=encryption_key),
            source_record_id=global_row.id,
            is_sensitive=global_row.is_sensitive,
        )
        if global_row is not None
        else None
    )
    try:
        resolved = resolve_effective_autofill_value(
            value_key=mapping.value_key,
            site_field_override=override_candidates.get(field.id),
            site_override=override_candidates.get("*"),
            global_value=global_candidate,
            allow_sensitive=allow_sensitive,
        )
    except EffectiveValueNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except EffectiveValueBlocked as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    transformed_value = apply_effective_value_transformation(
        resolved.value,
        mapping.transformation,
    )
    return EffectiveValueResponse(
        value_key=mapping.value_key,
        value=transformed_value,
        source=resolved.source.value,
        source_record_id=resolved.source_record_id,
        is_sensitive=resolved.is_sensitive,
        requires_review=resolved.requires_review or mapping.review_required,
    )


@app.get(
    "/v1/users/{user_id}/browser-sessions",
    response_model=list[BrowserSessionStatusResponse],
)
async def get_browser_session_statuses(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
    probe: bool = False,
) -> list[BrowserSessionStatusResponse]:
    if session.get(UserRow, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    settings = get_settings()
    try:
        store = create_session_store(settings)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    custom_sites = session.scalars(
        select(SiteDefinitionRow)
        .where(
            SiteDefinitionRow.user_id == user_id,
            SiteDefinitionRow.archived_at.is_(None),
        )
        .order_by(SiteDefinitionRow.name, SiteDefinitionRow.site_key)
    ).all()
    custom_sites = [row for row in custom_sites if row.site_key not in KNOWN_AUTHORIZATION_SITES]
    sites = [
        ("headhunter", "hh.ru", False),
        ("linkedin", "LinkedIn", False),
        *((row.site_key, row.name, True) for row in custom_sites),
    ]
    statuses: list[BrowserSessionStatusResponse] = []
    for site_key, site_name, is_custom in sites:
        row = session.scalar(
            select(BrowserSessionRow).where(
                BrowserSessionRow.user_id == user_id,
                BrowserSessionRow.site_key == site_key,
            )
        )
        is_authorized = False
        session_probe = None
        if row is not None and row.status in {"available", "active"}:
            try:
                stored_state = store.load(row.encrypted_state_path)
                is_authorized = True
                if probe and site_key in KNOWN_AUTHORIZATION_SITES:
                    session_probe = await probe_browser_session(
                        site_key=site_key,
                        state=stored_state,
                        headless=settings.browser_headless,
                        timeout_ms=settings.browser_timeout_ms,
                        artifact_directory=settings.artifact_directory,
                    )
                    if session_probe.is_live is False:
                        is_authorized = False
                        row.status = "expired"
                        session.commit()
            except InvalidBrowserState:
                is_authorized = False
        statuses.append(
            BrowserSessionStatusResponse(
                site_key=site_key,
                site_name=site_name,
                is_custom=is_custom,
                is_authorized=is_authorized,
                is_waiting_for_login=await _browser_login_is_waiting(
                    user_id=user_id, site_key=site_key
                ),
                last_url=row.last_url if row is not None else None,
                updated_at=row.updated_at.isoformat() if row is not None else None,
                is_live=session_probe.is_live if session_probe is not None else None,
                checked_at=(
                    session_probe.checked_at.isoformat() if session_probe is not None else None
                ),
                check_error=session_probe.error if session_probe is not None else None,
            )
        )
    return statuses


@app.post(
    "/v1/users/{user_id}/browser-sessions/{site_key}/start",
    response_model=BrowserAuthorizationResponse,
)
async def start_browser_authorization(
    user_id: str,
    site_key: str,
    session: Annotated[Session, Depends(session_scope)],
) -> BrowserAuthorizationResponse:
    settings = get_settings()
    if settings.environment == "production":
        raise HTTPException(
            status_code=403,
            detail="Вход через видимое окно браузера доступен только в локальной установке",
        )
    if session.get(UserRow, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    site = _authorization_site(session, user_id=user_id, site_key=site_key)
    client = BrowserWorkerClient(settings.browser_worker_url)
    try:
        await client.login_start(
            user_id=user_id,
            site_key=site_key,
            site=_authorization_site_payload(site),
        )
    except httpx.HTTPStatusError as error:
        raise _worker_http_error(error) from error
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502,
            detail="Браузерный воркер недоступен. Запустите browser-worker (--profile browser)",
        ) from error
    return BrowserAuthorizationResponse(site_key=site_key, state="waiting_for_login")


@app.post(
    "/v1/users/{user_id}/browser-sessions/{site_key}/confirm",
    response_model=BrowserAuthorizationResponse,
)
async def confirm_browser_authorization(
    user_id: str,
    site_key: str,
    session: Annotated[Session, Depends(session_scope)],
) -> BrowserAuthorizationResponse:
    if session.get(UserRow, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    _authorization_site(session, user_id=user_id, site_key=site_key)
    adapter_name = site_key if site_key in KNOWN_AUTHORIZATION_SITES else "generic"
    client = BrowserWorkerClient(get_settings().browser_worker_url)
    try:
        await client.login_confirm(
            user_id=user_id,
            site_key=site_key,
            adapter_name=adapter_name,
        )
    except httpx.HTTPStatusError as error:
        raise _worker_http_error(error) from error
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return BrowserAuthorizationResponse(site_key=site_key, state="authorized")


@app.post(
    "/v1/users/{user_id}/browser-sessions/{site_key}/cancel",
    response_model=BrowserAuthorizationResponse,
)
async def cancel_browser_authorization(
    user_id: str,
    site_key: str,
    session: Annotated[Session, Depends(session_scope)],
) -> BrowserAuthorizationResponse:
    if session.get(UserRow, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    _authorization_site(session, user_id=user_id, site_key=site_key)
    client = BrowserWorkerClient(get_settings().browser_worker_url)
    with suppress(httpx.HTTPError):
        await client.login_cancel(user_id=user_id, site_key=site_key)
    return BrowserAuthorizationResponse(site_key=site_key, state="cancelled")


@app.post(
    "/v1/users/{user_id}/facts",
    response_model=ProfileFactResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_profile_fact(
    user_id: str,
    request: ProfileFactRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> ProfileFactResponse:
    service = RecruitmentService(session)
    try:
        fact = service.add_profile_fact(user_id, **request.model_dump())
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await RagSyncService(session, get_settings()).sync_profile(user_id)
    return ProfileFactResponse(
        id=fact.id,
        user_id=fact.user_id,
        category=fact.category,
        name=fact.name,
        value=fact.value,
        is_verified=fact.is_verified,
    )


@app.get(
    "/v1/users/{user_id}/facts",
    response_model=list[ProfileFactResponse],
)
def list_profile_facts(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> list[ProfileFactResponse]:
    try:
        facts = RecruitmentService(session).list_profile_facts(user_id)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return [ProfileFactResponse.model_validate(fact, from_attributes=True) for fact in facts]


@app.put(
    "/v1/users/{user_id}/facts/{fact_id}",
    response_model=ProfileFactResponse,
)
async def update_profile_fact(
    user_id: str,
    fact_id: str,
    request: ProfileFactRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> ProfileFactResponse:
    try:
        fact = RecruitmentService(session).update_profile_fact(
            user_id,
            fact_id,
            **request.model_dump(),
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await RagSyncService(session, get_settings()).sync_profile(user_id)
    return ProfileFactResponse.model_validate(fact, from_attributes=True)


@app.delete(
    "/v1/users/{user_id}/facts/{fact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_profile_fact(
    user_id: str,
    fact_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> Response:
    try:
        RecruitmentService(session).delete_profile_fact(user_id, fact_id)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    await RagSyncService(session, get_settings()).sync_profile(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get(
    "/v1/users/{user_id}/facts-detailed",
    response_model=list[ProfileFactDetailResponse],
)
def list_profile_facts_detailed(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
    source_type: str | None = None,
    status_filter: str | None = None,
) -> list[ProfileFactDetailResponse]:
    """List facts with provenance details, optionally filtered."""
    from sqlalchemy import select as sa_select

    query = sa_select(ProfileFactRow).where(ProfileFactRow.user_id == user_id)
    if source_type:
        query = query.where(ProfileFactRow.source_type == source_type)
    if status_filter:
        query = query.where(ProfileFactRow.status == status_filter)
    query = query.order_by(ProfileFactRow.category, ProfileFactRow.name)
    facts = session.scalars(query).all()
    return [
        ProfileFactDetailResponse(
            id=f.id,
            user_id=f.user_id,
            category=f.category,
            name=f.name,
            value=f.value,
            is_verified=f.is_verified,
            source_type=f.source_type,
            source_id=f.source_id,
            source_text=f.source_text,
            extraction_method=f.extraction_method,
            batch_id=f.batch_id,
            confidence=f.confidence,
            experience_started_at=f.experience_started_at,
            experience_ended_at=f.experience_ended_at,
            status=f.status,
            created_at=f.created_at,
        )
        for f in facts
    ]


@app.post(
    "/v1/users/{user_id}/facts/import-file",
    response_model=FactImportResultResponse,
)
async def import_facts_from_file(
    user_id: str,
    file: UploadFile,
    session: Annotated[Session, Depends(session_scope)],
    auto_accept: bool = False,
) -> FactImportResultResponse:
    """Import facts from an uploaded file (TXT, MD, CSV, JSON, PDF, DOCX)."""
    from app.llm.preferences import InvalidLlmPreference
    from app.services.fact_ingestion import FactIngestionService

    content = await file.read()
    settings = get_settings()
    async with httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False) as client:
        try:
            providers = build_user_model_providers(client, session, user_id, settings)
        except InvalidLlmPreference as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if not providers:
            raise HTTPException(
                status_code=503,
                detail=(
                    "No LLM provider configured. Set one in the Model tab or via "
                    "APP_GEMINI_API_KEY / APP_ANTHROPIC_API_KEY."
                ),
            )
        router = ModelRouter(providers)
        prompt_registry = PromptRegistry.load(
            Path(__file__).parents[2] / "prompts" / "registry.json"
        )
        service = FactIngestionService(session, router, prompt_registry)
        try:
            result = await service.import_from_file(
                user_id,
                file.filename or "upload",
                content,
                auto_accept=auto_accept,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    await RagSyncService(session, settings).sync_profile(user_id)
    return FactImportResultResponse(
        batch_id=result.batch_id,
        source_type=result.source_type,
        facts_created=result.facts_created,
        facts_merged=result.facts_merged,
        facts_skipped=result.facts_skipped,
        facts_rejected=result.facts_rejected,
        candidates=[
            {
                "name": c.name,
                "category": c.category,
                "value": c.value,
                "confidence": c.confidence,
                "source_text": c.source_text,
                "duplicate_status": c.duplicate_status.value,
            }
            for c in result.candidates[:50]
        ],
    )


@app.post(
    "/v1/users/{user_id}/facts/extract-from-resume",
    response_model=FactImportResultResponse,
)
async def extract_facts_from_resume(
    user_id: str,
    request: ExtractFromResumeRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> FactImportResultResponse:
    """Extract facts from an existing resume using LLM."""
    from app.llm.preferences import InvalidLlmPreference
    from app.services.fact_ingestion import FactIngestionService

    settings = get_settings()
    async with httpx.AsyncClient(timeout=120, follow_redirects=False, trust_env=False) as client:
        try:
            providers = build_user_model_providers(client, session, user_id, settings)
        except InvalidLlmPreference as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if not providers:
            raise HTTPException(
                status_code=503,
                detail=(
                    "No LLM provider configured. Set one in the Model tab or via "
                    "APP_GEMINI_API_KEY / APP_ANTHROPIC_API_KEY."
                ),
            )
        router = ModelRouter(providers)
        prompt_registry = PromptRegistry.load(
            Path(__file__).parents[2] / "prompts" / "registry.json"
        )
        service = FactIngestionService(session, router, prompt_registry)
        try:
            result = await service.extract_from_resume(
                user_id,
                request.cv_file_id,
                force=request.force,
                auto_accept=request.auto_accept,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    await RagSyncService(session, settings).sync_profile(user_id)
    return FactImportResultResponse(
        batch_id=result.batch_id,
        source_type=result.source_type,
        facts_created=result.facts_created,
        facts_merged=result.facts_merged,
        facts_skipped=result.facts_skipped,
        facts_rejected=result.facts_rejected,
        candidates=[
            {
                "name": c.name,
                "category": c.category,
                "value": c.value,
                "confidence": c.confidence,
                "source_text": c.source_text,
                "duplicate_status": c.duplicate_status.value,
            }
            for c in result.candidates[:50]
        ],
    )


@app.get(
    "/v1/users/{user_id}/fact-import-batches",
    response_model=list[FactImportBatchResponse],
)
def list_fact_import_batches(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> list[FactImportBatchResponse]:
    """List fact import batches for a user."""
    from app.services.fact_ingestion import FactIngestionService

    service = FactIngestionService(
        session,
        ModelRouter(()),  # No LLM needed for listing
        PromptRegistry.load(Path(__file__).parents[2] / "prompts" / "registry.json"),
    )
    batches = service.list_batches(user_id)
    return [
        FactImportBatchResponse(
            id=b.id,
            user_id=b.user_id,
            source_type=b.source_type,
            source_id=b.source_id,
            source_filename=b.source_filename,
            extractor_version=b.extractor_version,
            facts_created=b.facts_created,
            facts_merged=b.facts_merged,
            facts_skipped=b.facts_skipped,
            facts_rejected=b.facts_rejected,
            status=b.status,
            created_at=b.created_at,
        )
        for b in batches
    ]


@app.post(
    "/v1/users/{user_id}/fact-import-batches/{batch_id}/undo",
)
def undo_fact_import_batch(
    user_id: str,
    batch_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> dict[str, object]:
    """Undo a fact import batch."""
    from app.services.fact_ingestion import FactIngestionService

    service = FactIngestionService(
        session,
        ModelRouter(()),
        PromptRegistry.load(Path(__file__).parents[2] / "prompts" / "registry.json"),
    )
    try:
        removed = service.undo_batch(user_id, batch_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"batch_id": batch_id, "facts_marked_superseded": removed}


@app.post(
    "/v1/applications/{application_id}/recalculate-match",
    response_model=WorkflowTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def recalculate_application_match(
    application_id: str,
    session: Annotated[Session, Depends(session_scope)],
    force: bool = False,
) -> WorkflowTaskResponse:
    settings = get_settings()
    if not settings.matching_v2_enabled:
        raise HTTPException(
            status_code=409,
            detail="Matching v2 is disabled; set APP_MATCHING_V2_ENABLED=true",
        )
    try:
        task = MatchingJobService(session).schedule(application_id, force=force)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except MatchingJobNotReadyError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _workflow_task_response(task, session)


@app.post(
    "/v1/users/{user_id}/cv-files",
    response_model=CvFileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_cv_file(
    user_id: str,
    file: Annotated[UploadFile, File()],
    session: Annotated[Session, Depends(session_scope)],
    storage: Annotated[DocumentStorage, Depends(document_storage)],
) -> CvFileResponse:
    settings = get_settings()
    content = await file.read(settings.max_document_bytes + 1)
    saved_document = None
    try:
        saved_document = storage.save(
            file.filename or "",
            file.content_type or "application/octet-stream",
            content,
        )
        cv_file = RecruitmentService(session).add_cv_file(user_id, saved_document)
    except InvalidDocumentError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except EntityNotFoundError as error:
        if saved_document is not None:
            saved_document.storage_path.unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError as error:
        if saved_document is not None:
            saved_document.storage_path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail=str(error)) from error
    if saved_document is not None and str(cv_file.storage_path) != str(saved_document.storage_path):
        # add_cv_file() returned a pre-existing record for identical bytes (idempotent re-upload)
        # rather than the one we just wrote to disk — remove the now-orphaned duplicate file.
        saved_document.storage_path.unlink(missing_ok=True)
    user = session.get(UserRow, user_id)
    return serialize_cv_file(
        cv_file,
        active_cv_file_id=user.active_cv_file_id if user is not None else None,
        rag_status=resume_rag_status(session, cv_file.id),
    )


@app.get("/v1/users/{user_id}/cv-files", response_model=list[CvFileResponse])
def list_cv_files(
    user_id: str, session: Annotated[Session, Depends(session_scope)]
) -> list[CvFileResponse]:
    service = RecruitmentService(session)
    try:
        cv_files = service.list_cv_files(user_id)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    user = session.get(UserRow, user_id)
    active_cv_file_id = user.active_cv_file_id if user is not None else None
    return [
        serialize_cv_file(
            cv_file,
            active_cv_file_id=active_cv_file_id,
            rag_status=resume_rag_status(session, cv_file.id),
        )
        for cv_file in cv_files
    ]


@app.delete(
    "/v1/users/{user_id}/cv-files/{cv_file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_cv_file(
    user_id: str,
    cv_file_id: str,
    session: Annotated[Session, Depends(session_scope)],
    storage: Annotated[DocumentStorage, Depends(document_storage)],
) -> Response:
    try:
        stored_path = RecruitmentService(session).delete_cv_file(user_id, cv_file_id)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    ResumeRagJobService(session).delete_for_cv(cv_file_id)
    await RagSyncService(session, get_settings()).delete_resume(user_id, cv_file_id)
    storage.delete(stored_path)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.put("/v1/users/{user_id}/active-cv-file", response_model=CvFileResponse)
def select_active_cv_file(
    user_id: str,
    request: ActiveCvFileRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> CvFileResponse:
    try:
        cv_file = RecruitmentService(session).set_active_cv_file(user_id, request.cv_file_id)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return serialize_cv_file(
        cv_file,
        active_cv_file_id=cv_file.id,
        rag_status=resume_rag_status(session, cv_file.id),
    )


@app.post(
    "/v1/users/{user_id}/cv-files/{cv_file_id}/extract-profile",
    response_model=ExtractedProfileResponse,
)
async def extract_profile_from_cv(
    user_id: str,
    cv_file_id: str,
    session: Annotated[Session, Depends(session_scope)],
    http_client: Annotated[httpx.AsyncClient, Depends(headhunter_http_client)],
    storage: Annotated[DocumentStorage, Depends(document_storage)],
) -> ExtractedProfileResponse:
    """Draft skills/summary/search keywords from an uploaded resume. Writes nothing yet.

    The result is a draft for the person to review and edit; call
    ``PUT /v1/users/{user_id}/cv-files/{cv_file_id}/profile`` to save it on this resume.
    Requires a configured per-user or environment LLM provider.
    """
    settings = get_settings()
    try:
        providers = build_user_model_providers(http_client, session, user_id, settings)
    except InvalidLlmPreference as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if not providers:
        raise HTTPException(
            status_code=503,
            detail=(
                "Resume analysis is unavailable: set APP_ANTHROPIC_API_KEY or APP_GEMINI_API_KEY"
            ),
        )
    cv_file = session.get(CvFileRow, cv_file_id)
    if cv_file is None or cv_file.user_id != user_id:
        raise HTTPException(status_code=404, detail="CV file not found for this user")
    try:
        content = storage.resolve(cv_file.storage_path).read_bytes()
        extension = Path(cv_file.original_filename).suffix
        resume_text = extract_resume_text(content, extension=extension)
    except (OSError, UnreadableResumeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        draft = await ResumeIntakeService(ModelRouter(providers)).draft_profile(resume_text)
    except NoModelAvailableError as error:
        raise HTTPException(status_code=502, detail=f"Resume analysis failed: {error}") from error
    return ExtractedProfileResponse(
        skills=draft.skills,
        experience_summary=draft.experience_summary,
        search_keywords=draft.search_keywords,
        years_of_experience=draft.years_of_experience,
    )


@app.put(
    "/v1/users/{user_id}/cv-files/{cv_file_id}/profile",
    response_model=CvFileResponse,
)
async def confirm_resume_profile(
    user_id: str,
    cv_file_id: str,
    request: ConfirmResumeProfileRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> CvFileResponse:
    """Persist reviewed skills and search data on one CV and select it for later searches."""
    try:
        cv_file = RecruitmentService(session).save_cv_profile(
            user_id,
            cv_file_id,
            skills=request.skills,
            experience_summary=request.experience_summary,
            search_keywords=request.search_keywords,
            years_of_experience=request.years_of_experience,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    settings = get_settings()
    if (
        settings.rag_enabled
        and settings.rag_service_url
        and settings.rag_api_key
        and settings.rag_project_id
    ):
        ResumeRagJobService(session).schedule(cv_file.id)
    return serialize_cv_file(
        cv_file,
        active_cv_file_id=cv_file.id,
        rag_status=resume_rag_status(session, cv_file.id),
    )


@app.post(
    "/v1/users/{user_id}/cv-files/{cv_file_id}/rag-sync",
    response_model=ResumeRagSyncResponse,
)
async def sync_resume_to_rag(
    user_id: str,
    cv_file_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> ResumeRagSyncResponse:
    """Manually retry RAG indexing for one reviewed resume owned by the user."""
    try:
        cv_file = RecruitmentService(session).get_cv_file(user_id, cv_file_id)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if cv_file.analyzed_at is None:
        raise HTTPException(
            status_code=409,
            detail="Confirm the resume profile before sending it to RAG",
        )

    settings = get_settings()
    if not (
        settings.rag_enabled
        and settings.rag_service_url
        and settings.rag_api_key
        and settings.rag_project_id
    ):
        raise HTTPException(status_code=503, detail="RAG synchronization is not configured")

    try:
        task = ResumeRagJobService(session).schedule(cv_file.id, force=True)
    except ResumeRagJobNotReadyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    task_status = ResumeRagJobService(session).status(cv_file.id)
    if task_status.task_id is None or task_status.updated_at is None:
        raise HTTPException(status_code=500, detail="RAG synchronization task was not persisted")
    return ResumeRagSyncResponse(
        task_id=task.id,
        status=task_status.status,
        attempt_number=task_status.attempt_number,
        failure_code=task_status.failure_code,
        updated_at=task_status.updated_at,
        synced_at=task_status.synced_at,
    )


@app.post(
    "/v1/users/{user_id}/confirm-profile-facts",
    response_model=list[ConfirmedProfileFactResponse],
)
async def confirm_profile_facts(
    user_id: str,
    request: ConfirmProfileFactsRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> list[ConfirmedProfileFactResponse]:
    """Save reviewer-confirmed skills/summary as VERIFIED profile facts.

    This is the human-confirmation step for AI-drafted resume analysis: nothing from
    ``extract-profile`` is ever saved automatically. Only what is submitted here becomes usable
    for vacancy matching, screening-answer autofill, and cover-letter drafting.
    """
    service = RecruitmentService(session)
    saved: list[ConfirmedProfileFactResponse] = []
    try:
        for skill in dict.fromkeys(s.strip() for s in request.skills if s.strip()):
            fact = service.add_profile_fact(
                user_id, category="skill", name=skill, value="", is_verified=True
            )
            saved.append(ConfirmedProfileFactResponse.model_validate(fact, from_attributes=True))
        if request.experience_summary.strip():
            fact = service.add_profile_fact(
                user_id,
                category="experience_summary",
                name="experience_summary",
                value=request.experience_summary.strip(),
                is_verified=True,
            )
            saved.append(ConfirmedProfileFactResponse.model_validate(fact, from_attributes=True))
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError:
        pass  # re-confirming an already-saved fact is a harmless no-op, not an error
    await RagSyncService(session, get_settings()).sync_profile(user_id)
    return saved


@app.post(
    "/v1/users/{user_id}/discover-headhunter-vacancies",
    response_model=list[DiscoveryOutcomeResponse],
)
async def discover_headhunter_vacancies(
    user_id: str,
    request: DiscoverHeadHunterVacanciesRequest,
    session: Annotated[Session, Depends(session_scope)],
    http_client: Annotated[httpx.AsyncClient, Depends(headhunter_http_client)],
) -> list[DiscoveryOutcomeResponse]:
    """Search hh.ru by the candidate's verified skills and stage results as awaiting_review.

    Uses a real browser and the user's encrypted hh.ru session rather than the unavailable
    job-seeker API. Only creates vacancies/applications for human review.
    """
    settings = get_settings()
    browser_client = BrowserWorkerClient(settings.browser_worker_url)
    adapter = HttpHeadHunterAdapter(browser_client, user_id)
    try:
        outcomes = await JobDiscoveryService(session).discover_headhunter_vacancies(
            user_id,
            headhunter_adapter=adapter,
            locations=request.locations,
            limit=request.limit,
            search_text=request.search_text,
            cv_file_id=request.cv_file_id,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NoSearchKeywordsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    providers = build_user_model_providers(http_client, session, user_id, settings)
    if providers:
        materials_router = ModelRouter(providers)
        materials_service = MaterialsGenerationService(session, materials_router)
        for outcome in outcomes:
            if not materials_service.needs_material_refresh(outcome.application_id):
                continue
            try:
                await materials_service.draft_materials(
                    outcome.application_id,
                    replace_mismatched_cover_letter=True,
                )
            except Exception:  # noqa: BLE001 - a drafting failure must not fail the whole search
                continue
    return serialize_discovery_outcomes(outcomes)


@app.post(
    "/v1/users/{user_id}/discover-linkedin-vacancies",
    response_model=list[DiscoveryOutcomeResponse],
)
async def discover_linkedin_vacancies(
    user_id: str,
    request: DiscoverLinkedInVacanciesRequest,
    session: Annotated[Session, Depends(session_scope)],
    http_client: Annotated[httpx.AsyncClient, Depends(headhunter_http_client)],
) -> list[DiscoveryOutcomeResponse]:
    """Search LinkedIn's job search page (native Easy Apply jobs only) via browser automation.

    Requires APP_ENABLE_LINKEDIN_APPLY=true (the same explicit-risk opt-in used for real
    submission) and a session saved from the personal dashboard. Only creates
    vacancies/applications for human review — never schedules a real submission.
    """
    settings = get_settings()
    if not settings.enable_linkedin_apply:
        raise HTTPException(
            status_code=403,
            detail="LinkedIn browser automation is disabled (APP_ENABLE_LINKEDIN_APPLY=false)",
        )
    browser_client = BrowserWorkerClient(settings.browser_worker_url)
    adapter = HttpLinkedInAdapter(browser_client, user_id)
    try:
        outcomes = await JobDiscoveryService(session).discover_linkedin_vacancies(
            user_id,
            linkedin_adapter=adapter,
            locations=request.locations,
            limit=request.limit,
            search_text=request.search_text,
            cv_file_id=request.cv_file_id,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NoSearchKeywordsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LinkedInSessionRequiredError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    providers = build_user_model_providers(http_client, session, user_id, settings)
    if providers:
        materials_router = ModelRouter(providers)
        materials_service = MaterialsGenerationService(session, materials_router)
        for outcome in outcomes:
            if not materials_service.needs_material_refresh(outcome.application_id):
                continue
            try:
                await materials_service.draft_materials(
                    outcome.application_id,
                    replace_mismatched_cover_letter=True,
                )
            except Exception:  # noqa: BLE001 - a drafting failure must not fail the whole search
                continue
    return serialize_discovery_outcomes(outcomes)


@app.post(
    "/v1/users/{user_id}/discover-greenhouse-vacancies",
    response_model=list[DiscoveryOutcomeResponse],
)
async def discover_greenhouse_vacancies(
    user_id: str,
    request: DiscoverGreenhouseVacanciesRequest,
    session: Annotated[Session, Depends(session_scope)],
    http_client: Annotated[httpx.AsyncClient, Depends(greenhouse_http_client)],
) -> list[DiscoveryOutcomeResponse]:
    """Search known or explicitly supplied Greenhouse company boards through their public API."""
    try:
        outcomes = await JobDiscoveryService(session).discover_greenhouse_vacancies(
            user_id,
            greenhouse_adapter=GreenhouseJobBoardApi(http_client),
            board_urls=[str(board_url) for board_url in request.board_urls],
            locations=request.locations,
            limit=request.limit,
            search_text=request.search_text,
            cv_file_id=request.cv_file_id,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NoSearchKeywordsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except GreenhouseDiscoveryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    settings = get_settings()
    providers = build_user_model_providers(http_client, session, user_id, settings)
    if providers:
        materials_service = MaterialsGenerationService(session, ModelRouter(providers))
        for outcome in outcomes:
            if not materials_service.needs_material_refresh(outcome.application_id):
                continue
            try:
                await materials_service.draft_materials(
                    outcome.application_id,
                    replace_mismatched_cover_letter=True,
                )
            except Exception:  # noqa: BLE001 - drafting failure must not discard the vacancy
                continue
    return serialize_discovery_outcomes(outcomes)


@app.post("/v1/users/{user_id}/discover-vacancies-stream")
async def discover_vacancies_stream(
    user_id: str,
    request: DiscoverVacanciesStreamRequest,
) -> StreamingResponse:
    """Stream each persisted vacancy as newline-delimited JSON."""
    with SessionFactory() as session:
        if session.get(UserRow, user_id) is None:
            raise HTTPException(status_code=404, detail="User not found")

    settings = get_settings()
    requested_sources = tuple(dict.fromkeys(request.sources))
    discovery_limit = min(50, request.limit * 3) if request.direct_rerank else request.limit
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    enrichment_semaphore = asyncio.Semaphore(3 if request.direct_rerank else 1)

    async def publish_outcome(
        source: str,
        outcome: DiscoveryOutcome,
        enrichment: dict[str, object] | None = None,
    ) -> None:
        item = serialize_discovery_outcome(outcome)
        if enrichment:
            item.update(enrichment)
        await queue.put(
            {
                "event": "vacancy",
                "source": source,
                "item": item,
            }
        )

    async def enrich_outcome(
        source: str,
        application_id: str,
        *,
        emit_events: bool = True,
    ) -> dict[str, object]:
        enrichment: dict[str, object] = {
            "matching_status": "processing",
            "materials_status": "processing",
        }

        async def emit(event: dict[str, object]) -> None:
            if emit_events:
                await queue.put(event)

        matching_was_scheduled = False

        async def schedule_matching(*, force: bool) -> None:
            nonlocal matching_was_scheduled
            if not settings.matching_v2_enabled:
                return
            try:
                with SessionFactory() as matching_session:
                    MatchingJobService(matching_session).schedule(
                        application_id,
                        force=force,
                        priority=BACKGROUND_MATCHING_PRIORITY,
                    )
                matching_was_scheduled = True
            except Exception as error:  # noqa: BLE001 - legacy score remains available
                enrichment.update(
                    matching_status="error",
                    matching_error=str(error),
                )
                await emit(
                    {
                        "event": "matching_error",
                        "source": source,
                        "application_id": application_id,
                        "error": str(error),
                    }
                )

        async with enrichment_semaphore:
            await emit(
                {
                    "event": "enrichment_started",
                    "source": source,
                    "application_id": application_id,
                }
            )
            if not request.direct_rerank:
                await schedule_matching(force=False)

            # Direct mode requires RAG ingestion to finish before matching is scheduled.
            if settings.rag_enabled:
                try:
                    from app.matching.rag_client import create_rag_client
                    from app.services.profile_rag_ingestion import ProfileRagIngestionService
                    from app.services.vacancy_rag_ingestion import VacancyRagIngestionService

                    rag = create_rag_client(
                        service_url=settings.rag_service_url,
                        api_key=(
                            settings.rag_api_key.get_secret_value()
                            if settings.rag_api_key
                            else None
                        ),
                        project_id=settings.rag_project_id,
                        collection="vacancies",
                        timeout_seconds=settings.rag_timeout_seconds,
                        enabled=True,
                    )
                    with SessionFactory() as rag_session:
                        app_row = rag_session.get(ApplicationRow, application_id)
                        if app_row is not None:
                            if request.direct_rerank:
                                profile_ingestion = ProfileRagIngestionService(rag_session, rag)
                                await profile_ingestion.ingest_profile(app_row.user_id)
                                if app_row.selected_cv_file_id is not None:
                                    await profile_ingestion.ingest_cv(app_row.selected_cv_file_id)
                            result = await VacancyRagIngestionService(
                                rag_session, rag
                            ).ingest_vacancy(
                                app_row.vacancy_id,
                                owner_user_id=app_row.user_id,
                            )
                            if result:
                                logger.info(
                                    "rag_vacancy_ingested",
                                    application_id=application_id,
                                    document_id=result.document_id,
                                    status=result.status,
                                )
                            else:
                                logger.warning(
                                    "rag_vacancy_ingest_skipped",
                                    application_id=application_id,
                                )
                # RAG ingestion failure must not block vacancy discovery.
                except Exception as rag_error:  # noqa: BLE001
                    logger.warning(
                        "rag_ingestion_failed",
                        application_id=application_id,
                        error=str(rag_error)[:200],
                    )

            if request.direct_rerank:
                await schedule_matching(force=True)

            try:
                async with httpx.AsyncClient(
                    timeout=60, follow_redirects=False, trust_env=False
                ) as client:
                    with SessionFactory() as materials_session:
                        providers = build_user_model_providers(
                            client, materials_session, user_id, settings
                        )
                        if not providers:
                            raise RuntimeError("LLM is not configured")
                        materials_service = MaterialsGenerationService(
                            materials_session, ModelRouter(providers)
                        )
                        if materials_service.needs_material_refresh(application_id):
                            generated = await materials_service.draft_materials(
                                application_id,
                                replace_mismatched_cover_letter=True,
                            )
                            cover_letter_text = generated.cover_letter_text
                        else:
                            application = materials_session.get(ApplicationRow, application_id)
                            cover_letter_text = (
                                application.cover_letter_text if application is not None else ""
                            )
                enrichment.update(
                    materials_status="ready",
                    cover_letter_text=cover_letter_text,
                )
                await emit(
                    {
                        "event": "materials_ready",
                        "source": source,
                        "application_id": application_id,
                        "cover_letter_text": cover_letter_text,
                    }
                )
            except Exception as error:  # noqa: BLE001 - matching and search must continue
                enrichment.update(
                    materials_status="error",
                    materials_error=str(error),
                )
                await emit(
                    {
                        "event": "materials_error",
                        "source": source,
                        "application_id": application_id,
                        "error": str(error),
                    }
                )

            if matching_was_scheduled:
                for _ in range(60):
                    with SessionFactory() as poll_session:
                        aggregate = poll_session.get(ApplicationMatchResultRow, application_id)
                        application = poll_session.get(ApplicationRow, application_id)
                        if aggregate is not None and aggregate.status in {
                            "scored",
                            "degraded",
                            "failed",
                        }:
                            score = (
                                round(aggregate.final_score)
                                if aggregate.status == "scored"
                                else application.match_score
                                if application is not None
                                else 0
                            )
                            enrichment.update(
                                matching_status=aggregate.status,
                                match_score=score,
                            )
                            await emit(
                                {
                                    "event": "matching_ready",
                                    "source": source,
                                    "application_id": application_id,
                                    "match_score": score,
                                    "matching_status": aggregate.status,
                                }
                            )
                            break
                    await asyncio.sleep(1)
                else:
                    enrichment.update(
                        matching_status="error",
                        matching_error="Detailed matching is still queued",
                    )
                    await emit(
                        {
                            "event": "matching_error",
                            "source": source,
                            "application_id": application_id,
                            "error": "Detailed matching is still queued",
                        }
                    )
            else:
                with SessionFactory() as score_session:
                    application = score_session.get(ApplicationRow, application_id)
                    score = application.match_score if application is not None else 0
                enrichment.update(
                    matching_status="legacy",
                    match_score=score,
                )
                await emit(
                    {
                        "event": "matching_ready",
                        "source": source,
                        "application_id": application_id,
                        "match_score": score,
                        "matching_status": "legacy",
                    }
                )
            return enrichment

    async def run_source(source: str) -> None:
        enrichment_tasks: list[asyncio.Task[dict[str, object]]] = []
        direct_results: list[tuple[DiscoveryOutcome, dict[str, object]]] = []
        direct_tasks: list[tuple[DiscoveryOutcome, asyncio.Task[dict[str, object]]]] = []
        try:
            with SessionFactory() as source_session:
                service = JobDiscoveryService(source_session)

                async def on_outcome(outcome: DiscoveryOutcome) -> None:
                    if request.direct_rerank:
                        direct_tasks.append(
                            (
                                outcome,
                                asyncio.create_task(
                                    enrich_outcome(
                                        source,
                                        outcome.application_id,
                                        emit_events=False,
                                    )
                                ),
                            )
                        )
                    else:
                        await publish_outcome(source, outcome)
                        enrichment_tasks.append(
                            asyncio.create_task(enrich_outcome(source, outcome.application_id))
                        )

                if source == "greenhouse":
                    async with httpx.AsyncClient(
                        timeout=30, follow_redirects=False, trust_env=False
                    ) as client:
                        await service.discover_greenhouse_vacancies(
                            user_id,
                            greenhouse_adapter=GreenhouseJobBoardApi(client),
                            board_urls=[str(board_url) for board_url in request.board_urls],
                            locations=request.locations,
                            limit=discovery_limit,
                            search_text=request.search_text,
                            cv_file_id=request.cv_file_id,
                            on_outcome=on_outcome,
                        )
                else:
                    if source == "linkedin" and not settings.enable_linkedin_apply:
                        raise LinkedInSessionRequiredError(
                            "LinkedIn browser automation is disabled"
                        )
                    browser_client = BrowserWorkerClient(settings.browser_worker_url)
                    if source == "headhunter":
                        headhunter_adapter = HttpHeadHunterAdapter(browser_client, user_id)
                    else:
                        linkedin_adapter = HttpLinkedInAdapter(browser_client, user_id)
                    if source == "headhunter":
                        await service.discover_headhunter_vacancies(
                            user_id,
                            headhunter_adapter=headhunter_adapter,
                            locations=request.locations,
                            limit=discovery_limit,
                            search_text=request.search_text,
                            cv_file_id=request.cv_file_id,
                            on_outcome=on_outcome,
                        )
                    else:
                        await service.discover_linkedin_vacancies(
                            user_id,
                            linkedin_adapter=linkedin_adapter,
                            locations=request.locations,
                            limit=discovery_limit,
                            search_text=request.search_text,
                            cv_file_id=request.cv_file_id,
                            on_outcome=on_outcome,
                        )
                if request.direct_rerank:
                    enrichments = await asyncio.gather(*(task for _outcome, task in direct_tasks))
                    direct_results.extend(
                        (outcome, enrichment)
                        for (outcome, _task), enrichment in zip(
                            direct_tasks,
                            enrichments,
                            strict=True,
                        )
                    )
                    direct_results.sort(
                        key=lambda item: (
                            float(raw_score)
                            if isinstance(
                                (raw_score := item[1].get("match_score", 0)),
                                str | int | float,
                            )
                            else 0.0
                        ),
                        reverse=True,
                    )
                    for outcome, enrichment in direct_results[: request.limit]:
                        await publish_outcome(source, outcome, enrichment)
        except Exception as error:  # noqa: BLE001 - one source must not end the whole stream
            await queue.put(
                {"event": "source_error", "source": source, "error": type(error).__name__}
            )
        finally:
            await queue.put({"event": "source_complete", "source": source})
            if enrichment_tasks:
                await asyncio.gather(*enrichment_tasks, return_exceptions=True)
            unfinished_direct_tasks = [task for _outcome, task in direct_tasks if not task.done()]
            if unfinished_direct_tasks:
                await asyncio.gather(*unfinished_direct_tasks, return_exceptions=True)
            await queue.put({"event": "_source_finished", "source": source})

    async def event_stream() -> AsyncIterator[bytes]:
        tasks = [asyncio.create_task(run_source(source)) for source in requested_sources]
        finished_sources = 0
        try:
            while finished_sources < len(tasks):
                event = await queue.get()
                if event["event"] == "_source_finished":
                    finished_sources += 1
                    continue
                yield (json.dumps(event, ensure_ascii=False) + "\n").encode()
            await asyncio.gather(*tasks)
            yield (
                json.dumps(
                    {"event": "complete", "sources_completed": finished_sources},
                    ensure_ascii=False,
                )
                + "\n"
            ).encode()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/vacancies", response_model=VacancyResponse, status_code=status.HTTP_201_CREATED)
def create_vacancy(
    request: VacancyRequest, session: Annotated[Session, Depends(session_scope)]
) -> VacancyResponse:
    try:
        vacancy = RecruitmentService(session).create_vacancy(
            source_url=str(request.source_url),
            title=request.title,
            company=request.company,
            required_skills=sorted(request.required_skills),
            preferred_skills=sorted(request.preferred_skills),
        )
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return VacancyResponse.model_validate(vacancy, from_attributes=True)


@app.post(
    "/v1/vacancies/import-greenhouse",
    response_model=ImportedVacancyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_greenhouse_vacancy(
    request: GreenhouseImportRequest,
    session: Annotated[Session, Depends(session_scope)],
    http_client: Annotated[httpx.AsyncClient, Depends(greenhouse_http_client)],
) -> ImportedVacancyResponse:
    try:
        extracted_job = await GreenhouseJobBoardApi(http_client).extract_job(
            str(request.source_url)
        )
        vacancy = RecruitmentService(session).create_vacancy(
            source_url=extracted_job.source_url,
            title=extracted_job.title,
            company=extracted_job.company,
            required_skills=[],
            preferred_skills=[],
            location=extracted_job.location,
            description_text=extracted_job.description_text,
            adapter_name="greenhouse",
            source_evidence_url=extracted_job.evidence_api_url,
            application_fields=[asdict(field) for field in extracted_job.form_fields],
            requires_sensitive_review=extracted_job.requires_sensitive_review,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail="Greenhouse API unavailable") from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ImportedVacancyResponse.model_validate(vacancy, from_attributes=True)


@app.post(
    "/v1/vacancies/import-headhunter",
    response_model=ImportedVacancyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_headhunter_vacancy(
    request: HeadHunterImportRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> ImportedVacancyResponse:
    """Import one hh.ru vacancy by URL using the browser-worker (not api.hh.ru).

    api.hh.ru's anonymous access is CAPTCHA-limited in practice, so extraction reads the public
    vacancy page directly through Chromium instead — no login/session is required for this.
    """
    settings = get_settings()
    browser_client = BrowserWorkerClient(settings.browser_worker_url)
    try:
        data = await browser_client.extract_headhunter(
            user_id="anonymous", url=str(request.source_url)
        )
        source_url = required_mapping_string(data, "source_url", default=str(request.source_url))
        title = required_mapping_string(data, "title")
        company = required_mapping_string(data, "company")
        location = required_mapping_string(data, "location")
        description_text = required_mapping_string(data, "description_text")
        required_skills = required_mapping_string_list(data, "required_skills")
        requires_sensitive_review = data.get("requires_sensitive_review", False)
        if not isinstance(requires_sensitive_review, bool):
            raise ValueError("Browser worker returned invalid sensitive-review flag")
        vacancy = RecruitmentService(session).create_vacancy(
            source_url=source_url,
            title=title,
            company=company,
            required_skills=required_skills,
            preferred_skills=[],
            location=location,
            description_text=description_text,
            adapter_name="headhunter",
            source_evidence_url=source_url,
            application_fields=[],
            requires_sensitive_review=requires_sensitive_review,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (RuntimeError, ApplyBlocked) as error:
        raise HTTPException(
            status_code=502, detail=f"Could not read hh.ru vacancy: {error}"
        ) from error
    except CaptchaChallenge as error:
        raise HTTPException(
            status_code=503, detail="hh.ru presented a verification checkpoint; try again shortly"
        ) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ImportedVacancyResponse.model_validate(vacancy, from_attributes=True)


@app.post(
    "/v1/vacancies/import-linkedin-reference",
    response_model=ImportedVacancyResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_linkedin_reference(
    request: LinkedInReferenceImportRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> ImportedVacancyResponse:
    try:
        reference = LinkedInJobReference.from_url(str(request.source_url))
        vacancy = RecruitmentService(session).create_vacancy(
            source_url=reference.source_url,
            title=request.title,
            company=request.company,
            required_skills=[],
            preferred_skills=[],
            location=request.location,
            description_text=request.description_text,
            adapter_name="linkedin-reference",
            source_evidence_url=reference.source_url,
            application_fields=[],
            requires_sensitive_review=False,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ImportedVacancyResponse.model_validate(vacancy, from_attributes=True)


@app.post(
    "/v1/applications/prepare",
    response_model=ApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def prepare_application(
    request: PrepareApplicationRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> ApplicationResponse:
    try:
        application = RecruitmentService(session).prepare_application(
            request.user_id, request.vacancy_id, request.cv_file_id
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ApplicationResponse.model_validate(application, from_attributes=True)


@app.get(
    "/v1/applications/{application_id}/match-details",
    response_model=ApplicationMatchDetailsResponse,
)
def application_match_details(
    application_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> ApplicationMatchDetailsResponse:
    application = session.get(ApplicationRow, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    # Ownership validation: application must belong to a known user
    if application.user_id is None:
        raise HTTPException(status_code=404, detail="Application not found")
    aggregate = session.get(ApplicationMatchResultRow, application_id)
    if aggregate is None:
        raise HTTPException(status_code=404, detail="Match details have not been calculated")
    rows = session.execute(
        select(RequirementMatchRow, VacancyRequirementRow, CandidateEvidenceRow)
        .join(
            VacancyRequirementRow,
            VacancyRequirementRow.id == RequirementMatchRow.requirement_id,
        )
        .outerjoin(
            CandidateEvidenceRow,
            CandidateEvidenceRow.id == RequirementMatchRow.evidence_id,
        )
        .where(RequirementMatchRow.application_id == application_id)
        .order_by(VacancyRequirementRow.created_at, VacancyRequirementRow.id)
    ).all()
    requirement_details = [
        RequirementMatchDetailResponse(
            requirement_id=requirement.id,
            requirement_text=requirement.requirement_text,
            requirement_type=requirement.requirement_type,
            importance=requirement.importance,
            is_blocker=requirement.is_blocker,
            source_fragment=requirement.source_fragment,
            evidence_id=evidence.id if evidence is not None else None,
            evidence_text=evidence.evidence_text if evidence is not None else None,
            evidence_experience_level=(evidence.experience_level if evidence is not None else None),
            evidence_source_fragment=(evidence.source_fragment if evidence is not None else None),
            lexical_score=requirement_match.lexical_score,
            dense_score=requirement_match.dense_score,
            hybrid_score=requirement_match.hybrid_score,
            reranker_raw_score=requirement_match.reranker_raw_score,
            reranker_score=requirement_match.reranker_score,
            final_match_score=requirement_match.final_match_score,
            match_level=requirement_match.match_level,
            explanation=requirement_match.explanation,
            retrieval_model_versions=requirement_match.retrieval_model_versions_json,
            entailment_relation=requirement_match.entailment_relation,
            evidence_strength=requirement_match.evidence_strength,
            is_hard_blocker=requirement_match.is_hard_blocker,
        )
        for requirement_match, requirement, evidence in rows
    ]
    return ApplicationMatchDetailsResponse(
        application_id=application.id,
        cv_file_id=aggregate.cv_file_id,
        legacy_match_score=application.match_score,
        status=aggregate.status,
        run_id=aggregate.run_id,
        eligibility_status=aggregate.eligibility_status,
        final_score=aggregate.final_score,
        hard_skill_score=aggregate.hard_skill_score,
        preferred_skill_score=aggregate.preferred_skill_score,
        role_score=aggregate.role_score,
        seniority_score=aggregate.seniority_score,
        experience_score=aggregate.experience_score,
        work_format_score=aggregate.work_format_score,
        location_score=aggregate.location_score,
        domain_score=aggregate.domain_score,
        language_score=aggregate.language_score,
        semantic_similarity=aggregate.semantic_similarity,
        reranker_score=aggregate.reranker_score,
        requirements_match=aggregate.requirements_match,
        blocker_count=aggregate.blocker_count,
        matched_required_count=aggregate.matched_required_count,
        missing_required_count=aggregate.missing_required_count,
        scoring_version=aggregate.scoring_version,
        model_versions=aggregate.model_versions_json,
        explanation=aggregate.explanation_json,
        fallback_reason=aggregate.fallback_reason,
        failure_reason=aggregate.failure_reason,
        started_at=aggregate.started_at,
        calculated_at=aggregate.calculated_at,
        requirements=requirement_details,
        required_score=aggregate.required_score,
        preferred_score=aggregate.preferred_score,
        bonus_score=aggregate.bonus_score,
        confidence=aggregate.confidence,
        raw_score_before_blockers=float(
            cast(Any, aggregate.explanation_json.get("raw_score_before_blockers", 0))
        ),
        blocker_penalty=float(cast(Any, aggregate.explanation_json.get("blocker_penalty", 0))),
        calibration_version=str(aggregate.explanation_json.get("calibration_version", "identity")),
        recommendations=list(cast(Any, aggregate.explanation_json.get("recommendations", []))),
        hard_blockers=list(cast(Any, aggregate.explanation_json.get("hard_blockers", []))),
        hard_blockers_unresolved=list(
            cast(Any, aggregate.explanation_json.get("hard_blockers_unresolved", []))
        ),
    )


@app.post(
    "/v1/applications/{application_id}/prepare-browser-review",
    response_model=WorkflowTaskResponse,
)
def prepare_browser_review(
    application_id: str,
    request: BrowserReviewRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> WorkflowTaskResponse:
    del request  # The explicit confirmation is validated but never placed in the browser payload.
    try:
        task = RecruitmentService(session).schedule_greenhouse_browser_review(application_id)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _workflow_task_response(task, session)


@app.post(
    "/v1/applications/{application_id}/apply-headhunter",
    response_model=WorkflowTaskResponse,
)
def apply_headhunter(
    application_id: str,
    request: BrowserApplySubmitRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> WorkflowTaskResponse:
    """Schedule a REAL hh.ru application submission via browser automation.

    Requires APP_ENABLE_HEADHUNTER_APPLY=true and a browser session captured through
    scripts/browser_login_capture.py. hh.ru's terms prohibit automated page interaction; this
    endpoint exists because the operator explicitly opted in, not by default.
    """
    del request
    settings = get_settings()
    if not settings.enable_headhunter_apply:
        raise HTTPException(
            status_code=403,
            detail="hh.ru real-submission apply is disabled (APP_ENABLE_HEADHUNTER_APPLY=false)",
        )
    try:
        task = RecruitmentService(session).schedule_real_submission_apply(
            application_id,
            adapter_name="headhunter",
            workflow="headhunter_apply",
            site_key="headhunter",
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _workflow_task_response(task, session)


@app.post(
    "/v1/applications/{application_id}/apply-linkedin",
    response_model=WorkflowTaskResponse,
)
def apply_linkedin(
    application_id: str,
    request: BrowserApplySubmitRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> WorkflowTaskResponse:
    """Schedule a REAL LinkedIn Easy Apply submission via browser automation.

    Requires APP_ENABLE_LINKEDIN_APPLY=true and a browser session captured through
    scripts/browser_login_capture.py. LinkedIn's terms prohibit automated access and actively
    detect/ban automation; this endpoint exists because the operator explicitly opted in and
    accepted that risk, not by default. Only native Easy Apply jobs are supported.
    """
    del request
    settings = get_settings()
    if not settings.enable_linkedin_apply:
        raise HTTPException(
            status_code=403,
            detail="LinkedIn real-submission apply is disabled (APP_ENABLE_LINKEDIN_APPLY=false)",
        )
    try:
        task = RecruitmentService(session).schedule_real_submission_apply(
            application_id,
            adapter_name="linkedin-reference",
            workflow="linkedin_apply",
            site_key="linkedin",
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _workflow_task_response(task, session)


@app.get(
    "/v1/applications/{application_id}/task",
    response_model=WorkflowTaskResponse,
)
def get_application_task(
    application_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> WorkflowTaskResponse:
    task = session.scalar(
        select(WorkflowTaskRow)
        .where(WorkflowTaskRow.application_id == application_id)
        .order_by(WorkflowTaskRow.created_at.desc())
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Workflow task not found")
    return _workflow_task_response(task, session)


@app.get("/v1/workflow-tasks/{task_id}", response_model=WorkflowTaskResponse)
def get_workflow_task(
    task_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> WorkflowTaskResponse:
    task = session.get(WorkflowTaskRow, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Workflow task not found")
    return _workflow_task_response(task, session)


def _workflow_task_response(task: WorkflowTaskRow, session: Session) -> WorkflowTaskResponse:
    active_states = ("running", "scheduled", "retry_scheduled")
    queue_position: int | None = None
    if task.state in active_states:
        ordered_ids = list(
            session.scalars(
                select(WorkflowTaskRow.id)
                .where(
                    WorkflowTaskRow.queue_name == task.queue_name,
                    WorkflowTaskRow.state.in_(active_states),
                )
                .order_by(WorkflowTaskRow.priority.desc(), WorkflowTaskRow.created_at)
            )
        )
        if task.id in ordered_ids:
            queue_position = ordered_ids.index(task.id) + 1
    aggregate = (
        session.get(ApplicationMatchResultRow, task.application_id)
        if task.idempotency_key.startswith("matching-v2:") and task.application_id is not None
        else None
    )
    return WorkflowTaskResponse(
        id=task.id,
        application_id=task.application_id,
        idempotency_key=task.idempotency_key,
        queue_name=task.queue_name,
        state=task.state,
        attempt_number=task.attempt_number,
        priority=task.priority,
        queue_position=queue_position,
        pipeline_status=aggregate.status if aggregate is not None else None,
        pipeline_started_at=aggregate.started_at if aggregate is not None else None,
        pipeline_updated_at=aggregate.updated_at if aggregate is not None else None,
        calculated_at=aggregate.calculated_at if aggregate is not None else None,
        requirements_total=aggregate.requirements_total if aggregate is not None else None,
        requirements_processed=aggregate.requirements_processed if aggregate is not None else None,
        llm_calls_made=aggregate.llm_calls_made if aggregate is not None else None,
        refresh_requested=task.refresh_requested,
        scheduled_for=task.scheduled_for,
        created_at=task.created_at,
        updated_at=task.updated_at,
        transitions=[
            TaskTransitionResponse(
                previous_state=transition.previous_state,
                new_state=transition.new_state,
                reason=transition.reason,
                worker=transition.worker,
                attempt_number=transition.attempt_number,
                evidence=transition.evidence,
                occurred_at=transition.occurred_at,
            )
            for transition in task.transitions
        ],
    )


@app.get("/v1/matching/queue", response_model=MatchingQueueResponse)
async def get_matching_queue(
    session: Annotated[Session, Depends(session_scope)],
    redis_client: Annotated[redis.Redis, Depends(matching_queue_redis)],
) -> MatchingQueueResponse:
    paused = await matching_queue_is_paused(redis_client)
    counts = {
        state: count
        for state, count in session.execute(
            select(WorkflowTaskRow.state, func.count())
            .where(WorkflowTaskRow.queue_name == MATCHING_QUEUE_NAME)
            .group_by(WorkflowTaskRow.state)
        ).all()
    }
    active_states = ("pending", "scheduled", "retry_scheduled", "running")
    rows = list(
        session.scalars(
            select(WorkflowTaskRow)
            .where(
                WorkflowTaskRow.queue_name == MATCHING_QUEUE_NAME,
                WorkflowTaskRow.state.in_(active_states),
            )
            .order_by(WorkflowTaskRow.priority.desc(), WorkflowTaskRow.created_at)
        )
    )
    tasks = [
        MatchingQueueTaskResponse(
            id=row.id,
            application_id=row.application_id,
            state=row.state,
            attempt_number=row.attempt_number,
            priority=row.priority,
            queue_position=index + 1,
            scheduled_for=row.scheduled_for,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for index, row in enumerate(rows)
    ]
    return MatchingQueueResponse(paused=paused, counts=counts, tasks=tasks)


@app.post("/v1/matching/queue/pause", response_model=MatchingQueuePauseResponse)
async def pause_matching_queue(
    redis_client: Annotated[redis.Redis, Depends(matching_queue_redis)],
) -> MatchingQueuePauseResponse:
    await set_matching_queue_paused(redis_client, paused=True)
    return MatchingQueuePauseResponse(paused=True)


@app.post("/v1/matching/queue/resume", response_model=MatchingQueuePauseResponse)
async def resume_matching_queue(
    redis_client: Annotated[redis.Redis, Depends(matching_queue_redis)],
) -> MatchingQueuePauseResponse:
    await set_matching_queue_paused(redis_client, paused=False)
    return MatchingQueuePauseResponse(paused=False)


@app.post("/v1/matching/queue/clear", response_model=ClearMatchingQueueResponse)
async def clear_matching_queue_endpoint(
    session: Annotated[Session, Depends(session_scope)],
    redis_client: Annotated[redis.Redis, Depends(matching_queue_redis)],
) -> ClearMatchingQueueResponse:
    report = clear_matching_queue(session)
    # Clearing also pauses the queue so the backlog cannot immediately refill.
    await set_matching_queue_paused(redis_client, paused=True)
    remaining = session.scalar(
        select(func.count())
        .select_from(WorkflowTaskRow)
        .where(
            WorkflowTaskRow.queue_name == MATCHING_QUEUE_NAME,
            WorkflowTaskRow.state.in_(("pending", "scheduled", "retry_scheduled", "running")),
        )
    )
    return ClearMatchingQueueResponse(
        paused=True,
        cancelled=report.cancelled,
        interrupted=report.interrupted,
        remaining_active=remaining or 0,
    )


def _active_human_action(
    workflow_task: WorkflowTaskRow,
) -> HumanActionCheckpointResponse | None:
    waiting = [
        checkpoint for checkpoint in workflow_task.human_actions if checkpoint.status == "waiting"
    ]
    if not waiting:
        return None
    checkpoint = max(waiting, key=lambda item: item.created_at)
    return HumanActionCheckpointResponse(
        id=checkpoint.id,
        kind=checkpoint.kind,
        status=checkpoint.status,
        instructions=checkpoint.instructions,
        evidence=checkpoint.evidence,
        artifacts=[
            EvidenceArtifactResponse(
                id=artifact.id,
                kind=artifact.kind,
                content_type=artifact.content_type,
                size_bytes=artifact.size_bytes,
                url=f"/v1/evidence/{artifact.id}",
            )
            for artifact in checkpoint.artifacts
        ],
    )


@app.get("/v1/review-queue", response_model=list[ReviewItemResponse])
def list_review_queue(
    session: Annotated[Session, Depends(session_scope)],
    application_id: str | None = None,
) -> list[ReviewItemResponse]:
    return [
        ReviewItemResponse(
            **ApplicationResponse.model_validate(application, from_attributes=True).model_dump(),
            company=vacancy.company,
            vacancy_title=vacancy.title,
            source_url=vacancy.source_url,
            adapter_name=vacancy.adapter_name,
            selected_cv_filename=cv_file.original_filename if cv_file is not None else None,
            current_workflow_state=workflow_task.state,
            cover_letter_text=application.cover_letter_text,
            screening_answers=[
                ScreeningAnswerResponse.model_validate(answer, from_attributes=True)
                for answer in application.answers
            ],
            missing_facts=[
                answer.label
                for answer in application.answers
                if answer.is_required and answer.answer is None
            ],
            requested_legal_declarations=[
                answer.label
                for answer in application.answers
                if answer.semantic_category in SENSITIVE_CATEGORIES
            ],
            active_human_action=_active_human_action(workflow_task),
            vacancy_summary=summarize_vacancy(vacancy.description_text),
            work_format=response_work_format(vacancy),
            salary_text=vacancy.salary_text,
            employment_types=response_employment_types(vacancy),
            missing_required_skills=[
                warning.removeprefix("Missing required skill: ").strip()
                for warning in application.warnings
                if warning.startswith("Missing required skill: ")
            ],
            key_skills=list(
                extract_key_skills(vacancy.description_text, vacancy.required_skills or ())
            ),
        )
        for application, vacancy, cv_file, workflow_task in RecruitmentService(
            session
        ).list_review_queue(application_id)
    ]


@app.post("/v1/applications/{application_id}/decision", response_model=ApplicationResponse)
def decide_application(
    application_id: str,
    request: ReviewDecisionRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> ApplicationResponse:
    try:
        application = RecruitmentService(session).decide_application(
            application_id, request.decision
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ApplicationResponse.model_validate(application, from_attributes=True)


@app.patch(
    "/v1/applications/{application_id}/status",
    response_model=ApplicationResponse,
)
def update_application_status(
    application_id: str,
    request: ApplicationStatusUpdateRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> ApplicationResponse:
    try:
        application = RecruitmentService(session).update_application_status(
            application_id, request.status
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return ApplicationResponse.model_validate(application, from_attributes=True)


@app.post(
    "/v1/applications/{application_id}/reject-vacancy",
    response_model=ApplicationResponse,
)
def reject_saved_vacancy(
    application_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> ApplicationResponse:
    try:
        application = VacancyCatalogService(session).reject_saved_vacancy(application_id)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ApplicationResponse.model_validate(application, from_attributes=True)


@app.post("/v1/applications/{application_id}/retry", response_model=WorkflowTaskResponse)
def retry_application_task(
    application_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> WorkflowTaskResponse:
    try:
        RecruitmentService(session).retry_application_task(application_id)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return get_application_task(application_id, session)


@app.patch(
    "/v1/applications/{application_id}/materials",
    response_model=ApplicationMaterialsResponse,
)
def update_application_materials(
    application_id: str,
    request: ApplicationMaterialsUpdateRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> ApplicationMaterialsResponse:
    answer_updates = {item.field_id: item.answer for item in request.screening_answers}
    if len(answer_updates) != len(request.screening_answers):
        raise HTTPException(status_code=422, detail="Duplicate screening answer field_id")
    try:
        application = RecruitmentService(session).update_application_materials(
            application_id,
            cover_letter_text=request.cover_letter_text,
            screening_answers=answer_updates,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _materials_response(session, application)


@app.get(
    "/v1/applications/{application_id}/materials",
    response_model=ApplicationMaterialsResponse,
)
def get_application_materials(
    application_id: str, session: Annotated[Session, Depends(session_scope)]
) -> ApplicationMaterialsResponse:
    application = session.get(ApplicationRow, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return _materials_response(session, application)


def _materials_response(
    session: Session, application: ApplicationRow
) -> ApplicationMaterialsResponse:
    vacancy = session.get(VacancyRow, application.vacancy_id)
    if vacancy is None:
        raise HTTPException(status_code=404, detail="Vacancy not found")
    return ApplicationMaterialsResponse(
        application_id=application.id,
        application_status=application.status,
        location=vacancy.location,
        vacancy_language=detect_vacancy_language(vacancy),
        cover_letter_language_matches=cover_letter_matches_vacancy_language(
            vacancy, application.cover_letter_text
        ),
        vacancy_summary=summarize_vacancy(vacancy.description_text),
        work_format=response_work_format(vacancy),
        employment_types=response_employment_types(vacancy),
        key_skills=list(
            extract_key_skills(vacancy.description_text, vacancy.required_skills or ())
        ),
        cover_letter_text=application.cover_letter_text,
        screening_answers=[
            ScreeningAnswerResponse.model_validate(answer, from_attributes=True)
            for answer in application.answers
        ],
        missing_facts=[
            answer.label
            for answer in application.answers
            if answer.is_required and answer.answer is None
        ],
        requested_legal_declarations=[
            answer.label
            for answer in application.answers
            if answer.semantic_category in SENSITIVE_CATEGORIES
        ],
    )


@app.post(
    "/v1/applications/{application_id}/generate-materials",
    response_model=ApplicationMaterialsResponse,
)
async def generate_application_materials(
    application_id: str,
    session: Annotated[Session, Depends(session_scope)],
    http_client: Annotated[httpx.AsyncClient, Depends(llm_http_client)],
) -> ApplicationMaterialsResponse:
    """Draft a cover letter and open screening answers from the candidate's verified facts.

    This is a draft, not a submission: results land as ``llm_generated`` on the still-editable,
    still-``awaiting_review`` application. Existing non-empty answers and an existing cover
    letter are left untouched, so re-running this is safe. Sensitive-category fields (work
    authorization, disability, etc.) are never sent to the model and never written by it — see
    app/services/materials_generation.py. Requires APP_ANTHROPIC_API_KEY or APP_GEMINI_API_KEY.
    """
    settings = get_settings()
    application = session.get(ApplicationRow, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    try:
        providers = build_user_model_providers(http_client, session, application.user_id, settings)
    except InvalidLlmPreference as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if not providers:
        raise HTTPException(
            status_code=503,
            detail=(
                "Materials drafting is unavailable: set APP_ANTHROPIC_API_KEY or APP_GEMINI_API_KEY"
            ),
        )
    try:
        await MaterialsGenerationService(session, ModelRouter(providers)).draft_materials(
            application_id,
            replace_mismatched_cover_letter=True,
            force_replace_cover_letter=True,
            timeout_seconds=settings.materials_generation_timeout_seconds,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NoVerifiedFactsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except MaterialsLanguageMismatchError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except NoModelAvailableError as error:
        raise HTTPException(
            status_code=502, detail=f"Materials drafting failed: {error}"
        ) from error
    return _materials_response(session, application)


@app.post("/v1/applications/{application_id}/resume", response_model=WorkflowTaskResponse)
def resume_human_action(
    application_id: str,
    request: HumanActionResumeRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> WorkflowTaskResponse:
    try:
        RecruitmentService(session).resume_human_action(
            application_id, resolution_evidence=request.confirmation
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateEntityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return get_application_task(application_id, session)


@app.delete("/v1/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
    storage: Annotated[DocumentStorage, Depends(document_storage)],
    evidence_storage: Annotated[EvidenceArtifactStorage, Depends(evidence_artifact_storage)],
) -> Response:
    try:
        cv_file_ids = tuple(
            session.scalars(select(CvFileRow.id).where(CvFileRow.user_id == user_id)).all()
        )
        vacancy_ids = tuple(
            session.scalars(
                select(ApplicationRow.vacancy_id)
                .where(ApplicationRow.user_id == user_id)
                .distinct()
            ).all()
        )
        stored_paths, evidence_paths, browser_state_paths = RecruitmentService(session).delete_user(
            user_id
        )
        resume_rag_jobs = ResumeRagJobService(session)
        for cv_file_id in cv_file_ids:
            resume_rag_jobs.delete_for_cv(cv_file_id)
        await RagSyncService(session, get_settings()).delete_owner_documents(
            user_id,
            cv_file_ids=cv_file_ids,
            vacancy_ids=vacancy_ids,
        )
        for stored_path in stored_paths:
            storage.delete(stored_path)
        for evidence_path in evidence_paths:
            evidence_storage.delete(evidence_path)
        for browser_state_path in browser_state_paths:
            delete_browser_state_file(get_settings().artifact_directory, browser_state_path)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidDocumentError as error:
        raise HTTPException(status_code=500, detail="Stored document path is invalid") from error
    except InvalidEvidenceArtifact as error:
        raise HTTPException(status_code=500, detail="Stored evidence path is invalid") from error
    except InvalidBrowserState as error:
        raise HTTPException(
            status_code=500, detail="Stored browser state path is invalid"
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
