from app.api.schemas import (
    DiscoverGreenhouseVacanciesRequest,
    DiscoverHeadHunterVacanciesRequest,
    DiscoverLinkedInVacanciesRequest,
    DiscoverVacanciesStreamRequest,
)


def test_all_discovery_requests_accept_resume_sized_search_keywords() -> None:
    search_text = "keyword " * 240

    requests = (
        DiscoverHeadHunterVacanciesRequest(search_text=search_text),
        DiscoverLinkedInVacanciesRequest(search_text=search_text),
        DiscoverGreenhouseVacanciesRequest(search_text=search_text),
        DiscoverVacanciesStreamRequest(sources=["greenhouse"], search_text=search_text),
    )

    assert len(search_text) > 300
    assert all(request.search_text == search_text for request in requests)
