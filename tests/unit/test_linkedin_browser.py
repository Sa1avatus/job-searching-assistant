from collections.abc import Iterable
from typing import cast

import pytest
from playwright.async_api import Page

from adapters.job_boards.linkedin_browser import LinkedInBrowserAdapter
from app.browser.engine import BrowserActionResult, PlaywrightEngine
from app.domain.failures import FailureCategory


def _navigation_result(
    *,
    successful: bool,
    category: FailureCategory | None = None,
    should_retry: bool = False,
) -> BrowserActionResult:
    return BrowserActionResult(
        action_name="navigate",
        target="https://www.linkedin.com/jobs/search/",
        is_successful=successful,
        duration_ms=1,
        resulting_url="https://www.linkedin.com/jobs/search/",
        error_category=category,
        should_retry=should_retry,
    )


class _NavigationEngine:
    def __init__(self, results: Iterable[BrowserActionResult]) -> None:
        self.results = iter(results)
        self.calls: list[tuple[object, str]] = []

    async def navigate(self, page: object, url: str) -> BrowserActionResult:
        self.calls.append((page, url))
        return next(self.results)


@pytest.mark.asyncio
async def test_linkedin_search_navigation_retries_one_transient_failure() -> None:
    engine = _NavigationEngine(
        (
            _navigation_result(
                successful=False,
                category=FailureCategory.TRANSIENT_NETWORK_ERROR,
                should_retry=True,
            ),
            _navigation_result(successful=True),
        )
    )
    adapter = LinkedInBrowserAdapter(cast(PlaywrightEngine, engine))
    page = cast(Page, object())

    result = await adapter._navigate_search_with_retry(page, "https://example.test/search")

    assert result.is_successful is True
    assert len(engine.calls) == 2
    assert engine.calls[0] == engine.calls[1]


@pytest.mark.asyncio
async def test_linkedin_search_navigation_retries_only_once() -> None:
    transient_failure = _navigation_result(
        successful=False,
        category=FailureCategory.TRANSIENT_NETWORK_ERROR,
        should_retry=True,
    )
    engine = _NavigationEngine((transient_failure, transient_failure))
    adapter = LinkedInBrowserAdapter(cast(PlaywrightEngine, engine))

    result = await adapter._navigate_search_with_retry(
        cast(Page, object()), "https://example.test/search"
    )

    assert result.is_successful is False
    assert len(engine.calls) == 2


@pytest.mark.asyncio
async def test_linkedin_search_navigation_does_not_retry_other_failures() -> None:
    engine = _NavigationEngine(
        (
            _navigation_result(
                successful=False,
                category=FailureCategory.AUTHENTICATION_FAILURE,
                should_retry=False,
            ),
        )
    )
    adapter = LinkedInBrowserAdapter(cast(PlaywrightEngine, engine))

    result = await adapter._navigate_search_with_retry(
        cast(Page, object()), "https://example.test/search"
    )

    assert result.is_successful is False
    assert len(engine.calls) == 1
