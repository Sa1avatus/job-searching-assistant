"""HTTP client for the browser-worker's browser operations API.

The API container uses this client to delegate all Playwright-based browser
operations to the browser-worker, so the API no longer needs Playwright
or browser binaries installed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import httpx
import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BrowserSearchHit:
    source_url: str
    title: str
    company: str
    vacancy_id: str | None = None


@dataclass(frozen=True, slots=True)
class BrowserProbeResult:
    valid: bool
    details: str = ""


class BrowserWorkerClient:
    """Thin HTTP client for the browser-worker's browser operations."""

    def __init__(self, base_url: str, timeout: float = 120) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def search(
        self,
        *,
        source: str,
        user_id: str,
        search_text: str | None = None,
        locations: list[str] | None = None,
        limit: int = 15,
    ) -> list[BrowserSearchHit]:
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.post(
                f"{self._base_url}/v1/browser/search",
                json={
                    "source": source,
                    "user_id": user_id,
                    "search_text": search_text,
                    "locations": locations or [],
                    "limit": limit,
                },
            )
            response.raise_for_status()
        data = response.json()
        return [
            BrowserSearchHit(
                source_url=hit["source_url"],
                title=hit["title"],
                company=hit["company"],
                vacancy_id=hit.get("vacancy_id"),
            )
            for hit in data.get("hits", [])
        ]

    async def extract_headhunter(self, *, user_id: str, url: str) -> dict[str, object]:
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.post(
                f"{self._base_url}/v1/browser/extract-headhunter",
                json={"user_id": user_id, "url": url},
            )
            response.raise_for_status()
        return cast(dict[str, object], response.json())

    async def extract_linkedin(self, *, user_id: str, url: str) -> dict[str, object]:
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.post(
                f"{self._base_url}/v1/browser/extract-linkedin",
                json={"user_id": user_id, "url": url},
            )
            response.raise_for_status()
        return cast(dict[str, object], response.json())

    async def probe(self, *, user_id: str, site_key: str) -> BrowserProbeResult:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                f"{self._base_url}/v1/browser/probe",
                json={"user_id": user_id, "site_key": site_key},
            )
            response.raise_for_status()
        data = response.json()
        return BrowserProbeResult(
            valid=data["valid"],
            details=data.get("details", ""),
        )

    async def has_submitted_application(
        self,
        *,
        source: str,
        user_id: str,
        url: str,
    ) -> bool:
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.post(
                f"{self._base_url}/v1/browser/submission-probe",
                json={"source": source, "user_id": user_id, "url": url},
            )
            response.raise_for_status()
        data = response.json()
        return bool(data["submitted"])

    async def login_start(
        self,
        *,
        user_id: str,
        site_key: str,
        site: dict[str, object],
    ) -> dict[str, object]:
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.post(
                f"{self._base_url}/v1/browser/login/start",
                json={"user_id": user_id, "site_key": site_key, "site": site},
            )
            response.raise_for_status()
        return cast(dict[str, object], response.json())

    async def login_confirm(
        self,
        *,
        user_id: str,
        site_key: str,
        adapter_name: str,
    ) -> dict[str, object]:
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.post(
                f"{self._base_url}/v1/browser/login/confirm",
                json={"user_id": user_id, "site_key": site_key, "adapter_name": adapter_name},
            )
            response.raise_for_status()
        return cast(dict[str, object], response.json())

    async def login_cancel(self, *, user_id: str, site_key: str) -> None:
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.post(
                f"{self._base_url}/v1/browser/login/cancel",
                json={"user_id": user_id, "site_key": site_key},
            )
            response.raise_for_status()

    async def login_is_waiting(self, *, user_id: str, site_key: str) -> bool:
        async with httpx.AsyncClient(
            timeout=10, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.get(
                f"{self._base_url}/v1/browser/login/status",
                params={"user_id": user_id, "site_key": site_key},
            )
            response.raise_for_status()
        return bool(response.json().get("is_waiting", False))
