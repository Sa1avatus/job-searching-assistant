"""Search and extraction for user-defined sites, driven only by a validated declarative recipe.

Everything here reads the page through Playwright locators and ``text_content``; no page
JavaScript is evaluated and navigation never leaves the site's approved hosts (ADR 0003).
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from app.browser.engine import PlaywrightEngine
from app.browser.recipe_learning import parse_html
from app.domain.search_recipe import (
    SearchRecipe,
    build_search_url,
    is_allowed_host,
    resolve_hit_url,
    validate_recipe,
)
from app.workflows.workflow_input_resolver import WorkflowExecutionInputResolver
from app.workflows.workflow_runner import execute_workflow_steps

_CARD_WAIT_MS = 15_000
_MAX_DESCRIPTION = 20_000
_MAX_CARDS_SCANNED = 100


class CustomSiteError(RuntimeError):
    """A custom-site page could not be read; the message is safe to show to the user."""


class _ReachStepInputResolver(WorkflowExecutionInputResolver):
    """Resolves the two placeholders a recorded search scenario may use. Nothing else."""

    def __init__(self, *, query: str, location: str) -> None:
        self._values = {"query": query, "location": location}

    def resolve_text(self, key: str) -> str:
        if key not in self._values:
            raise CustomSiteError("Сценарий поиска ссылается на недопустимое поле")
        return self._values[key]

    def resolve_boolean(self, key: str) -> bool:
        raise CustomSiteError("Сценарий поиска не может использовать это поле")

    def resolve_file(self, key: str) -> Path:
        raise CustomSiteError("Сценарий поиска не может использовать это поле")


@dataclass(frozen=True, slots=True)
class CustomSiteHit:
    source_url: str
    title: str
    company: str


def _css(selector: str) -> str:
    # The explicit engine prefix stops Playwright from interpreting text=/xpath=/>> selectors.
    return f"css={selector}"


async def _open(engine: PlaywrightEngine, url: str, allowed_hosts: tuple[str, ...]) -> Page:
    if urlsplit(url).scheme != "https" or not is_allowed_host(
        urlsplit(url).hostname, allowed_hosts
    ):
        raise CustomSiteError("Адрес не входит в разрешённые хосты сайта")
    page = await engine.new_page()
    navigation = await engine.navigate(page, url)
    if not navigation.is_successful:
        await page.close()
        raise CustomSiteError("Не удалось открыть страницу сайта")
    if not is_allowed_host(urlsplit(page.url).hostname, allowed_hosts):
        await page.close()
        raise CustomSiteError("Сайт перенаправил на неразрешённый хост")
    return page


async def _text(locator_owner: Page, selector: str) -> str:
    locator = locator_owner.locator(_css(selector)).first
    if await locator.count() == 0:
        return ""
    return re.sub(r"\s+", " ", (await locator.inner_text()) or "").strip()


async def read_cards(
    page: Page, recipe: SearchRecipe, allowed_hosts: tuple[str, ...], limit: int
) -> list[CustomSiteHit]:
    cards = page.locator(_css(recipe.card_selector))
    try:
        await cards.first.wait_for(timeout=_CARD_WAIT_MS)
    except PlaywrightTimeout:
        return []
    hits: list[CustomSiteHit] = []
    seen: set[str] = set()
    for index in range(min(await cards.count(), _MAX_CARDS_SCANNED)):
        card = cards.nth(index)
        link = card.locator(_css(recipe.link_selector)).first if recipe.link_selector else card
        if await link.count() == 0:
            continue
        href = await link.get_attribute("href") or ""
        url = resolve_hit_url(page.url, href, allowed_hosts)
        if url is None or url in seen:
            continue
        title = ""
        if recipe.title_selector:
            title_locator = card.locator(_css(recipe.title_selector)).first
            if await title_locator.count() > 0:
                title = (await title_locator.inner_text()).strip()
        if not title:
            title = (await link.inner_text()).strip()
        company = ""
        if recipe.company_selector:
            company_locator = card.locator(_css(recipe.company_selector)).first
            if await company_locator.count() > 0:
                company = (await company_locator.inner_text()).strip()
        seen.add(url)
        hits.append(
            CustomSiteHit(
                source_url=url,
                title=re.sub(r"\s+", " ", title)[:300],
                company=re.sub(r"\s+", " ", company)[:300],
            )
        )
        if len(hits) == limit:
            break
    return hits


async def search_custom_site(
    engine: PlaywrightEngine,
    *,
    recipe: SearchRecipe,
    allowed_hosts: tuple[str, ...],
    query: str,
    location: str = "",
    limit: int = 15,
) -> list[CustomSiteHit]:
    recipe = validate_recipe(recipe, allowed_hosts)
    if recipe.reach_steps:
        page = await engine.new_page()
        try:
            await execute_workflow_steps(
                page,
                list(recipe.reach_steps),
                _ReachStepInputResolver(query=query, location=location),
                allowed_hosts=allowed_hosts,
                is_submit_confirmed=False,
            )
            if not is_allowed_host(urlsplit(page.url).hostname, allowed_hosts):
                raise CustomSiteError("Сайт перенаправил на неразрешённый хост")
            return await read_cards(page, recipe, allowed_hosts, limit)
        finally:
            await page.close()
    page = await _open(
        engine, build_search_url(recipe, query=query, location=location), allowed_hosts
    )
    try:
        return await read_cards(page, recipe, allowed_hosts, limit)
    finally:
        await page.close()


async def fetch_page_html(
    engine: PlaywrightEngine, url: str, allowed_hosts: tuple[str, ...]
) -> tuple[str, str]:
    """(final URL, rendered HTML) of a page on an approved host."""
    page = await _open(engine, url, allowed_hosts)
    try:
        return page.url, await page.content()
    finally:
        await page.close()


# --- vacancy extraction ---------------------------------------------------------------------


def _plain_text(markup: str) -> str:
    return parse_html(f"<div>{html.unescape(markup)}</div>").text()


def _first(value: object) -> object:
    return value[0] if isinstance(value, list) and value else value


def _job_postings(documents: list[str]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []

    def visit(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "JobPosting" in kinds:
                found.append(node)
            visit(node.get("@graph"))

    for document in documents:
        try:
            visit(json.loads(document))
        except ValueError:
            continue
    return found


def _named(value: object) -> str:
    value = _first(value)
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    return str(value or "").strip()


def _location(posting: dict[str, object]) -> str:
    parts: list[str] = []
    place = _first(posting.get("jobLocation"))
    if isinstance(place, dict):
        address = place.get("address")
        if isinstance(address, dict):
            for key in ("addressLocality", "addressRegion", "addressCountry"):
                text = _named(address.get(key))
                if text and text not in parts:
                    parts.append(text)
        elif isinstance(address, str):
            parts.append(address)
    if str(posting.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        parts.append("Remote")
    return ", ".join(parts)


def _salary(posting: dict[str, object]) -> str:
    base = posting.get("baseSalary")
    if not isinstance(base, dict):
        return ""
    value = base.get("value")
    currency = str(base.get("currency") or "")
    if isinstance(value, dict):
        low, high = value.get("minValue"), value.get("maxValue")
        unit = str(value.get("unitText") or "")
        amount = (
            f"{low}-{high}"
            if low is not None and high is not None
            else str(low or high or value.get("value") or "")
        )
    else:
        amount, unit = str(value or ""), ""
    return " ".join(part for part in (amount, currency, unit.lower()) if part).strip()


def _published_at(posting: dict[str, object]) -> str | None:
    raw = str(posting.get("datePosted") or "").strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat() if raw else None
    except ValueError:
        return None


def vacancy_from_job_posting(url: str, documents: list[str]) -> dict[str, object] | None:
    """Vacancy fields from schema.org JobPosting JSON-LD, or ``None`` if the page has none."""
    postings = _job_postings(documents)
    if not postings:
        return None
    posting = postings[0]
    employment = _first(posting.get("employmentType"))
    return {
        "source_url": url,
        "title": str(posting.get("title") or "").strip(),
        "company": _named(posting.get("hiringOrganization")),
        "location": _location(posting),
        "description_text": _plain_text(str(posting.get("description") or ""))[:_MAX_DESCRIPTION],
        "required_skills": [],
        "form_fields": [],
        "requires_sensitive_review": False,
        "published_at": _published_at(posting),
        "salary_text": _salary(posting),
        "employment_text": str(employment or "").replace("_", " ").lower(),
    }


async def extract_custom_vacancy(
    engine: PlaywrightEngine, *, url: str, allowed_hosts: tuple[str, ...]
) -> dict[str, object]:
    page = await _open(engine, url, allowed_hosts)
    try:
        scripts = page.locator('script[type="application/ld+json"]')
        documents = [
            text
            for index in range(await scripts.count())
            if (text := await scripts.nth(index).text_content())
        ]
        vacancy = vacancy_from_job_posting(page.url, documents)
        if vacancy is not None and vacancy["title"]:
            return vacancy
        title = await _text(page, "h1") or (await page.title()).strip()
        company_meta = page.locator('meta[property="og:site_name"]').first
        company = (
            (await company_meta.get_attribute("content") or "").strip()
            if await company_meta.count() > 0
            else ""
        )
        body = ""
        for selector in ("main", "article", "body"):
            if await page.locator(_css(selector)).count() > 0:
                body = await _text(page, selector)
                break
        if not title:
            raise CustomSiteError("На странице не найдено название вакансии")
        return {
            "source_url": page.url,
            "title": title[:300],
            "company": company[:300],
            "location": "",
            "description_text": body[:_MAX_DESCRIPTION],
            "required_skills": [],
            "form_fields": [],
            "requires_sensitive_review": False,
            "published_at": None,
            "salary_text": "",
            "employment_text": "",
        }
    finally:
        await page.close()
