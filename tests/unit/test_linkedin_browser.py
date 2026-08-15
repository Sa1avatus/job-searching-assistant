from collections.abc import Iterable
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import adapters.job_boards.linkedin_browser as linkedin_browser_module
from adapters.job_boards.linkedin_browser import (
    LinkedInBrowserAdapter,
    clean_linkedin_description_text,
    has_submitted_application_marker,
    is_meaningful_linkedin_description,
    select_best_linkedin_description,
)
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


def test_linkedin_description_cleaner_removes_premium_chrome() -> None:
    raw = """Job search smarter with Premium
See jobs where you'd be a top applicant
About the job
About The Role
Build secure distributed systems.
Requirements
Experience with AWS, PostgreSQL and Kubernetes."""

    cleaned = clean_linkedin_description_text(raw)

    assert cleaned.startswith("About The Role")
    assert "Premium" not in cleaned
    assert "Experience with AWS" in cleaned
    assert is_meaningful_linkedin_description(cleaned)


def test_linkedin_premium_stub_is_not_a_meaningful_description() -> None:
    stub = (
        "Job search smarter with Premium "
        "See jobs where you'd be a top applicant "
        "Message hiring managers with InMail"
    )

    assert not is_meaningful_linkedin_description(stub)


def test_linkedin_description_cleaner_drops_footer_and_premium_only_page() -> None:
    raw = """TechX Corp.
AI/ML Architect
See jobs where you'd be a top applicant
Get personalized cover letter and resume tips
Try Premium for $0
Looking for talent?
Post a job
Accessibility
Talent Solutions
Community Guidelines"""

    cleaned = clean_linkedin_description_text(raw)

    assert "Looking for talent?" not in cleaned
    assert not is_meaningful_linkedin_description(cleaned)


def test_linkedin_description_selector_prefers_full_vacancy_over_promo() -> None:
    promo = """See jobs where you’d be a top applicant Plus!
Get insider access to live talks with industry leaders.
1-month free trial. Easy to cancel.
We'll remind you 7 days before your trial ends."""
    full_description = """About the job
About the role
Build and evaluate production LLM systems for enterprise customers.
Responsibilities
Design evaluation datasets and improve prompt quality across products.
Requirements
Three years of Python experience and hands-on work with LLM evaluation."""

    selected = select_best_linkedin_description((promo, full_description))

    assert selected.startswith("About the role")
    assert "Responsibilities" in selected
    assert "Three years of Python experience" in selected
    assert "free trial" not in selected.casefold()


@pytest.mark.asyncio
async def test_linkedin_description_waits_for_meaningful_semantic_container() -> None:
    marker = MagicMock()
    marker.evaluate = AsyncMock(
        return_value=(
            "About the job\nResponsibilities\nBuild production evaluation systems.\n"
            "Requirements\nExperience with Python, LLMs, and evaluation datasets."
        )
    )
    markers = MagicMock()
    markers.count = AsyncMock(return_value=1)
    markers.nth.return_value = marker
    page = MagicMock()
    page.get_by_text.return_value = markers
    page.wait_for_timeout = AsyncMock()

    candidates = await LinkedInBrowserAdapter._semantic_description_candidates(cast(Page, page))

    assert select_best_linkedin_description(candidates).startswith("Responsibilities")
    page.wait_for_timeout.assert_not_awaited()


@pytest.mark.asyncio
async def test_linkedin_submitted_marker_waits_for_dynamic_status() -> None:
    marker = MagicMock()
    marker.wait_for = AsyncMock()
    text_locator = MagicMock()
    text_locator.first = marker
    page = MagicMock()
    page.get_by_text.return_value = text_locator

    assert await has_submitted_application_marker(cast(Page, page))
    marker.wait_for.assert_awaited_once_with(state="visible", timeout=5_000)


@pytest.mark.asyncio
async def test_linkedin_submitted_marker_falls_back_to_visible_page_text() -> None:
    marker = MagicMock()
    marker.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("not ready"))
    text_locator = MagicMock()
    text_locator.first = marker
    body = MagicMock()
    body.inner_text = AsyncMock(
        return_value="Application status\nApplication submitted\n2 hours ago"
    )
    page = MagicMock()
    page.get_by_text.return_value = text_locator
    page.locator.return_value = body

    assert await has_submitted_application_marker(cast(Page, page), timeout_ms=1)


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


@pytest.mark.asyncio
async def test_search_closes_page_on_zero_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/search/?keywords=missing"
    page.close = AsyncMock()
    cards = MagicMock()
    cards.count = AsyncMock(return_value=0)
    result_targets = MagicMock()
    result_targets.first.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("no results"))
    page.locator.side_effect = [cards, result_targets]
    engine = MagicMock()
    engine.artifact_directory = Path(".artifacts")
    engine.new_page = AsyncMock(return_value=page)
    engine.navigate = AsyncMock(return_value=_navigation_result(successful=True))
    capture_failure = AsyncMock()
    monkeypatch.setattr(linkedin_browser_module, "capture_browser_failure", capture_failure)
    adapter = LinkedInBrowserAdapter(cast(PlaywrightEngine, engine))

    result = await adapter.search(text="missing")

    assert result == []
    page.close.assert_awaited_once()
    capture_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_closes_page_on_navigation_failure() -> None:
    page = MagicMock()
    page.close = AsyncMock()
    engine = MagicMock()
    engine.new_page = AsyncMock(return_value=page)
    engine.navigate = AsyncMock(
        return_value=_navigation_result(
            successful=False,
            category=FailureCategory.AUTHENTICATION_FAILURE,
        )
    )
    adapter = LinkedInBrowserAdapter(cast(PlaywrightEngine, engine))

    with pytest.raises(RuntimeError, match="LinkedIn search navigation failed"):
        await adapter.search(text="test")

    page.close.assert_awaited_once()
