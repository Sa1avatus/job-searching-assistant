from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.job_boards.headhunter_browser import (
    HeadHunterBrowserAdapter,
    _vacancy_id_from_href,
    resolve_known_area_ids,
)


def test_resolve_known_area_ids_empty_means_anywhere() -> None:
    assert resolve_known_area_ids([]) == []
    assert resolve_known_area_ids(["anywhere"]) == []
    assert resolve_known_area_ids(["везде"]) == []


def test_resolve_known_area_ids_matches_known_cities() -> None:
    assert resolve_known_area_ids(["Москва"]) == ["1"]
    assert resolve_known_area_ids(["москва", "спб"]) == ["1", "2"]


def test_resolve_known_area_ids_ignores_unknown_names() -> None:
    assert resolve_known_area_ids(["Атлантида"]) == []


def test_vacancy_id_from_href_extracts_numeric_id() -> None:
    assert _vacancy_id_from_href("/vacancy/12345678?query=1") == "12345678"
    assert _vacancy_id_from_href("https://hh.ru/vacancy/999") == "999"


def test_vacancy_id_from_href_returns_none_when_absent() -> None:
    assert _vacancy_id_from_href("/search/vacancy?text=python") is None


@pytest.mark.asyncio
async def test_search_closes_page_on_zero_results() -> None:
    mock_page = MagicMock()
    mock_page.url = "https://hh.ru/search/vacancy?text=python"
    mock_page.title = AsyncMock(return_value="Search results")
    mock_page.close = AsyncMock()

    mock_locator = MagicMock()
    mock_locator.count = AsyncMock(return_value=0)
    mock_page.locator.return_value = mock_locator

    navigation = MagicMock()
    navigation.is_successful = True

    mock_engine = AsyncMock()
    mock_engine.new_page.return_value = mock_page
    mock_engine.navigate.return_value = navigation

    adapter = HeadHunterBrowserAdapter(mock_engine)
    hits = await adapter.search(text="python")

    assert hits == []
    mock_page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_closes_page_on_navigation_failure() -> None:
    mock_page = MagicMock()
    mock_page.url = ""
    mock_page.close = AsyncMock()

    navigation = MagicMock()
    navigation.is_successful = False
    navigation.error_category = "timeout"

    mock_engine = AsyncMock()
    mock_engine.new_page.return_value = mock_page
    mock_engine.navigate.return_value = navigation

    adapter = HeadHunterBrowserAdapter(mock_engine)

    with pytest.raises(RuntimeError, match="hh.ru search navigation failed"):
        await adapter.search(text="python")

    mock_page.close.assert_awaited_once()


def _cross_country_page(*, dialog_present: bool) -> tuple[MagicMock, MagicMock]:
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()

    heading = MagicMock()
    heading.count = AsyncMock(return_value=1)

    continue_button = MagicMock()
    continue_button.count = AsyncMock(return_value=1)
    continue_button.is_visible = AsyncMock(return_value=True)

    dialog = MagicMock()
    dialog.is_visible = AsyncMock(return_value=True)
    dialog.get_by_text.return_value = heading
    dialog.locator.return_value.first = continue_button

    dialogs = MagicMock()
    dialogs.count = AsyncMock(return_value=1 if dialog_present else 0)
    dialogs.nth.return_value = dialog
    page.locator.return_value = dialogs
    return page, continue_button


def test_headhunter_apply_checks_cross_country_dialog_before_cover_letter() -> None:
    source = HeadHunterBrowserAdapter.apply.__code__
    names = source.co_names

    assert names.index("_handle_cross_country_dialog") < names.index(
        "_find_cover_letter_field"
    )


@pytest.mark.asyncio
async def test_cross_country_dialog_clicks_continue_inside_dialog() -> None:
    page, continue_button = _cross_country_page(dialog_present=True)
    action = MagicMock()
    action.is_successful = True
    adapter = HeadHunterBrowserAdapter(AsyncMock())
    adapter._click = AsyncMock(return_value=action)

    result = await adapter._handle_cross_country_dialog(page)

    assert result is action
    adapter._click.assert_awaited_once_with(
        page,
        continue_button,
        "continue-cross-country-application",
        already_ok=False,
    )
    dialog = page.locator.return_value.nth.return_value
    dialog.locator.assert_called_once_with(
        "[data-qa='relocation-warning-confirm']"
    )
    dialog.get_by_role.assert_not_called()


@pytest.mark.asyncio
async def test_cross_country_dialog_absent_does_not_click() -> None:
    page, _ = _cross_country_page(dialog_present=False)
    adapter = HeadHunterBrowserAdapter(AsyncMock())
    adapter._click = AsyncMock()

    result = await adapter._handle_cross_country_dialog(page)

    assert result is None
    adapter._click.assert_not_awaited()
    assert page.wait_for_timeout.await_count == 6


@pytest.mark.asyncio
async def test_reveal_cover_letter_uses_current_headhunter_toggle() -> None:
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()

    toggle = MagicMock()
    toggle.count = AsyncMock(return_value=1)
    toggle.is_visible = AsyncMock(return_value=True)
    page.locator.return_value.first = toggle

    letter_field = MagicMock()
    action = MagicMock()
    action.is_successful = True
    adapter = HeadHunterBrowserAdapter(AsyncMock())
    adapter._click = AsyncMock(return_value=action)
    adapter._find_cover_letter_field = AsyncMock(return_value=letter_field)
    actions: list[MagicMock] = []

    result = await adapter._reveal_cover_letter_field(page, actions)

    assert result is letter_field
    page.locator.assert_called_once_with(
        "[data-qa='vacancy-response-letter-toggle']"
    )
    adapter._click.assert_awaited_once_with(
        page, toggle, "reveal-cover-letter", already_ok=False
    )
    assert actions == [action]
