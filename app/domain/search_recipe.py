"""Declarative search recipe for a user-defined job site.

A recipe is data, never code: an HTTPS URL template plus plain CSS selectors. It is validated
against the site's host allowlist so a recipe can only ever read pages from hosts the user
approved (ADR 0003: fail closed on cross-host navigation, no arbitrary JavaScript).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import quote_plus, urljoin, urlsplit

from pydantic import ValidationError

from app.domain.workflow_click import ClickWorkflowStep
from app.domain.workflow_fill import FillWorkflowStep
from app.domain.workflow_schemas import NavigateWorkflowStep
from app.domain.workflow_step_parser import parse_workflow_step

QUERY_PLACEHOLDER = "{query}"
LOCATION_PLACEHOLDER = "{location}"

_MAX_SELECTOR_LENGTH = 300
_MAX_URL_LENGTH = 2000
# Plain CSS only: no engine prefixes (`xpath=`, `text=`, `js=`), no chaining (`>>`), no markup.
_SELECTOR_PATTERN = re.compile(r"^[A-Za-z0-9_\-\s.#\[\]=\"'*:>+~,()^$|/%]+$")
_FORBIDDEN_SELECTOR_FRAGMENTS = (">>", "xpath", "js=", "javascript", "text=", "internal:")

# A recorded "reach the results page" scenario is a short, closed sequence: open a page, type
# into fields, click things. No submit, upload, file access or arbitrary step type - it only
# gets the browser to a results page, which is then read the same way as with a URL template.
ReachStep = NavigateWorkflowStep | FillWorkflowStep | ClickWorkflowStep
_ALLOWED_REACH_STEP_TYPES = (NavigateWorkflowStep, FillWorkflowStep, ClickWorkflowStep)
_ALLOWED_REACH_VALUE_KEYS = frozenset({"query", "location"})
_MAX_REACH_STEPS = 30


class InvalidSearchRecipe(ValueError):
    """Raised when a recipe is malformed or would leave the approved hosts."""


def normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def is_allowed_host(host: str | None, allowed_hosts: tuple[str, ...] | list[str]) -> bool:
    """Exact host match; subdomains must be listed explicitly."""
    if not host:
        return False
    normalized = normalize_host(host)
    return any(normalized == normalize_host(allowed) for allowed in allowed_hosts)


def validate_selector(selector: str, *, field: str, required: bool) -> str:
    selector = selector.strip()
    if not selector:
        if required:
            raise InvalidSearchRecipe(f"{field}: селектор обязателен")
        return ""
    if len(selector) > _MAX_SELECTOR_LENGTH:
        raise InvalidSearchRecipe(f"{field}: селектор длиннее {_MAX_SELECTOR_LENGTH} символов")
    lowered = selector.lower()
    if any(fragment in lowered for fragment in _FORBIDDEN_SELECTOR_FRAGMENTS):
        raise InvalidSearchRecipe(f"{field}: допустим только обычный CSS-селектор")
    if not _SELECTOR_PATTERN.fullmatch(selector):
        raise InvalidSearchRecipe(f"{field}: недопустимые символы в CSS-селекторе")
    return selector


@dataclass(frozen=True, slots=True)
class SearchRecipe:
    """How to turn a query into result cards on one site.

    ``link_selector`` and ``title_selector`` are relative to a card; an empty link selector means
    the card itself is the link, an empty title selector means the link text is the title.

    ``reach_steps`` is an alternative to ``url_template`` for sites where search cannot be
    expressed as a URL (a POST form, an in-page click): a short recorded sequence of
    navigate/fill/click steps that gets the browser to the results page, which is then read with
    the same card/link/title/company selectors either way. When both are set, ``reach_steps``
    takes priority at search time.
    """

    url_template: str
    card_selector: str
    link_selector: str = ""
    title_selector: str = ""
    company_selector: str = ""
    reach_steps: tuple[ReachStep, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "url_template": self.url_template,
            "card_selector": self.card_selector,
            "link_selector": self.link_selector,
            "title_selector": self.title_selector,
            "company_selector": self.company_selector,
            "reach_steps": [step.model_dump(mode="json") for step in self.reach_steps],
        }

    @classmethod
    def from_dict(cls, data: object) -> SearchRecipe:
        if not isinstance(data, dict):
            raise InvalidSearchRecipe("Рецепт должен быть объектом")
        return cls(
            url_template=str(data.get("url_template", "")),
            card_selector=str(data.get("card_selector", "")),
            link_selector=str(data.get("link_selector", "")),
            title_selector=str(data.get("title_selector", "")),
            company_selector=str(data.get("company_selector", "")),
            reach_steps=_parse_reach_steps(data.get("reach_steps") or []),
        )


def _parse_reach_steps(raw: object) -> tuple[ReachStep, ...]:
    if not isinstance(raw, list):
        raise InvalidSearchRecipe("Сценарий поиска должен быть списком шагов")
    parsed: list[ReachStep] = []
    for index, item in enumerate(raw):
        if isinstance(item, _ALLOWED_REACH_STEP_TYPES):
            parsed.append(item)
            continue
        try:
            step = parse_workflow_step(item)
        except ValidationError as error:
            raise InvalidSearchRecipe(f"Шаг сценария {index + 1}: некорректные данные") from error
        if not isinstance(step, _ALLOWED_REACH_STEP_TYPES):
            raise InvalidSearchRecipe(
                f"Шаг сценария {index + 1}: сценарий поиска допускает только navigate, fill и click"
            )
        parsed.append(step)
    return tuple(parsed)


def _validate_url_template(template: str, allowed_hosts: tuple[str, ...] | list[str]) -> str:
    if len(template) > _MAX_URL_LENGTH:
        raise InvalidSearchRecipe("Шаблон URL слишком длинный")
    if QUERY_PLACEHOLDER not in template:
        raise InvalidSearchRecipe(f"Шаблон URL должен содержать {QUERY_PLACEHOLDER}")
    probe = urlsplit(template.replace(QUERY_PLACEHOLDER, "x").replace(LOCATION_PLACEHOLDER, "x"))
    if probe.scheme != "https":
        raise InvalidSearchRecipe("Шаблон URL должен начинаться с https://")
    if probe.username or probe.password:
        raise InvalidSearchRecipe("В URL не должно быть логина и пароля")
    if not is_allowed_host(probe.hostname, allowed_hosts):
        raise InvalidSearchRecipe(f"Хост {probe.hostname} не входит в разрешённые хосты сайта")
    leftover = re.sub(r"\{(query|location)\}", "", template)
    if "{" in leftover or "}" in leftover:
        raise InvalidSearchRecipe("Допустимы только подстановки {query} и {location}")
    return template


def validate_reach_steps(
    steps: Sequence[ReachStep], allowed_hosts: tuple[str, ...] | list[str]
) -> tuple[ReachStep, ...]:
    """Return the normalized reach-steps or raise :class:`InvalidSearchRecipe`."""
    if not steps:
        return ()
    if len(steps) > _MAX_REACH_STEPS:
        raise InvalidSearchRecipe(f"Сценарий поиска длиннее {_MAX_REACH_STEPS} шагов")
    if not isinstance(steps[0], NavigateWorkflowStep):
        raise InvalidSearchRecipe("Сценарий поиска должен начинаться с открытия страницы")
    has_query_fill = False
    for index, step in enumerate(steps):
        if isinstance(step, NavigateWorkflowStep):
            hostname = urlsplit(step.parameters.url).hostname
            if not is_allowed_host(hostname, allowed_hosts):
                raise InvalidSearchRecipe(
                    f"Шаг {index + 1}: хост {hostname} не входит в разрешённые хосты сайта"
                )
        elif isinstance(step, FillWorkflowStep):
            if step.parameters.value_key not in _ALLOWED_REACH_VALUE_KEYS:
                raise InvalidSearchRecipe(
                    f"Шаг {index + 1}: поле сценария поиска может быть только query или location"
                )
            has_query_fill = has_query_fill or step.parameters.value_key == "query"
    if not has_query_fill:
        raise InvalidSearchRecipe("Сценарий поиска должен хотя бы раз вводить запрос (query)")
    return tuple(steps)


def validate_recipe(
    recipe: SearchRecipe, allowed_hosts: tuple[str, ...] | list[str]
) -> SearchRecipe:
    """Return the normalized recipe or raise :class:`InvalidSearchRecipe`."""
    reach_steps = validate_reach_steps(recipe.reach_steps, allowed_hosts)
    template = recipe.url_template.strip()
    if not template and not reach_steps:
        raise InvalidSearchRecipe(
            f"Нужен шаблон URL с {QUERY_PLACEHOLDER} или записанный сценарий поиска"
        )
    if template:
        template = _validate_url_template(template, allowed_hosts)
    return SearchRecipe(
        url_template=template,
        card_selector=validate_selector(recipe.card_selector, field="Карточка", required=True),
        link_selector=validate_selector(recipe.link_selector, field="Ссылка", required=False),
        title_selector=validate_selector(recipe.title_selector, field="Название", required=False),
        company_selector=validate_selector(
            recipe.company_selector, field="Компания", required=False
        ),
        reach_steps=reach_steps,
    )


def build_search_url(recipe: SearchRecipe, *, query: str, location: str = "") -> str:
    return recipe.url_template.replace(QUERY_PLACEHOLDER, quote_plus(query.strip())).replace(
        LOCATION_PLACEHOLDER, quote_plus(location.strip())
    )


def resolve_hit_url(
    base_url: str, href: str, allowed_hosts: tuple[str, ...] | list[str]
) -> str | None:
    """Absolute https URL of a result link, or ``None`` if it leaves the approved hosts."""
    href = href.strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    absolute = urlsplit(urljoin(base_url, href))
    if absolute.scheme != "https" or absolute.username or absolute.password:
        return None
    if not is_allowed_host(absolute.hostname, allowed_hosts):
        return None
    return absolute._replace(fragment="").geturl()
