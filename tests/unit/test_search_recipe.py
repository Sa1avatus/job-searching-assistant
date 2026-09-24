from pathlib import Path

import pytest

from app.browser.recipe_learning import (
    RecipeLearningError,
    infer_url_template,
    learn_selectors,
)
from app.domain.search_recipe import (
    InvalidSearchRecipe,
    SearchRecipe,
    build_search_url,
    resolve_hit_url,
    validate_recipe,
)

HOSTS = ("jobs.example.com",)
PAGE = "https://jobs.example.com/search?q=python&page=1"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "custom_site" / "results.html"


def _recipe(**overrides: str) -> SearchRecipe:
    values = {
        "url_template": "https://jobs.example.com/search?q={query}",
        "card_selector": "li.job-card",
        "link_selector": "a.job-link",
        "title_selector": "h2",
        "company_selector": "span.company-name",
    }
    values.update(overrides)
    return SearchRecipe(**values)


def test_valid_recipe_round_trips_and_builds_urls() -> None:
    recipe = validate_recipe(_recipe(), HOSTS)
    assert SearchRecipe.from_dict(recipe.to_dict()) == recipe
    assert (
        build_search_url(recipe, query="senior c++ dev")
        == "https://jobs.example.com/search?q=senior+c%2B%2B+dev"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"url_template": "http://jobs.example.com/search?q={query}"},
        {"url_template": "https://jobs.example.com/search"},
        {"url_template": "https://evil.example.org/search?q={query}"},
        {"url_template": "https://user:pw@jobs.example.com/search?q={query}"},
        {"url_template": "https://jobs.example.com/{other}?q={query}"},
        {"card_selector": ""},
        {"card_selector": "xpath=//li"},
        {"card_selector": "li >> text=Apply"},
        {"link_selector": "a<script>"},
        {"title_selector": "x" * 400},
    ],
)
def test_invalid_recipes_are_rejected(overrides: dict[str, str]) -> None:
    with pytest.raises(InvalidSearchRecipe):
        validate_recipe(_recipe(**overrides), HOSTS)


def test_hit_urls_must_stay_on_allowed_hosts() -> None:
    base = "https://jobs.example.com/search"
    assert resolve_hit_url(base, "/jobs/1#top", HOSTS) == "https://jobs.example.com/jobs/1"
    assert resolve_hit_url(base, "https://evil.example.org/jobs/1", HOSTS) is None
    assert resolve_hit_url(base, "http://jobs.example.com/jobs/1", HOSTS) is None
    assert resolve_hit_url(base, "javascript:alert(1)", HOSTS) is None


def test_url_template_is_inferred_from_the_results_url() -> None:
    assert (
        infer_url_template(
            "https://jobs.example.com/search?q=Python+dev&l=Berlin",
            query="python dev",
            location="berlin",
        )
        == "https://jobs.example.com/search?q={query}&l={location}"
    )
    # the query must not be replaced inside the host name
    assert infer_url_template("https://python.example.com/search?q=go", query="python") is None


def test_url_template_is_inferred_from_an_seo_slug() -> None:
    """Some sites put the query into the URL as a hyphenated slug rather than percent/plus
    encoding (e.g. CareerViet: ".../ML-Engineer-k-en.html" for the query "ML Engineer")."""
    assert (
        infer_url_template(
            "https://jobs.example.com/jobs/ML-Engineer-k-en.html", query="ML Engineer"
        )
        == "https://jobs.example.com/jobs/{query-slug}-k-en.html"
    )


def test_slug_url_template_round_trips_and_builds_urls() -> None:
    recipe = validate_recipe(
        _recipe(url_template="https://jobs.example.com/jobs/{query-slug}-k-en.html"), HOSTS
    )
    assert SearchRecipe.from_dict(recipe.to_dict()) == recipe
    assert (
        build_search_url(recipe, query="ML Engineer")
        == "https://jobs.example.com/jobs/ML-Engineer-k-en.html"
    )


def test_selectors_are_learned_from_a_results_page() -> None:
    learned = learn_selectors(
        FIXTURE.read_text(encoding="utf-8"), page_url=PAGE, allowed_hosts=HOSTS
    )

    assert learned.card_selector == "li.job-card"
    assert learned.link_selector == "a.job-link"
    assert learned.title_selector == "h2"
    assert learned.company_selector == "span.company-name"
    assert learned.card_count == 4
    validate_recipe(
        SearchRecipe(
            url_template="https://jobs.example.com/search?q={query}",
            card_selector=learned.card_selector,
            link_selector=learned.link_selector,
            title_selector=learned.title_selector,
            company_selector=learned.company_selector,
        ),
        HOSTS,
    )


def test_learning_fails_with_a_reason_when_there_are_no_results() -> None:
    with pytest.raises(RecipeLearningError):
        learn_selectors(
            "<html><body><a href='/jobs/1'>One</a><a href='/about'>About</a></body></html>",
            page_url=PAGE,
            allowed_hosts=HOSTS,
        )
