"""HTTP-backed adapters that proxy browser operations to the browser-worker.

These implement the same interface as HeadHunterBrowserAdapter /
LinkedInBrowserAdapter but delegate all Playwright interactions to the
browser-worker's HTTP API, so the API container doesn't need Playwright."""
from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.services.browser_worker_client import BrowserWorkerClient

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _HeadHunterSearchHit:
    source_url: str
    title: str
    company: str
    vacancy_id: str | None = None


@dataclass(frozen=True, slots=True)
class _LinkedInSearchHit:
    source_url: str
    title: str
    company: str


class HttpHeadHunterAdapter:
    """Proxy for HeadHunterBrowserAdapter over HTTP."""

    def __init__(self, client: BrowserWorkerClient, user_id: str) -> None:
        self._client = client
        self._user_id = user_id

    async def search(
        self, *, text: str, location_names: list[str] | None = None, limit: int = 15
    ) -> list[_HeadHunterSearchHit]:
        hits = await self._client.search(
            source="headhunter",
            user_id=self._user_id,
            search_text=text,
            locations=location_names,
            limit=limit,
        )
        return [
            _HeadHunterSearchHit(
                source_url=h.source_url,
                title=h.title,
                company=h.company,
                vacancy_id=h.vacancy_id,
            )
            for h in hits
        ]

    async def extract_vacancy(self, url: str) -> object:
        data = await self._client.extract_headhunter(
            user_id=self._user_id, url=url
        )
        return _ExtractedVacancy(data)


class HttpLinkedInAdapter:
    """Proxy for LinkedInBrowserAdapter over HTTP."""

    def __init__(self, client: BrowserWorkerClient, user_id: str) -> None:
        self._client = client
        self._user_id = user_id

    async def search(
        self, *, text: str, location_names: list[str] | None = None, limit: int = 15
    ) -> list[_LinkedInSearchHit]:
        hits = await self._client.search(
            source="linkedin",
            user_id=self._user_id,
            search_text=text,
            locations=location_names,
            limit=limit,
        )
        return [
            _LinkedInSearchHit(
                source_url=h.source_url,
                title=h.title,
                company=h.company,
            )
            for h in hits
        ]

    async def extract_vacancy(self, url: str) -> object:
        data = await self._client.extract_linkedin(
            user_id=self._user_id, url=url
        )
        return _ExtractedVacancy(data)


class _ExtractedVacancy:
    """Wraps the dict returned by browser-worker extract endpoints
    to match the interface expected by JobDiscoveryService."""

    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> object:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._data.get(name)
