import asyncio
import secrets
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

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
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from adapters.job_boards.browser_apply_common import ApplyBlocked, CaptchaChallenge
from adapters.job_boards.contracts import ADAPTER_CAPABILITIES
from adapters.job_boards.greenhouse_api import GreenhouseJobBoardApi
from adapters.job_boards.headhunter_browser import HeadHunterBrowserAdapter
from adapters.job_boards.linkedin_browser import LinkedInBrowserAdapter
from adapters.job_boards.linkedin_reference import LinkedInJobReference
from app.api.schemas import (
    ApplicationMaterialsResponse,
    ApplicationMaterialsUpdateRequest,
    ApplicationResponse,
    AssessmentRequest,
    AssessmentResponse,
    BrowserApplySubmitRequest,
    BrowserHandoffRequest,
    BrowserHandoffResponse,
    BrowserReviewRequest,
    ConfirmedProfileFactResponse,
    ConfirmProfileFactsRequest,
    ConnectorCapabilityResponse,
    CvFileResponse,
    DiscoverHeadHunterVacanciesRequest,
    DiscoverLinkedInVacanciesRequest,
    DiscoveryOutcomeResponse,
    EvidenceArtifactResponse,
    ExtractedProfileResponse,
    GreenhouseImportRequest,
    HeadHunterImportRequest,
    HealthResponse,
    HumanActionCheckpointResponse,
    HumanActionResumeRequest,
    ImportedVacancyResponse,
    LinkedInReferenceImportRequest,
    PrepareApplicationRequest,
    ProfileFactRequest,
    ProfileFactResponse,
    ReviewDecisionRequest,
    ReviewItemResponse,
    ScreeningAnswerResponse,
    TaskTransitionResponse,
    UserRequest,
    UserResponse,
    VacancyRequest,
    VacancyResponse,
    WorkflowTaskResponse,
)
from app.browser.engine import PlaywrightEngine
from app.browser.selector_library import SelectorLibrary
from app.browser.session_store import InvalidBrowserState, delete_browser_state_file
from app.config import Settings, get_settings
from app.domain.models import ProfileFact, Vacancy
from app.domain.policy import SENSITIVE_CATEGORIES, assess_vacancy
from app.domain.resume_text import UnreadableResumeError, extract_resume_text
from app.llm.providers.anthropic import AnthropicMessagesProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.router import ModelProvider, ModelRouter, NoModelAvailableError
from app.observability.logging import configure_logging
from app.observability.metrics import metrics
from app.services.browser_handoff import create_browser_handoff
from app.services.job_discovery import (
    JobDiscoveryService,
    LinkedInSessionRequiredError,
    NoSearchKeywordsError,
)
from app.services.materials_generation import (
    MaterialsGenerationService,
    NoVerifiedFactsError,
)
from app.services.recruitment import (
    DuplicateEntityError,
    EntityNotFoundError,
    RecruitmentService,
)
from app.services.resume_intake import ResumeIntakeService
from app.storage.database import SessionFactory, session_scope
from app.storage.documents import DocumentStorage, InvalidDocumentError
from app.storage.evidence_artifacts import EvidenceArtifactStorage, InvalidEvidenceArtifact
from app.storage.tables import ApplicationRow, CvFileRow, WorkerHeartbeatRow, WorkflowTaskRow
from app.workers.browser_tasks import _restore_browser_session
from app.workers.browser_worker import create_session_store

configure_logging()
app = FastAPI(title="Job Searching Assistant", version="0.1.0")
REVIEW_UI_PATH = Path(__file__).parents[1] / "static" / "review.html"
DASHBOARD_UI_PATH = Path(__file__).parents[1] / "static" / "dashboard.html"


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
    if path == "/v1/applications/prepare":
        return "applications:write"
    if path.startswith("/v1/applications/") and (
        path.endswith("/apply-headhunter") or path.endswith("/apply-linkedin")
    ):
        return "applications:submit_real"
    if path.startswith("/v1/applications/") and (
        path.endswith("/decision")
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


def _configured_secret(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value().strip()
    return value or None


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


async def model_router() -> AsyncIterator[ModelRouter]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False) as client:
        yield ModelRouter(build_model_providers(client, settings))


async def headhunter_browser_adapter() -> AsyncIterator[HeadHunterBrowserAdapter]:
    """A HeadHunterBrowserAdapter for read-only search/extraction (no captured session needed).

    api.hh.ru's anonymous access is CAPTCHA-limited in practice, so search and vacancy reads go
    through a real Chromium instance against hh.ru's public pages instead. Only the separate
    apply step needs a session captured via scripts/browser_login_capture.py.
    """
    settings = get_settings()
    task_artifact_directory = settings.artifact_directory / "browser-worker" / "headhunter-read"
    async with PlaywrightEngine(
        headless=settings.browser_headless,
        timeout_ms=settings.browser_timeout_ms,
        artifact_directory=task_artifact_directory,
        selector_library=SelectorLibrary(settings.artifact_directory),
    ) as browser_engine:
        yield HeadHunterBrowserAdapter(browser_engine)


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
        },
    )


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


@app.post(
    "/v1/users/{user_id}/facts",
    response_model=ProfileFactResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_profile_fact(
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
    return ProfileFactResponse(
        id=fact.id,
        user_id=fact.user_id,
        category=fact.category,
        name=fact.name,
        value=fact.value,
        is_verified=fact.is_verified,
    )


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
    return CvFileResponse.model_validate(cv_file, from_attributes=True)


@app.post(
    "/v1/users/{user_id}/cv-files/{cv_file_id}/extract-profile",
    response_model=ExtractedProfileResponse,
)
async def extract_profile_from_cv(
    user_id: str,
    cv_file_id: str,
    session: Annotated[Session, Depends(session_scope)],
    router: Annotated[ModelRouter, Depends(model_router)],
    storage: Annotated[DocumentStorage, Depends(document_storage)],
) -> ExtractedProfileResponse:
    """Draft skills/summary/search keywords from an uploaded resume. Writes nothing yet.

    The result is a draft for the person to review and edit; call
    ``POST /v1/users/{user_id}/confirm-profile-facts`` to actually save any of it as verified
    profile facts. Requires APP_ANTHROPIC_API_KEY or APP_GEMINI_API_KEY to be set.
    """
    settings = get_settings()
    if not llm_is_configured(settings):
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
        draft = await ResumeIntakeService(router).draft_profile(resume_text)
    except NoModelAvailableError as error:
        raise HTTPException(status_code=502, detail=f"Resume analysis failed: {error}") from error
    return ExtractedProfileResponse(
        skills=draft.skills,
        experience_summary=draft.experience_summary,
        search_keywords=draft.search_keywords,
        years_of_experience=draft.years_of_experience,
    )


@app.post(
    "/v1/users/{user_id}/confirm-profile-facts",
    response_model=list[ConfirmedProfileFactResponse],
)
def confirm_profile_facts(
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
    adapter: Annotated[HeadHunterBrowserAdapter, Depends(headhunter_browser_adapter)],
) -> list[DiscoveryOutcomeResponse]:
    """Search hh.ru by the candidate's verified skills and stage results as awaiting_review.

    Uses a real (headless) browser against hh.ru's public search/vacancy pages rather than
    api.hh.ru, whose anonymous access is CAPTCHA-limited in practice. Only creates
    vacancies/applications for human review — never schedules a real submission.
    """
    settings = get_settings()
    try:
        outcomes = await JobDiscoveryService(session).discover_headhunter_vacancies(
            user_id,
            headhunter_adapter=adapter,
            locations=request.locations,
            limit=request.limit,
            search_text=request.search_text,
        )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NoSearchKeywordsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if llm_is_configured(settings):
        materials_router = ModelRouter(build_model_providers(http_client, settings))
        materials_service = MaterialsGenerationService(session, materials_router)
        for outcome in outcomes:
            if outcome.status != "created":
                continue
            try:
                await materials_service.draft_materials(outcome.application_id)
            except Exception:  # noqa: BLE001 - a drafting failure must not fail the whole search
                continue
    return [DiscoveryOutcomeResponse(**outcome.__dict__) for outcome in outcomes]


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
    submission) and a session captured via ``scripts/browser_login_capture.py linkedin`` — an
    anonymous/un-authed LinkedIn job search hits an auth wall almost immediately. Only creates
    vacancies/applications for human review — never schedules a real submission.
    """
    settings = get_settings()
    if not settings.enable_linkedin_apply:
        raise HTTPException(
            status_code=403,
            detail="LinkedIn browser automation is disabled (APP_ENABLE_LINKEDIN_APPLY=false)",
        )
    store = create_session_store(settings)
    state = _restore_browser_session(SessionFactory, store, user_id=user_id, site_key="linkedin")
    if state is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No usable LinkedIn session; run "
                "`python scripts/browser_login_capture.py linkedin --user-id "
                f"{user_id}` first"
            ),
        )
    task_artifact_directory = (
        settings.artifact_directory / "browser-worker" / f"li-discover-{user_id}"
    )
    try:
        async with PlaywrightEngine(
            headless=settings.browser_headless,
            timeout_ms=settings.browser_timeout_ms,
            artifact_directory=task_artifact_directory,
            storage_state=state,
            selector_library=SelectorLibrary(settings.artifact_directory),
        ) as browser_engine:
            adapter = LinkedInBrowserAdapter(browser_engine)
            outcomes = await JobDiscoveryService(session).discover_linkedin_vacancies(
                user_id,
                linkedin_adapter=adapter,
                locations=request.locations,
                limit=request.limit,
                search_text=request.search_text,
            )
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NoSearchKeywordsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LinkedInSessionRequiredError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if llm_is_configured(settings):
        materials_router = ModelRouter(build_model_providers(http_client, settings))
        materials_service = MaterialsGenerationService(session, materials_router)
        for outcome in outcomes:
            if outcome.status != "created":
                continue
            try:
                await materials_service.draft_materials(outcome.application_id)
            except Exception:  # noqa: BLE001 - a drafting failure must not fail the whole search
                continue
    return [DiscoveryOutcomeResponse(**outcome.__dict__) for outcome in outcomes]


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
    adapter: Annotated[HeadHunterBrowserAdapter, Depends(headhunter_browser_adapter)],
) -> ImportedVacancyResponse:
    """Import one hh.ru vacancy by URL using a real browser (not api.hh.ru).

    api.hh.ru's anonymous access is CAPTCHA-limited in practice, so extraction reads the public
    vacancy page directly through Chromium instead — no login/session is required for this.
    """
    try:
        extracted = await adapter.extract_vacancy(str(request.source_url))
        vacancy = RecruitmentService(session).create_vacancy(
            source_url=extracted.source_url,
            title=extracted.title,
            company=extracted.company,
            required_skills=list(extracted.required_skills),
            preferred_skills=[],
            location=extracted.location,
            description_text=extracted.description_text,
            adapter_name="headhunter",
            source_evidence_url=extracted.source_url,
            application_fields=[asdict(field) for field in extracted.form_fields],
            requires_sensitive_review=extracted.requires_sensitive_review,
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
    return _workflow_task_response(task)


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
    return _workflow_task_response(task)


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
    return _workflow_task_response(task)


@app.get(
    "/v1/applications/{application_id}/task",
    response_model=WorkflowTaskResponse,
)
def get_application_task(
    application_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> WorkflowTaskResponse:
    task = session.scalar(
        select(WorkflowTaskRow).where(WorkflowTaskRow.application_id == application_id)
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Workflow task not found")
    return _workflow_task_response(task)


def _workflow_task_response(task: WorkflowTaskRow) -> WorkflowTaskResponse:
    return WorkflowTaskResponse(
        id=task.id,
        application_id=task.application_id,
        idempotency_key=task.idempotency_key,
        queue_name=task.queue_name,
        state=task.state,
        attempt_number=task.attempt_number,
        priority=task.priority,
        transitions=[
            TaskTransitionResponse(
                previous_state=transition.previous_state,
                new_state=transition.new_state,
                reason=transition.reason,
                worker=transition.worker,
                attempt_number=transition.attempt_number,
                evidence=transition.evidence,
            )
            for transition in task.transitions
        ],
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
        )
        for application, vacancy, cv_file, workflow_task in RecruitmentService(
            session
        ).list_review_queue()
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
    return _materials_response(application)


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
    return _materials_response(application)


def _materials_response(application: ApplicationRow) -> ApplicationMaterialsResponse:
    return ApplicationMaterialsResponse(
        application_id=application.id,
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
    router: Annotated[ModelRouter, Depends(model_router)],
) -> ApplicationMaterialsResponse:
    """Draft a cover letter and open screening answers from the candidate's verified facts.

    This is a draft, not a submission: results land as ``llm_generated`` on the still-editable,
    still-``awaiting_review`` application. Existing non-empty answers and an existing cover
    letter are left untouched, so re-running this is safe. Sensitive-category fields (work
    authorization, disability, etc.) are never sent to the model and never written by it — see
    app/services/materials_generation.py. Requires APP_ANTHROPIC_API_KEY or APP_GEMINI_API_KEY.
    """
    settings = get_settings()
    if not llm_is_configured(settings):
        raise HTTPException(
            status_code=503,
            detail=(
                "Materials drafting is unavailable: set APP_ANTHROPIC_API_KEY or APP_GEMINI_API_KEY"
            ),
        )
    try:
        await MaterialsGenerationService(session, router).draft_materials(application_id)
    except EntityNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NoVerifiedFactsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except NoModelAvailableError as error:
        raise HTTPException(
            status_code=502, detail=f"Materials drafting failed: {error}"
        ) from error
    application = session.get(ApplicationRow, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return _materials_response(application)


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
def delete_user(
    user_id: str,
    session: Annotated[Session, Depends(session_scope)],
    storage: Annotated[DocumentStorage, Depends(document_storage)],
    evidence_storage: Annotated[EvidenceArtifactStorage, Depends(evidence_artifact_storage)],
) -> Response:
    try:
        stored_paths, evidence_paths, browser_state_paths = RecruitmentService(session).delete_user(
            user_id
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
