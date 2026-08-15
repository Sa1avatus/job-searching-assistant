"""HTTP-backed adapters that proxy browser operations to the browser-worker.

These implement the same interface as HeadHunterBrowserAdapter /
LinkedInBrowserAdapter but delegate all Playwright interactions to the
browser-worker's HTTP API, so the API container doesn't need Playwright."""

from __future__ import annotations

from typing import cast

import structlog

from adapters.job_boards.headhunter_browser import (
    ExtractedHeadHunterVacancy,
    HeadHunterSearchHit,
)
from adapters.job_boards.linkedin_browser import ExtractedLinkedInVacancy, LinkedInSearchHit
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


class _ExtractedVacancy:
    """Wraps the dict returned by browser-worker extract endpoints
    to match the interface expected by JobDiscoveryService."""

    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> object:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._data.get(name)
