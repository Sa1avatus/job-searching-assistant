"""HTTP-backed adapters that proxy browser operations to the browser-worker.

These implement the same interface as HeadHunterBrowserAdapter /
LinkedInBrowserAdapter but delegate all Playwright interactions to the
browser-worker's HTTP API, so the API container doesn't need Playwright."""

from __future__ import annotations

from datetime import datetime
from typing import cast

import structlog

from adapters.job_boards.headhunter_browser import (
    ExtractedHeadHunterVacancy,
    HeadHunterSearchHit,
)
from adapters.job_boards.linkedin_browser import ExtractedLinkedInVacancy, LinkedInSearchHit
from app.domain.forms import FieldConstraints, FormField, FormFieldType
from app.services.browser_worker_client import BrowserWorkerClient

logger = structlog.get_logger(__name__)


class HttpHeadHunterAdapter:
    """Proxy for HeadHunterBrowserAdapter over HTTP."""

    def __init__(self, client: BrowserWorkerClient, user_id: str) -> None:
        self._client = client
        self._user_id = user_id

    async def search(
        self, *, text: str, location_names: list[str] | None = None, limit: int = 15
    ) -> list[HeadHunterSearchHit]:
        hits = await self._client.search(
            source="headhunter",
            user_id=self._user_id,
            search_text=text,
            locations=location_names,
            limit=limit,
        )
        return [
            HeadHunterSearchHit(
                vacancy_id=h.vacancy_id,
                source_url=h.source_url,
                title=h.title,
                company=h.company,
            )
            for h in hits
            if h.vacancy_id is not None
        ]

    async def extract_vacancy(self, url: str) -> ExtractedHeadHunterVacancy:
        data = await self._client.extract_headhunter(user_id=self._user_id, url=url)
        return cast(ExtractedHeadHunterVacancy, _ExtractedVacancy(data))

    async def has_submitted_application(self, url: str) -> bool:
        return await self._client.has_submitted_application(
            source="headhunter", user_id=self._user_id, url=url
        )


class HttpCustomSiteAdapter:
    """Search and extraction for a user-defined site through its active search recipe."""

    def __init__(
        self,
        client: BrowserWorkerClient,
        user_id: str,
        site: dict[str, object],
        recipe: dict[str, object],
    ) -> None:
        self._client = client
        self._user_id = user_id
        self._site = site
        self._recipe = recipe

    async def search(
        self, *, text: str, location_names: list[str] | None = None, limit: int = 15
    ) -> list[HeadHunterSearchHit]:
        hits = await self._client.custom_search(
            user_id=self._user_id,
            site=self._site,
            recipe=self._recipe,
            search_text=text,
            locations=location_names,
            limit=limit,
        )
        return [
            HeadHunterSearchHit(
                vacancy_id=hit.source_url,
                source_url=hit.source_url,
                title=hit.title,
                company=hit.company,
            )
            for hit in hits
        ]

    async def extract_vacancy(self, url: str) -> ExtractedHeadHunterVacancy:
        data = await self._client.custom_extract(user_id=self._user_id, site=self._site, url=url)
        return cast(ExtractedHeadHunterVacancy, _ExtractedVacancy(data))


class HttpLinkedInAdapter:
    """Proxy for LinkedInBrowserAdapter over HTTP."""

    def __init__(self, client: BrowserWorkerClient, user_id: str) -> None:
        self._client = client
        self._user_id = user_id

    async def search(
        self, *, text: str, location_names: list[str] | None = None, limit: int = 15
    ) -> list[LinkedInSearchHit]:
        hits = await self._client.search(
            source="linkedin",
            user_id=self._user_id,
            search_text=text,
            locations=location_names,
            limit=limit,
        )
        return [
            LinkedInSearchHit(
                job_id=h.vacancy_id,
                source_url=h.source_url,
                title=h.title,
                company=h.company,
            )
            for h in hits
            if h.vacancy_id is not None
        ]

    async def extract_vacancy(self, url: str) -> ExtractedLinkedInVacancy:
        data = await self._client.extract_linkedin(user_id=self._user_id, url=url)
        return cast(ExtractedLinkedInVacancy, _ExtractedVacancy(data))

    async def has_submitted_application(self, url: str) -> bool:
        return await self._client.has_submitted_application(
            source="linkedin", user_id=self._user_id, url=url
        )


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _constraints(data: object) -> FieldConstraints:
    if not isinstance(data, dict):
        return FieldConstraints()
    values = dict(data)
    values["accepted_file_types"] = tuple(values.get("accepted_file_types") or ())
    return FieldConstraints(**values)


def _form_field(data: object) -> FormField:
    if isinstance(data, FormField):
        return data
    if not isinstance(data, dict):
        raise TypeError(f"Unexpected form field payload: {type(data).__name__}")
    constraints = data.get("constraints")
    return FormField(
        field_id=str(data.get("field_id", "")),
        label=str(data.get("label", "")),
        field_type=FormFieldType(str(data.get("field_type", FormFieldType.UNKNOWN.value))),
        is_required=bool(data.get("is_required", False)),
        options=tuple(str(option) for option in data.get("options") or ()),
        current_value=data.get("current_value"),
        semantic_category=str(data.get("semantic_category", "custom")),
        confidence=float(data.get("confidence", 0.0)),
        source_locator=str(data.get("source_locator", "")),
        constraints=_constraints(constraints),
        locator_candidates=tuple(str(item) for item in data.get("locator_candidates") or ()),
    )


class _ExtractedVacancy:
    """Wraps the JSON returned by browser-worker extract endpoints.

    JSON loses types (datetimes become ISO strings, enums plain strings, dataclasses dicts), so
    they are rebuilt here to match what ``JobDiscoveryService`` reads from the in-process adapters.
    """

    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> object:
        if name.startswith("_"):
            raise AttributeError(name)
        value = self._data.get(name)
        if name == "published_at":
            return _parse_datetime(value)
        if name == "form_fields":
            return tuple(_form_field(item) for item in value or ())  # type: ignore[attr-defined]
        if name == "required_skills":
            return tuple(str(item) for item in value or ())  # type: ignore[attr-defined]
        return value
