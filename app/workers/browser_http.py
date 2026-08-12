"""HTTP API for the browser-worker, exposing browser operations over HTTP
so the API container no longer needs Playwright or browser binaries."""
from __future__ import annotations

import asyncio
import dataclasses
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from adapters.job_boards.headhunter_browser import HeadHunterBrowserAdapter
from adapters.job_boards.linkedin_browser import LinkedInBrowserAdapter
from app.browser.engine import PlaywrightEngine
from app.browser.selector_library import SelectorLibrary
from app.browser.session_probe import probe_browser_session
from app.browser.session_service import BrowserSessionService
from app.browser.session_store import EncryptedBrowserStateStore
from app.config import Settings, get_settings
from app.observability.logging import configure_logging
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


def _restore_session(user_id: str, site_key: str) -> dict | None:
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
            _restored_row, state = BrowserSessionService(
                session, _get_store()
            ).restore(row.id)
            return state
        except Exception:
            return None


from sqlalchemy import select


@asynccontextmanager
async def lifespan(app: FastAPI):
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
            adapter = HeadHunterBrowserAdapter(browser_engine)
            raw_hits = await adapter.search(
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
                for hit in raw_hits
            ]
        else:
            adapter = LinkedInBrowserAdapter(browser_engine)
            raw_hits = await adapter.search(
                text=request.search_text or "",
                location_names=request.locations or None,
                limit=request.limit,
            )
            hits = [
                SearchHit(
                    source_url=hit.source_url,
                    title=hit.title,
                    company=hit.company,
                )
                for hit in raw_hits
            ]

    return SearchResponse(hits=hits)


@app.post("/v1/browser/extract-headhunter")
async def extract_headhunter_vacancy(
    request: ExtractHeadhunterRequest,
) -> dict:
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
        return {"title": extracted.title, "company": extracted.company,
                "description_text": extracted.description_text, "source_url": extracted.source_url}


class ExtractHeadhunterRequest(StrictModel):
    user_id: str
    url: str


class ExtractLinkedinRequest(StrictModel):
    user_id: str
    url: str


@app.post("/v1/browser/extract-linkedin")
async def extract_linkedin_vacancy(
    request: ExtractLinkedinRequest,
) -> dict:
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
        return {"title": extracted.title, "company": extracted.company,
                "description_text": extracted.description_text, "source_url": extracted.source_url}


@app.post("/v1/browser/probe", response_model=ProbeResponse)
async def browser_probe(request: ProbeRequest) -> ProbeResponse:
    settings = _get_settings()
    state = _restore_session(request.user_id, request.site_key)
    if state is None:
        return ProbeResponse(valid=False, details="No stored session found")

    try:
        result = await probe_browser_session(
            site_key=request.site_key,
            storage_state=state,
            headless=settings.browser_headless,
            timeout_ms=settings.browser_timeout_ms,
        )
        return ProbeResponse(valid=result.is_valid, details=result.status_detail)
    except Exception as error:
        return ProbeResponse(valid=False, details=f"{type(error).__name__}: {error}")
