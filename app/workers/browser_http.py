"""HTTP API for the browser-worker, exposing browser operations over HTTP
so the API container no longer needs Playwright or browser binaries."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from adapters.job_boards.headhunter_browser import HeadHunterBrowserAdapter
from adapters.job_boards.linkedin_browser import LinkedInBrowserAdapter
from app.browser.custom_site import (
    CustomSiteError,
    CustomSiteHit,
    extract_custom_vacancy,
    fetch_page_html,
    search_custom_site,
)
from app.browser.engine import PlaywrightEngine
from app.browser.recipe_learning import RecipeLearningError, infer_url_template, learn_selectors
from app.browser.search_reach_recording import selector_candidates_for
from app.browser.selector_library import SelectorLibrary
from app.browser.session_probe import probe_browser_session
from app.browser.session_service import BrowserSessionService
from app.browser.session_store import EncryptedBrowserStateStore, InvalidBrowserState
from app.config import Settings, get_settings
from app.domain.search_recipe import InvalidSearchRecipe, SearchRecipe, validate_recipe
from app.observability.logging import configure_logging
from app.services.browser_authorization import (
    BrowserAuthorizationError,
    BrowserAuthorizationManager,
    BrowserAuthorizationSite,
)
from app.services.search_recipe_recording import (
    SearchRecipeRecordingManager,
    SearchRecordingError,
)
from app.storage.database import SessionFactory
from app.storage.tables import BrowserSessionRow
from app.workers.browser_worker import create_session_store

logger = structlog.get_logger(__name__)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchRequest(StrictModel):
    source: str = Field(pattern=r"^(headhunter|linkedin)$")
    user_id: str
    search_text: str | None = None
    locations: list[str] = Field(default_factory=list)
    limit: int = Field(default=15, ge=1, le=50)


class SearchHit(StrictModel):
    source_url: str
    title: str
    company: str
    vacancy_id: str | None = None


class SearchResponse(StrictModel):
    hits: list[SearchHit]


class ProbeRequest(StrictModel):
    user_id: str
    site_key: str = Field(pattern=r"^(headhunter|linkedin)$")


class ProbeResponse(StrictModel):
    valid: bool
    details: str = ""


class SubmissionProbeRequest(StrictModel):
    source: str = Field(pattern=r"^(headhunter|linkedin)$")
    user_id: str
    url: str


class SubmissionProbeResponse(StrictModel):
    submitted: bool


_settings: Settings | None = None
_store: EncryptedBrowserStateStore | None = None
_selector_library: SelectorLibrary | None = None


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = get_settings()
    return _settings


def _get_store() -> EncryptedBrowserStateStore:
    global _store
    if _store is None:
        _store = create_session_store(_get_settings())
    return _store


def _get_selector_library() -> SelectorLibrary:
    global _selector_library
    if _selector_library is None:
        _selector_library = SelectorLibrary(_get_settings().artifact_directory)
    return _selector_library


def _restore_session(user_id: str, site_key: str) -> dict[str, object] | None:
    with SessionFactory() as session:
        row = session.scalar(
            select(BrowserSessionRow).where(
                BrowserSessionRow.user_id == user_id,
                BrowserSessionRow.site_key == site_key,
                BrowserSessionRow.status.in_(("available", "active")),
            )
        )
        if row is None:
            return None
        try:
            _restored_row, state = BrowserSessionService(session, _get_store()).restore(row.id)
            return state
        except Exception:
            return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    yield


app = FastAPI(title="Browser Worker HTTP", version="1", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/browser/search", response_model=SearchResponse)
async def browser_search(request: SearchRequest) -> SearchResponse:
    settings = _get_settings()
    state = _restore_session(request.user_id, request.source)
    if state is None:
        raise HTTPException(
            status_code=422,
            detail=f"No active browser session for {request.source}",
        )

    artifact_prefix = "hh" if request.source == "headhunter" else "li"
    artifact_directory = (
        settings.artifact_directory
        / "browser-worker"
        / f"{artifact_prefix}-discover-{request.user_id}"
    )

    hits: list[SearchHit] = []
    async with PlaywrightEngine(
        headless=settings.browser_headless,
        timeout_ms=settings.browser_timeout_ms,
        artifact_directory=artifact_directory,
        storage_state=state,
        selector_library=_get_selector_library(),
    ) as browser_engine:
        if request.source == "headhunter":
            headhunter_adapter = HeadHunterBrowserAdapter(browser_engine)
            headhunter_hits = await headhunter_adapter.search(
                text=request.search_text or "",
                location_names=request.locations or None,
                limit=request.limit,
            )
            hits = [
                SearchHit(
                    source_url=hit.source_url,
                    title=hit.title,
                    company=hit.company,
                    vacancy_id=hit.vacancy_id,
                )
                for hit in headhunter_hits
            ]
        else:
            linkedin_adapter = LinkedInBrowserAdapter(browser_engine)
            linkedin_hits = await linkedin_adapter.search(
                text=request.search_text or "",
                location_names=request.locations or None,
                limit=request.limit,
            )
            hits = [
                SearchHit(
                    source_url=hit.source_url,
                    title=hit.title,
                    company=hit.company,
                    vacancy_id=hit.job_id,
                )
                for hit in linkedin_hits
            ]

    return SearchResponse(hits=hits)


# Request models must precede the routes that use them: with `from __future__ import annotations`
# FastAPI resolves annotations when the decorator runs, and an undefined name silently turns the
# body parameter into a required query parameter (every call then fails with 422).
class ExtractHeadhunterRequest(StrictModel):
    user_id: str
    url: str


class ExtractLinkedinRequest(StrictModel):
    user_id: str
    url: str


@app.post("/v1/browser/extract-headhunter")
async def extract_headhunter_vacancy(
    request: ExtractHeadhunterRequest,
) -> dict[str, object]:
    settings = _get_settings()
    state = _restore_session(request.user_id, "headhunter")
    if state is None:
        raise HTTPException(status_code=422, detail="No active headhunter session")

    artifact_directory = (
        settings.artifact_directory / "browser-worker" / f"hh-extract-{request.user_id}"
    )
    async with PlaywrightEngine(
        headless=settings.browser_headless,
        timeout_ms=settings.browser_timeout_ms,
        artifact_directory=artifact_directory,
        storage_state=state,
        selector_library=_get_selector_library(),
    ) as browser_engine:
        adapter = HeadHunterBrowserAdapter(browser_engine)
        extracted = await adapter.extract_vacancy(request.url)
        if dataclasses.is_dataclass(extracted):
            return dataclasses.asdict(extracted)
        return {
            "title": extracted.title,
            "company": extracted.company,
            "description_text": extracted.description_text,
            "source_url": extracted.source_url,
        }


@app.post("/v1/browser/extract-linkedin")
async def extract_linkedin_vacancy(
    request: ExtractLinkedinRequest,
) -> dict[str, object]:
    settings = _get_settings()
    state = _restore_session(request.user_id, "linkedin")
    if state is None:
        raise HTTPException(status_code=422, detail="No active linkedin session")

    artifact_directory = (
        settings.artifact_directory / "browser-worker" / f"li-extract-{request.user_id}"
    )
    async with PlaywrightEngine(
        headless=settings.browser_headless,
        timeout_ms=settings.browser_timeout_ms,
        artifact_directory=artifact_directory,
        storage_state=state,
        selector_library=_get_selector_library(),
    ) as browser_engine:
        adapter = LinkedInBrowserAdapter(browser_engine)
        extracted = await adapter.extract_vacancy(request.url)
        if dataclasses.is_dataclass(extracted):
            return dataclasses.asdict(extracted)
        return {
            "title": extracted.title,
            "company": extracted.company,
            "description_text": extracted.description_text,
            "source_url": extracted.source_url,
        }


@app.post("/v1/browser/probe", response_model=ProbeResponse)
async def browser_probe(request: ProbeRequest) -> ProbeResponse:
    settings = _get_settings()
    state = _restore_session(request.user_id, request.site_key)
    if state is None:
        return ProbeResponse(valid=False, details="No stored session found")

    try:
        result = await probe_browser_session(
            site_key=request.site_key,
            state=state,
            headless=settings.browser_headless,
            timeout_ms=settings.browser_timeout_ms,
            artifact_directory=settings.artifact_directory,
        )
        return ProbeResponse(valid=result.is_live is True, details=result.error or "")
    except Exception as error:
        return ProbeResponse(valid=False, details=type(error).__name__)


@app.post("/v1/browser/submission-probe", response_model=SubmissionProbeResponse)
async def submission_probe(request: SubmissionProbeRequest) -> SubmissionProbeResponse:
    settings = _get_settings()
    state = _restore_session(request.user_id, request.source)
    if state is None:
        raise HTTPException(status_code=422, detail="No active browser session")
    artifact_directory = (
        settings.artifact_directory
        / "browser-worker"
        / f"submission-probe-{request.source}-{request.user_id}"
    )
    async with PlaywrightEngine(
        headless=settings.browser_headless,
        timeout_ms=settings.browser_timeout_ms,
        artifact_directory=artifact_directory,
        storage_state=state,
        selector_library=_get_selector_library(),
    ) as browser_engine:
        if request.source == "headhunter":
            submitted = await HeadHunterBrowserAdapter(browser_engine).has_submitted_application(
                request.url
            )
        else:
            submitted = await LinkedInBrowserAdapter(browser_engine).has_submitted_application(
                request.url
            )
    return SubmissionProbeResponse(submitted=submitted)


_auth_manager = BrowserAuthorizationManager()


class LoginSiteConfig(StrictModel):
    site_key: str
    login_url: str
    allowed_hosts: list[str]
    login_path_markers: list[str] = Field(default_factory=list)
    allow_subdomains: bool = False


class LoginStartRequest(StrictModel):
    user_id: str
    site_key: str
    site: LoginSiteConfig


class LoginConfirmRequest(StrictModel):
    user_id: str
    site_key: str
    adapter_name: str


class LoginCancelRequest(StrictModel):
    user_id: str
    site_key: str


@app.post("/v1/browser/login/start")
async def login_start(request: LoginStartRequest) -> dict[str, str]:
    settings = _get_settings()
    site = BrowserAuthorizationSite(
        site_key=request.site.site_key,
        login_url=request.site.login_url,
        allowed_hosts=tuple(request.site.allowed_hosts),
        login_path_markers=tuple(request.site.login_path_markers),
        allow_subdomains=request.site.allow_subdomains,
    )
    try:
        await _auth_manager.start(
            user_id=request.user_id,
            site_key=request.site_key,
            timeout_ms=settings.browser_timeout_ms,
            artifact_directory=(
                settings.artifact_directory / "browser" / "login-capture" / request.site_key
            ),
            site=site,
        )
    except BrowserAuthorizationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="Не удалось открыть окно входа. Проверьте установку Chromium",
        ) from error
    return {"site_key": request.site_key, "state": "waiting_for_login"}


@app.post("/v1/browser/login/confirm")
async def login_confirm(request: LoginConfirmRequest) -> dict[str, object]:
    try:
        state, last_url = await _auth_manager.confirm(
            user_id=request.user_id,
            site_key=request.site_key,
        )
    except BrowserAuthorizationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (InvalidBrowserState, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    store = _get_store()
    with SessionFactory() as session:
        BrowserSessionService(session, store).save(
            user_id=request.user_id,
            site_key=request.site_key,
            adapter_name=request.adapter_name,
            state=state,
            last_url=last_url,
        )
    return {"site_key": request.site_key, "state": "authorized", "last_url": last_url}


@app.post("/v1/browser/login/cancel")
async def login_cancel(request: LoginCancelRequest) -> dict[str, str]:
    await _auth_manager.cancel(user_id=request.user_id, site_key=request.site_key)
    return {"site_key": request.site_key, "state": "cancelled"}


@app.get("/v1/browser/login/status")
async def login_status(user_id: str, site_key: str) -> dict[str, bool]:
    return {"is_waiting": _auth_manager.is_waiting(user_id=user_id, site_key=site_key)}


# --- user-defined sites -------------------------------------------------------------------
# Request models come first for the reason given above the extract routes.


class CustomSiteConfig(StrictModel):
    site_key: str = Field(min_length=1, max_length=100)
    allowed_hosts: list[str] = Field(min_length=1, max_length=20)
    login_path_markers: list[str] = Field(default_factory=list, max_length=20)


class CustomLearnRequest(StrictModel):
    user_id: str
    site: CustomSiteConfig
    results_url: str = Field(max_length=2000)
    query: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=200)


class CustomSearchRequest(StrictModel):
    user_id: str
    site: CustomSiteConfig
    recipe: dict[str, object]
    search_text: str = Field(min_length=1, max_length=500)
    locations: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=15, ge=1, le=50)


class CustomExtractRequest(StrictModel):
    user_id: str
    site: CustomSiteConfig
    url: str = Field(max_length=2000)


def _custom_engine(request_user_id: str, site: CustomSiteConfig) -> PlaywrightEngine:
    """Engine for a user-defined site; the saved sign-in is used when there is one."""
    settings = _get_settings()
    return PlaywrightEngine(
        headless=settings.browser_headless,
        timeout_ms=settings.browser_timeout_ms,
        artifact_directory=settings.artifact_directory
        / "browser-worker"
        / f"custom-{site.site_key}-{request_user_id}",
        storage_state=_restore_session(request_user_id, site.site_key),
        selector_library=_get_selector_library(),
    )


def _custom_error(error: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


def _hit_payload(hit: CustomSiteHit) -> dict[str, str]:
    return {
        "source_url": hit.source_url,
        "title": hit.title,
        "company": hit.company,
    }


@app.post("/v1/browser/custom/learn")
async def custom_learn(request: CustomLearnRequest) -> dict[str, object]:
    """Derive a recipe from a results page the person produced and prove it by running it."""
    hosts = tuple(request.site.allowed_hosts)
    login_markers = tuple(request.site.login_path_markers)
    try:
        async with _custom_engine(request.user_id, request.site) as engine:
            final_url, page_html = await fetch_page_html(
                engine, request.results_url, hosts, login_markers
            )
            template = infer_url_template(final_url, query=request.query, location=request.location)
            if template is None:
                raise RecipeLearningError(
                    "Запрос не найден в адресе страницы (сайт, вероятно, ищет без "
                    "параметров в URL). "
                    "Задайте рецепт вручную"
                )
            learned = learn_selectors(page_html, page_url=final_url, allowed_hosts=hosts)
            recipe = validate_recipe(
                SearchRecipe(
                    url_template=template,
                    card_selector=learned.card_selector,
                    link_selector=learned.link_selector,
                    title_selector=learned.title_selector,
                    company_selector=learned.company_selector,
                ),
                hosts,
            )
            hits = await search_custom_site(
                engine,
                recipe=recipe,
                allowed_hosts=hosts,
                query=request.query,
                location=request.location,
                limit=10,
                login_path_markers=login_markers,
            )
    except (CustomSiteError, RecipeLearningError, InvalidSearchRecipe) as error:
        raise _custom_error(error) from error
    return {
        "recipe": recipe.to_dict(),
        "card_count": learned.card_count,
        "preview": [_hit_payload(hit) for hit in hits],
    }


@app.post("/v1/browser/custom/search")
async def custom_search(request: CustomSearchRequest) -> dict[str, object]:
    hosts = tuple(request.site.allowed_hosts)
    try:
        recipe = validate_recipe(SearchRecipe.from_dict(request.recipe), hosts)
        async with _custom_engine(request.user_id, request.site) as engine:
            hits = await search_custom_site(
                engine,
                recipe=recipe,
                allowed_hosts=hosts,
                query=request.search_text,
                location=request.locations[0] if request.locations else "",
                limit=request.limit,
                login_path_markers=tuple(request.site.login_path_markers),
            )
    except (CustomSiteError, InvalidSearchRecipe) as error:
        raise _custom_error(error) from error
    return {"hits": [_hit_payload(hit) for hit in hits]}


@app.post("/v1/browser/custom/extract")
async def custom_extract(request: CustomExtractRequest) -> dict[str, object]:
    try:
        async with _custom_engine(request.user_id, request.site) as engine:
            return await extract_custom_vacancy(
                engine, url=request.url, allowed_hosts=tuple(request.site.allowed_hosts)
            )
    except CustomSiteError as error:
        raise _custom_error(error) from error


# --- recording a search scenario on a user-defined site -----------------------------------


_recording_manager = SearchRecipeRecordingManager()


class CustomRecordStartRequest(StrictModel):
    user_id: str
    site: CustomSiteConfig
    start_url: str = Field(max_length=2000)


class CustomRecordStopRequest(StrictModel):
    user_id: str
    site: CustomSiteConfig


class CustomRecordCancelRequest(StrictModel):
    user_id: str
    site_key: str = Field(min_length=1, max_length=100)


class RecordedActionPayload(StrictModel):
    kind: str
    tag: str
    element_type: str
    element_id: str
    name: str
    role: str
    aria_label: str
    test_id: str
    placeholder: str
    label_text: str
    text: str
    value_preview: str
    value_length: int
    selector_candidates: list[dict[str, str]]


def _recording_error(error: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(error))


@app.post("/v1/browser/custom/record/start")
async def custom_record_start(request: CustomRecordStartRequest) -> dict[str, str]:
    """Open a visible session the person searches in themselves; every click and completed
    field edit is reported by a fixed, observational script - nothing on the page is acted on
    until the person tags the results and saves a draft."""
    settings = _get_settings()
    try:
        await _recording_manager.start(
            user_id=request.user_id,
            site_key=request.site.site_key,
            start_url=request.start_url,
            allowed_hosts=tuple(request.site.allowed_hosts),
            timeout_ms=settings.browser_timeout_ms,
            artifact_directory=settings.artifact_directory,
            storage_state=_restore_session(request.user_id, request.site.site_key),
        )
    except SearchRecordingError as error:
        raise _recording_error(error) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="Не удалось открыть окно записи. Проверьте установку Chromium",
        ) from error
    return {"state": "recording"}


@app.post("/v1/browser/custom/record/stop")
async def custom_record_stop(request: CustomRecordStopRequest) -> dict[str, object]:
    try:
        result = await _recording_manager.stop(
            user_id=request.user_id, site_key=request.site.site_key
        )
    except SearchRecordingError as error:
        raise _recording_error(error) from error
    actions = [
        RecordedActionPayload(
            kind=action.kind,
            tag=action.tag,
            element_type=action.element_type,
            element_id=action.element_id,
            name=action.name,
            role=action.role,
            aria_label=action.aria_label,
            test_id=action.test_id,
            placeholder=action.placeholder,
            label_text=action.label_text,
            text=action.description(),
            value_preview=action.value_preview if action.kind == "fill" else "",
            value_length=action.value_length,
            selector_candidates=[
                {"kind": candidate.kind, "value": candidate.value}
                for candidate in selector_candidates_for(action)
            ],
        ).model_dump()
        for action in result.actions
    ]
    learned_recipe: dict[str, object] | None = None
    try:
        learned = learn_selectors(
            result.final_html,
            page_url=result.final_url,
            allowed_hosts=tuple(request.site.allowed_hosts),
        )
        learned_recipe = {
            "card_selector": learned.card_selector,
            "link_selector": learned.link_selector,
            "title_selector": learned.title_selector,
            "company_selector": learned.company_selector,
            "card_count": learned.card_count,
        }
    except RecipeLearningError:
        learned_recipe = None
    return {
        "start_url": result.start_url,
        "final_url": result.final_url,
        "actions": actions,
        "learned_recipe": learned_recipe,
    }


@app.post("/v1/browser/custom/record/cancel")
async def custom_record_cancel(request: CustomRecordCancelRequest) -> dict[str, str]:
    await _recording_manager.cancel(user_id=request.user_id, site_key=request.site_key)
    return {"state": "cancelled"}
