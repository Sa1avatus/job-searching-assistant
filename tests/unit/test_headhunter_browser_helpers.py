from adapters.job_boards.headhunter_browser import _vacancy_id_from_href, resolve_known_area_ids


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
