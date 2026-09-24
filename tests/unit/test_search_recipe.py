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


def test_selectors_are_learned_when_the_card_is_bare_but_a_grandparent_is_distinctive() -> None:
    """A card and its immediate parent can both be bare tags with no class of their own (e.g.
    a plain <li> inside a plain <ul>) that also match unrelated <li>s elsewhere on the page (a
    nav menu here); only a great-grandparent two levels further up is actually distinctive."""
    # The nav links share the same "/jobs/" prefix as the real cards too, so a content-based
    # (":has(a[href*=...])") filter alone cannot tell them apart - only being outside <nav>,
    # inside div.results, does. <nav> is excluded from link *grouping* already, so it never
    # pollutes which links count as "the" vacancy links, but it still inflates the raw <li>
    # count that makes the bare "li" selector ambiguous in the first place.
    nav_items = "".join(f'<li><a href="/jobs/nav-{i}">Nav {i}</a></li>' for i in range(10))
    html = f"""
    <html><body>
      <nav><ul>{nav_items}</ul></nav>
      <div class="results">
        <ul>
          <li><a href="/jobs/1">Python Developer</a></li>
          <li><a href="/jobs/2">Backend Engineer</a></li>
          <li><a href="/jobs/3">Data Engineer</a></li>
        </ul>
      </div>
    </body></html>
    """
    learned = learn_selectors(html, page_url=PAGE, allowed_hosts=HOSTS)

    assert learned.card_selector == "div.results li"
    assert learned.card_count == 3


def test_selectors_are_learned_when_an_unrelated_widget_shares_the_same_card_markup() -> None:
    """A different widget on the page (a "related searches" list here) can reuse the exact same
    bare <li> inside the exact same classed ancestor as the real cards; only the vacancy link
    itself tells them apart."""
    # Content-less filler <li>s (a breadcrumb, a feature list - nothing to do with vacancies)
    # push the plain "li" match count past the ambiguity threshold on their own.
    filler_items = "".join(f"<li>Step {i}</li>" for i in range(10))
    html = f"""
    <html><body>
      <ul class="steps">{filler_items}</ul>
      <div class="box-jobs">
        <ul>
          <li><a href="/related/react">React</a></li>
          <li><a href="/related/vue">Vue</a></li>
        </ul>
      </div>
      <div class="box-jobs">
        <ul>
          <li><a href="/jobs/1">Python Developer</a></li>
          <li><a href="/jobs/2">Backend Engineer</a></li>
          <li><a href="/jobs/3">Data Engineer</a></li>
        </ul>
      </div>
    </body></html>
    """
    learned = learn_selectors(html, page_url=PAGE, allowed_hosts=HOSTS)

    assert learned.card_selector == 'li:has(a[href*="/jobs/"])'
    assert learned.card_count == 3


def test_learning_fails_with_a_reason_when_there_are_no_results() -> None:
    with pytest.raises(RecipeLearningError):
        learn_selectors(
            "<html><body><a href='/jobs/1'>One</a><a href='/about'>About</a></body></html>",
            page_url=PAGE,
            allowed_hosts=HOSTS,
        )
