from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.job_boards import linkedin_browser
from adapters.job_boards.headhunter_browser import HeadHunterBrowserAdapter
from adapters.job_boards.linkedin_browser import LinkedInBrowserAdapter


@pytest.mark.asyncio
async def test_headhunter_submission_probe_reads_marker_without_clicking() -> None:
    page = MagicMock()
    page.url = "https://hh.ru/vacancy/123"
    page.locator.return_value.count = AsyncMock(return_value=1)
    page.get_by_text.return_value.count = AsyncMock(return_value=0)
    engine = MagicMock()
    engine.new_page = AsyncMock(return_value=page)
    engine.navigate = AsyncMock(
        return_value=SimpleNamespace(is_successful=True, error_category=None)
    )
    adapter = HeadHunterBrowserAdapter(engine)
    adapter._raise_if_captcha = AsyncMock()
    adapter._raise_if_logged_out = AsyncMock()

    result = await adapter.has_submitted_application("https://hh.ru/vacancy/123")

    assert result is True
    page.locator.return_value.click.assert_not_called()
    page.get_by_role.assert_not_called()


@pytest.mark.asyncio
async def test_linkedin_submission_probe_reads_marker_without_clicking(monkeypatch) -> None:
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/view/123"
    engine = MagicMock()
    engine.new_page = AsyncMock(return_value=page)
    engine.navigate = AsyncMock(
        return_value=SimpleNamespace(is_successful=True, error_category=None)
    )
    marker_probe = AsyncMock(return_value=True)
    monkeypatch.setattr(linkedin_browser, "has_submitted_application_marker", marker_probe)
    adapter = LinkedInBrowserAdapter(engine)
    adapter._raise_if_challenge_url = AsyncMock()

    result = await adapter.has_submitted_application("https://www.linkedin.com/jobs/view/123")

    assert result is True
    marker_probe.assert_awaited_once_with(page)
    page.get_by_role.assert_not_called()
    page.locator.return_value.click.assert_not_called()
