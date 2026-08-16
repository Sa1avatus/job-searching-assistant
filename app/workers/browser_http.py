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
from app.browser.engine import PlaywrightEngine
from app.browser.selector_library import SelectorLibrary
from app.browser.session_probe import probe_browser_session
from app.browser.session_service import BrowserSessionService
from app.browser.session_store import EncryptedBrowserStateStore, InvalidBrowserState
from app.config import Settings, get_settings
from app.observability.logging import configure_logging
from app.services.browser_authorization import (
    BrowserAuthorizationError,
    BrowserAuthorizationManager,
    BrowserAuthorizationSite,
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


class ExtractHeadhunterRequest(StrictModel):
    user_id: str
    url: str


class ExtractLinkedinRequest(StrictModel):
    user_id: str
    url: str


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
