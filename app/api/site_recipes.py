"""Search recipes for user-defined sites: learn from a results page, test, activate, roll back."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.search_recipe import InvalidSearchRecipe, SearchRecipe
from app.services.browser_worker_client import BrowserWorkerClient, BrowserWorkerRejected
from app.services.recruitment import EntityNotFoundError
from app.services.site_search_recipes import (
    RecipeNotVerified,
    activate,
    archive_recipe,
    get_recipe,
    get_site,
    list_recipes,
    record_verification,
    save_draft,
    site_payload,
)
from app.storage.database import session_scope
from app.storage.tables import SiteSearchRecipeRow

router = APIRouter(
    prefix="/v1/users/{user_id}/site-definitions/{site_definition_id}/search-recipe",
    tags=["site-search-recipes"],
)


class RecipeBody(BaseModel):
    url_template: str = Field(default="", max_length=2000)
    card_selector: str = Field(max_length=300)
    link_selector: str = Field(default="", max_length=300)
    title_selector: str = Field(default="", max_length=300)
    company_selector: str = Field(default="", max_length=300)
    # A recorded alternative to url_template: navigate/fill/click steps that reach the results
    # page. Validated into typed steps by SearchRecipe.from_dict/validate_recipe, not here.
    reach_steps: list[dict[str, object]] = Field(default_factory=list, max_length=30)


class LearnRequest(BaseModel):
    results_url: str = Field(min_length=8, max_length=2000)
    query: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=200)


class TestRequest(BaseModel):
    __test__ = False  # not a pytest class

    query: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=200)


class RecordStartRequest(BaseModel):
    start_url: str = Field(min_length=8, max_length=2000)


class RecordedActionResponse(BaseModel):
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


class LearnedCardSelectors(BaseModel):
    card_selector: str
    link_selector: str
    title_selector: str
    company_selector: str
    card_count: int


class RecordStopResponse(BaseModel):
    start_url: str
    final_url: str
    actions: list[RecordedActionResponse]
    learned_recipe: LearnedCardSelectors | None


class RecipeVersion(BaseModel):
    version: int
    status: str
    recipe: RecipeBody
    learned_from_url: str | None
    preview: list[dict[str, str]]
    verified_at: datetime | None
    created_at: datetime


def _version(row: SiteSearchRecipeRow) -> RecipeVersion:
    return RecipeVersion(
        version=row.version,
        status=row.status,
        recipe=RecipeBody.model_validate(row.recipe),
        learned_from_url=row.learned_from_url,
        preview=list(row.preview),
        verified_at=row.verified_at,
        created_at=row.created_at,
    )


def _client() -> BrowserWorkerClient:
    return BrowserWorkerClient(get_settings().browser_worker_url)


def _worker_error(error: Exception) -> HTTPException:
    if isinstance(error, BrowserWorkerRejected):
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(status_code=502, detail="Браузерный воркер недоступен")


def _not_found(error: EntityNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(error))


def _preview_rows(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    return [
        {str(key): str(value) for key, value in hit.items()} for hit in raw if isinstance(hit, dict)
    ]


@router.get("", response_model=list[RecipeVersion])
def list_versions(
    user_id: str, site_definition_id: str, session: Annotated[Session, Depends(session_scope)]
) -> list[RecipeVersion]:
    try:
        site = get_site(session, user_id, site_definition_id)
    except EntityNotFoundError as error:
        raise _not_found(error) from error
    return [_version(row) for row in list_recipes(session, site)]


@router.put("/draft", response_model=RecipeVersion)
def put_draft(
    user_id: str,
    site_definition_id: str,
    request: RecipeBody,
    session: Annotated[Session, Depends(session_scope)],
) -> RecipeVersion:
    """Save a hand-written or recorded recipe as an unverified draft."""
    try:
        site = get_site(session, user_id, site_definition_id)
        row = save_draft(session, site, SearchRecipe.from_dict(request.model_dump()))
    except EntityNotFoundError as error:
        raise _not_found(error) from error
    except InvalidSearchRecipe as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _version(row)


@router.post("/learn", response_model=RecipeVersion, status_code=201)
async def learn_recipe(
    user_id: str,
    site_definition_id: str,
    request: LearnRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> RecipeVersion:
    """Derive a draft from a results page the person already searched for on the site."""
    try:
        site = get_site(session, user_id, site_definition_id)
    except EntityNotFoundError as error:
        raise _not_found(error) from error
    try:
        learned = await _client().custom_learn(
            user_id=user_id,
            site=site_payload(site),
            results_url=request.results_url,
            query=request.query,
            location=request.location,
        )
    except (BrowserWorkerRejected, httpx.HTTPError) as error:
        raise _worker_error(error) from error
    try:
        row = save_draft(
            session,
            site,
            SearchRecipe.from_dict(learned.get("recipe")),
            learned_from_url=request.results_url,
            preview=_preview_rows(learned.get("preview")),
        )
    except InvalidSearchRecipe as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _version(row)


@router.post("/record/start", status_code=202)
async def start_recording(
    user_id: str,
    site_definition_id: str,
    request: RecordStartRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> dict[str, str]:
    """Open a visible session the person searches in themselves; nothing is saved yet."""
    try:
        site = get_site(session, user_id, site_definition_id)
    except EntityNotFoundError as error:
        raise _not_found(error) from error
    try:
        await _client().custom_record_start(
            user_id=user_id, site=site_payload(site), start_url=request.start_url
        )
    except (BrowserWorkerRejected, httpx.HTTPError) as error:
        raise _worker_error(error) from error
    return {"state": "recording"}


@router.post("/record/stop", response_model=RecordStopResponse)
async def stop_recording(
    user_id: str,
    site_definition_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> RecordStopResponse:
    """End the recording and return what was observed, for the person to tag before saving."""
    try:
        site = get_site(session, user_id, site_definition_id)
    except EntityNotFoundError as error:
        raise _not_found(error) from error
    try:
        result = await _client().custom_record_stop(user_id=user_id, site=site_payload(site))
    except (BrowserWorkerRejected, httpx.HTTPError) as error:
        raise _worker_error(error) from error
    learned = result.get("learned_recipe")
    raw_actions = result.get("actions")
    return RecordStopResponse(
        start_url=str(result.get("start_url", "")),
        final_url=str(result.get("final_url", "")),
        actions=[
            RecordedActionResponse(**action)
            for action in (raw_actions if isinstance(raw_actions, list) else [])
            if isinstance(action, dict)
        ],
        learned_recipe=LearnedCardSelectors(**learned) if isinstance(learned, dict) else None,
    )


@router.post("/record/cancel")
async def cancel_recording(
    user_id: str,
    site_definition_id: str,
    session: Annotated[Session, Depends(session_scope)],
) -> dict[str, str]:
    try:
        site = get_site(session, user_id, site_definition_id)
    except EntityNotFoundError as error:
        raise _not_found(error) from error
    await _client().custom_record_cancel(user_id=user_id, site_key=site.site_key)
    return {"state": "cancelled"}


@router.post("/{version}/test", response_model=RecipeVersion)
async def test_recipe(
    user_id: str,
    site_definition_id: str,
    version: int,
    request: TestRequest,
    session: Annotated[Session, Depends(session_scope)],
) -> RecipeVersion:
    """Run the recipe for real; only a run that returns results makes it eligible to activate."""
    try:
        site = get_site(session, user_id, site_definition_id)
        row = get_recipe(session, site, version)
    except EntityNotFoundError as error:
        raise _not_found(error) from error
    try:
        hits = await _client().custom_search(
            user_id=user_id,
            site=site_payload(site),
            recipe=dict(row.recipe),
            search_text=request.query,
            locations=[request.location] if request.location else [],
            limit=10,
        )
    except (BrowserWorkerRejected, httpx.HTTPError) as error:
        raise _worker_error(error) from error
    preview = [
        {"source_url": hit.source_url, "title": hit.title, "company": hit.company} for hit in hits
    ]
    return _version(record_verification(session, row, preview))


@router.post("/{version}/activate", response_model=RecipeVersion)
def activate_recipe(
    user_id: str,
    site_definition_id: str,
    version: int,
    session: Annotated[Session, Depends(session_scope)],
) -> RecipeVersion:
    try:
        site = get_site(session, user_id, site_definition_id)
        row = activate(session, site, version)
    except EntityNotFoundError as error:
        raise _not_found(error) from error
    except (RecipeNotVerified, InvalidSearchRecipe) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _version(row)


@router.post("/{version}/archive", response_model=RecipeVersion)
def archive_recipe_version(
    user_id: str,
    site_definition_id: str,
    version: int,
    session: Annotated[Session, Depends(session_scope)],
) -> RecipeVersion:
    try:
        site = get_site(session, user_id, site_definition_id)
        row = archive_recipe(session, site, version)
    except EntityNotFoundError as error:
        raise _not_found(error) from error
    return _version(row)
