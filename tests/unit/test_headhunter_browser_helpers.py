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
