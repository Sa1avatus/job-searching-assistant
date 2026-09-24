"""Custom-site search and extraction against controlled pages (no network, nothing submitted)."""

import asyncio
import json
from pathlib import Path

import pytest
from playwright.async_api import Page, Route

from app.browser.custom_site import CustomSiteError, extract_custom_vacancy, search_custom_site
from app.browser.engine import PlaywrightEngine
from app.browser.recipe_learning import learn_selectors
from app.domain.search_recipe import SearchRecipe, validate_recipe
from app.domain.workflow_click import ClickWorkflowStep
from app.domain.workflow_fill import FillStepParameters, FillWorkflowStep
from app.domain.workflow_schemas import NavigateStepParameters, NavigateWorkflowStep
from app.domain.workflow_selectors import WorkflowSelectorCandidate

HOSTS = ("jobs.example.com",)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "custom_site"
RESULTS = (FIXTURES / "results.html").read_text(encoding="utf-8")
SEARCH_FORM = (FIXTURES / "search_form.html").read_text(encoding="utf-8")
POSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Senior Python Developer",
    "hiringOrganization": {"@type": "Organization", "name": "Acme Corp"},
    "jobLocation": {"address": {"addressLocality": "Berlin", "addressCountry": "DE"}},
    "description": "<p>Build <b>APIs</b> &amp; services</p>",
    "datePosted": "2026-09-01",
    "employmentType": "FULL_TIME",
    "baseSalary": {
        "currency": "EUR",
        "value": {"minValue": 60000, "maxValue": 80000, "unitText": "YEAR"},
    },
}
VACANCY = (
    "<html><head><script type='application/ld+json'>"
    + json.dumps(POSTING)
    + "</script></head><body><h1>ignored</h1></body></html>"
)
PLAIN_VACANCY = (
    "<html><head><title>t</title></head><body><main><h1>Plain role</h1>Text</main></body></html>"
)


async def _serve(route: Route) -> None:
    url = route.request.url
    if url.startswith("https://evil.example.org"):
        await route.fulfill(status=200, content_type="text/html", body="<html>evil</html>")
    elif url.rstrip("/") == "https://jobs.example.com":
        await route.fulfill(status=200, content_type="text/html", body=SEARCH_FORM)
    elif "/search" in url:
        await route.fulfill(status=200, content_type="text/html", body=RESULTS)
    elif url.endswith("/jobs/101"):
        await route.fulfill(status=200, content_type="text/html", body=VACANCY)
    elif url.endswith("/jobs/202"):
        await route.fulfill(status=200, content_type="text/html", body=PLAIN_VACANCY)
    elif url.endswith("/redirect"):
        await route.fulfill(status=302, headers={"location": "https://evil.example.org/x"})
    else:
        await route.fulfill(status=404, body="not found")


def _offline(engine: PlaywrightEngine) -> PlaywrightEngine:
    original = engine.new_page

    async def routed() -> Page:
        page = await original()
        await page.route("**/*", _serve)
        return page

    engine.new_page = routed  # type: ignore[method-assign]
    return engine


RECIPE = SearchRecipe(
    url_template="https://jobs.example.com/search?q={query}",
    card_selector="li.job-card",
    link_selector="a.job-link",
    title_selector="h2",
    company_selector="span.company-name",
)


def test_search_reads_cards_and_drops_off_host_links(tmp_path: Path) -> None:
    async def run() -> None:
        async with _offline(PlaywrightEngine(artifact_directory=tmp_path)) as engine:
            hits = await search_custom_site(
                engine, recipe=RECIPE, allowed_hosts=HOSTS, query="python", limit=10
            )

        assert [(hit.source_url, hit.title, hit.company) for hit in hits] == [
            ("https://jobs.example.com/jobs/101", "Senior Python Developer", "Acme Corp"),
            ("https://jobs.example.com/jobs/102", "Backend Engineer", "Globex"),
            ("https://jobs.example.com/jobs/103", "Data Engineer", "Initech"),
            ("https://jobs.example.com/jobs/104", "SRE", "Umbrella"),
        ]

    asyncio.run(run())


def test_recorded_reach_steps_replay_navigate_fill_click_to_reach_results(tmp_path: Path) -> None:
    """A recorded search scenario (no URL template) fills the form and clicks Search."""
    recipe = validate_recipe(
        SearchRecipe(
            url_template="",
            card_selector="li.job-card",
            link_selector="a.job-link",
            title_selector="h2",
            company_selector="span.company-name",
            reach_steps=(
                NavigateWorkflowStep(
                    parameters=NavigateStepParameters(url="https://jobs.example.com/")
                ),
                FillWorkflowStep(
                    selector_candidates=[WorkflowSelectorCandidate(kind="id", value="query-input")],
                    parameters=FillStepParameters(value_key="query"),
                ),
                FillWorkflowStep(
                    selector_candidates=[
                        WorkflowSelectorCandidate(kind="id", value="location-input")
                    ],
                    parameters=FillStepParameters(value_key="location"),
                ),
                ClickWorkflowStep(
                    selector_candidates=[
                        WorkflowSelectorCandidate(kind="id", value="search-button")
                    ]
                ),
            ),
        ),
        HOSTS,
    )

    async def run() -> None:
        async with _offline(PlaywrightEngine(artifact_directory=tmp_path)) as engine:
            hits = await search_custom_site(
                engine,
                recipe=recipe,
                allowed_hosts=HOSTS,
                query="python",
                location="berlin",
                limit=10,
            )

        assert [hit.source_url for hit in hits] == [
            "https://jobs.example.com/jobs/101",
            "https://jobs.example.com/jobs/102",
            "https://jobs.example.com/jobs/103",
            "https://jobs.example.com/jobs/104",
        ]

    asyncio.run(run())


def test_reach_steps_refuse_a_click_that_navigates_off_host(tmp_path: Path) -> None:
    recipe = validate_recipe(
        SearchRecipe(
            url_template="",
            card_selector="li.job-card",
            reach_steps=(
                NavigateWorkflowStep(
                    parameters=NavigateStepParameters(url="https://jobs.example.com/off-host-link")
                ),
                FillWorkflowStep(
                    selector_candidates=[WorkflowSelectorCandidate(kind="id", value="q")],
                    parameters=FillStepParameters(value_key="query"),
                ),
                ClickWorkflowStep(
                    selector_candidates=[WorkflowSelectorCandidate(kind="id", value="ignored")],
                ),
            ),
        ),
        HOSTS,
    )

    async def _serve_off_host_link(route: Route) -> None:
        url = route.request.url
        if url.endswith("/off-host-link"):
            await route.fulfill(
                status=200,
                content_type="text/html",
                body='<html><body><input id="q">'
                '<a href="https://evil.example.org/x" id="ignored">x</a>'
                "</body></html>",
            )
        else:
            await _serve(route)

    async def run() -> None:
        engine = PlaywrightEngine(artifact_directory=tmp_path)
        original = engine.new_page

        async def routed() -> Page:
            page = await original()
            await page.route("**/*", _serve_off_host_link)
            return page

        engine.new_page = routed  # type: ignore[method-assign]
        async with engine:
            with pytest.raises(CustomSiteError):
                await search_custom_site(
                    engine, recipe=recipe, allowed_hosts=HOSTS, query="python", limit=10
                )

    asyncio.run(run())


def test_learned_selectors_reproduce_the_same_hits_in_a_real_browser(tmp_path: Path) -> None:
    learned = learn_selectors(
        RESULTS, page_url="https://jobs.example.com/search?q=python", allowed_hosts=HOSTS
    )
    recipe = SearchRecipe(
        url_template="https://jobs.example.com/search?q={query}",
        card_selector=learned.card_selector,
        link_selector=learned.link_selector,
        title_selector=learned.title_selector,
        company_selector=learned.company_selector,
    )

    async def run() -> None:
        async with _offline(PlaywrightEngine(artifact_directory=tmp_path)) as engine:
            hits = await search_custom_site(
                engine, recipe=recipe, allowed_hosts=HOSTS, query="python", limit=10
            )
        assert len(hits) == learned.card_count == 4

    asyncio.run(run())


def test_extraction_prefers_json_ld_and_falls_back_to_the_page(tmp_path: Path) -> None:
    async def run() -> None:
        async with _offline(PlaywrightEngine(artifact_directory=tmp_path)) as engine:
            structured = await extract_custom_vacancy(
                engine, url="https://jobs.example.com/jobs/101", allowed_hosts=HOSTS
            )
            plain = await extract_custom_vacancy(
                engine, url="https://jobs.example.com/jobs/202", allowed_hosts=HOSTS
            )

        assert structured["title"] == "Senior Python Developer"
        assert structured["company"] == "Acme Corp"
        assert structured["location"] == "Berlin, DE"
        assert structured["description_text"] == "Build APIs & services"
        assert structured["salary_text"] == "60000-80000 EUR year"
        assert structured["employment_text"] == "full time"
        assert str(structured["published_at"]).startswith("2026-09-01")
        assert plain["title"] == "Plain role"
        assert "Text" in str(plain["description_text"])

    asyncio.run(run())


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.org/jobs/1",
        "http://jobs.example.com/jobs/1",
        "https://jobs.example.com/redirect",
    ],
)
def test_pages_outside_the_approved_hosts_are_refused(tmp_path: Path, url: str) -> None:
    async def run() -> None:
        async with _offline(PlaywrightEngine(artifact_directory=tmp_path)) as engine:
            with pytest.raises(CustomSiteError):
                await extract_custom_vacancy(engine, url=url, allowed_hosts=HOSTS)

    asyncio.run(run())
