"""Browser-automated hh.ru application submission.

hh.ru's terms prohibit automated page parsing/collection, but they do not offer a job-seeker
application API either, so the only way to submit a real response programmatically is to drive a
signed-in browser. This adapter requires a session captured through
``scripts/browser_login_capture.py`` (the user logs in by hand, including any CAPTCHA/SMS code);
the automation never sees or types a password.

Selectors below are based on hh.ru's publicly documented ``data-qa`` attributes, which the site
uses for its own UI tests and changes rarely, but they have not been exercised against the live
site from this environment (no network egress here). Treat them as a verified starting point that
you should confirm locally with ``APP_BROWSER_HEADLESS=false`` before relying on it unattended, and
update ``app/browser/selector_library.py`` fallbacks if hh.ru changes its markup.
"""

from __future__ import annotations

import re
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urlparse

import structlog
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from adapters.job_boards.browser_apply_common import ApplyBlocked, CaptchaChallenge, LoginRequired
from app.browser.engine import BrowserActionResult, PlaywrightEngine
from app.browser.evidence import capture_browser_failure
from app.browser.publication_dates import parse_publication_datetime
from app.domain.failures import FailureCategory
from app.domain.forms import FormField, FormFieldType

logger = structlog.get_logger(__name__)

_HOST_SUFFIX = "hh.ru"

# hh.ru renders a captcha checkpoint page/iframe at these well-known markers when it flags
# automated-looking activity (also used by the manual browser-handoff flow's own docs).
_CAPTCHA_MARKERS = ("checkcaptcha", "captcha-page", "hh.ru/account/blocked")

_COVER_LETTER_EDITABLE_SELECTORS: tuple[str, ...] = (
    "textarea[data-qa='vacancy-response-popup-form-letter-input']",
    "[data-qa='vacancy-response-popup-form-letter-input'] textarea",
    "textarea[data-qa='vacancy-response-popup-letter-input']",
    "textarea[data-qa='vacancy-response-letter-textarea']",
    "textarea[data-qa='vacancy-response-letter-informer-textarea']",
    "[data-qa='vacancy-response-letter-informer'] textarea",
    "[role='dialog'] textarea",
    "textarea[name='letter']",
)
_COVER_LETTER_INFORMER_SELECTOR = "[data-qa='vacancy-response-letter-informer']"
_COVER_LETTER_REVEAL_SELECTORS: tuple[str, ...] = (
    "[data-qa='add-cover-letter']",
    "[data-qa='vacancy-response-popup-letter-button-add']",
    "[data-qa='vacancy-response-popup-letter-add']",
    "[data-qa='vacancy-response-letter-informer-addletter']",
    "[data-qa='vacancy-response-letter-informer-attachletter']",
    "[data-qa='vacancy-response-letter-informer-writebutton']",
    "button:has-text('Добавить сопроводительное письмо')",
    "button:has-text('Приложить сопроводительное письмо')",
    "button:has-text('Написать сопроводительное')",
)
_COVER_LETTER_SAVE_SELECTOR = (
    "[data-qa='vacancy-response-popup-letter-button-save'],"
    "[role='dialog'] button:has-text('Сохранить')"
)

# hh.ru's `area` filter uses the same small set of numeric region ids as its (now avoided) public
# API, but there is no network-free way to look up an arbitrary name, so only the common cases are
# built in here. Unknown location names are ignored (search falls back to "anywhere") rather than
# making an HTTP call to resolve them, since this adapter intentionally has zero non-browser
# network dependency. Extend this table if you search other regions often.
KNOWN_AREA_IDS: dict[str, str] = {
    "москва": "1",
    "moscow": "1",
    "санкт-петербург": "2",
    "спб": "2",
    "saint petersburg": "2",
    "saint-petersburg": "2",
    "новосибирск": "4",
    "екатеринбург": "3",
    "казань": "88",
    "россия": "113",
    "russia": "113",
}
_ANYWHERE_MARKERS = {"anywhere", "везде", "any", "любое", "remote", "удалённо", "удаленно"}


def resolve_known_area_ids(location_names: list[str]) -> list[str]:
    normalized = {name.strip().casefold() for name in location_names if name.strip()}
    if not normalized or normalized & _ANYWHERE_MARKERS:
        return []
    return sorted({KNOWN_AREA_IDS[name] for name in normalized if name in KNOWN_AREA_IDS})


def _vacancy_id_from_href(href: str) -> str | None:
    match = re.search(r"/vacancy/(\d+)", href)
    return match.group(1) if match else None


@dataclass(frozen=True, slots=True)
class HeadHunterSearchHit:
    vacancy_id: str
    source_url: str
    title: str
    company: str


@dataclass(frozen=True, slots=True)
class ExtractedHeadHunterVacancy:
    source_url: str
    title: str
    company: str
    location: str
    description_text: str
    required_skills: tuple[str, ...]
    form_fields: tuple[FormField, ...]
    requires_sensitive_review: bool
    published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class HeadHunterApplyResult:
    actions: tuple[BrowserActionResult, ...]
    already_applied: bool
    confirmation_url: str

    @property
    def is_successful(self) -> bool:
        return bool(self.actions) and all(action.is_successful for action in self.actions)


class HeadHunterBrowserAdapter:
    name = "headhunter-browser"

    def __init__(self, browser_engine: PlaywrightEngine) -> None:
        self._browser_engine = browser_engine

    def supports_url(self, url: str) -> bool:
        hostname = (urlparse(url).hostname or "").casefold()
        return hostname == _HOST_SUFFIX or hostname.endswith(f".{_HOST_SUFFIX}")

    async def apply(
        self,
        url: str,
        *,
        cover_letter: str | None,
        resume_title: str | None = None,
    ) -> HeadHunterApplyResult:
        """Open the vacancy with the restored session and submit a real response.

        Raises ``LoginRequired`` if the session is not authenticated, ``CaptchaChallenge`` if hh.ru
        presents a verification checkpoint, and ``ApplyBlocked`` if the vacancy cannot be safely
        auto-applied to (already responded, requires a test/assignment, external apply link).
        """
        if not self.supports_url(url):
            raise ValueError("URL is not a supported hh.ru host")
        page = await self._browser_engine.new_page()
        navigation = await self._browser_engine.navigate(page, url)
        if not navigation.is_successful:
            raise RuntimeError(f"hh.ru navigation failed: {navigation.error_category}")
        await self._raise_if_captcha(page)
        if not self.supports_url(page.url):
            raise RuntimeError("hh.ru navigation left the trusted host")
        await self._raise_if_logged_out(page)

        actions: list[BrowserActionResult] = []
        response_button = page.locator("[data-qa='vacancy-response-link-top']").first
        already_applied_marker = page.locator(
            "[data-qa='vacancy-response-link-top-disabled'],"
            "[data-qa='vacancy-response-alreadyresponded-applied-short']"
        )
        already_applied_text = page.get_by_text(
            re.compile(r"^\s*(?:You applied|Вы откликнулись)(?:\s|$)", re.IGNORECASE)
        )
        if (
            await already_applied_marker.count() > 0
            or await already_applied_text.count() > 0
        ):
            checkpoint = await self._browser_engine.capture_review_checkpoint(
                page, target="already-applied"
            )
            return HeadHunterApplyResult((checkpoint,), True, page.url)

        actions.append(
            await self._click(page, response_button, "open-response-form", already_ok=False)
        )
        if not actions[-1].is_successful:
            raise ApplyBlocked("The response control was not found on this vacancy page")

        await self._wait_for_response_form(page, response_button)
        await self._raise_if_captcha(page)
        if cover_letter:
            letter_field = await self._find_cover_letter_field(page)
            if letter_field is None:
                letter_field = await self._reveal_cover_letter_field(page, actions)
            if letter_field is None:
                await capture_browser_failure(
                    page,
                    artifact_directory=self._browser_engine.artifact_directory,
                    action_name="locate",
                    target="cover-letter-field-not-found",
                    error=ApplyBlocked("No editable cover-letter field was found"),
                )
                raise ApplyBlocked(
                    "A cover letter was requested but no editable cover-letter field was found"
                )
            with_letter = await self._safe_fill(letter_field, cover_letter)
            actions.append(with_letter)
            if not with_letter.is_successful:
                raise ApplyBlocked("The cover letter field could not be filled")
            try:
                actual_value = await letter_field.input_value(timeout=10_000)
            except Exception as error:  # noqa: BLE001 - converted to a safe apply failure
                raise ApplyBlocked("The filled cover letter could not be verified") from error
            if actual_value != cover_letter:
                raise ApplyBlocked(
                    "Cover letter field value does not match the requested text after fill"
                )
            save_letter_button = page.locator(_COVER_LETTER_SAVE_SELECTOR).first
            if (
                await save_letter_button.count() > 0
                and await save_letter_button.is_visible()
            ):
                save_letter = await self._click(
                    page, save_letter_button, "save-cover-letter", already_ok=False
                )
                actions.append(save_letter)
                if not save_letter.is_successful:
                    raise ApplyBlocked("The cover letter could not be saved in the response form")

        submit_button = page.locator(
            "[data-qa='vacancy-response-popup-submit'],"
            "[data-qa='vacancy-response-submit-popup'],"
            "[data-qa='vacancy-response-submit-form']"
        ).first
        actions.append(await self._click(page, submit_button, "submit-response", already_ok=False))
        if not actions[-1].is_successful:
            raise ApplyBlocked(
                "The submit control was not found; this vacancy likely requires a test"
            )

        await self._raise_if_captcha(page)
        confirmation = page.locator(
            "[data-qa='vacancy-response-popup-form-done'],"
            "[data-qa='vacancy-response-sent'],"
            "[data-qa='vacancy-responded-success-title'],"
            "[data-qa='vacancy-response-letter-informer-headresponsesuccess'],"
            "[data-qa='vacancy-response-alreadyresponded-applied-short']"
        )
        confirmed = False
        for _ in range(20):
            if await self._has_visible_candidate(confirmation) or await self._has_visible_candidate(
                already_applied_text
            ):
                confirmed = True
                break
            await page.wait_for_timeout(500)
        checkpoint = await self._browser_engine.capture_review_checkpoint(
            page, target="response-submitted" if confirmed else "response-status-unclear"
        )
        actions.append(checkpoint)
        if not confirmed:
            raise ApplyBlocked("Submission was not visibly confirmed; review the screenshot")
        return HeadHunterApplyResult(tuple(actions), False, page.url)

    @staticmethod
    async def _has_visible_candidate(locator: Locator) -> bool:
        try:
            return await locator.count() > 0 and await locator.first.is_visible()
        except Exception:  # noqa: BLE001 - stale confirmation candidates are treated as absent
            return False

    async def search(
        self, *, text: str, location_names: list[str] | None = None, limit: int = 15
    ) -> list[HeadHunterSearchHit]:
        """Search hh.ru's public results page (no login required) instead of api.hh.ru.

        Only public search-result pages are visited; no session is required for this method
        (only ``apply`` needs a captured session). CAPTCHA still routes to ``CaptchaChallenge``.
        """
        area_ids = resolve_known_area_ids(location_names or [])
        query = "&".join([f"text={quote(text)}", *[f"area={area_id}" for area_id in area_ids]])
        page = await self._browser_engine.new_page()
        navigation = await self._browser_engine.navigate(
            page, f"https://hh.ru/search/vacancy?{query}"
        )
        if not navigation.is_successful:
            raise RuntimeError(f"hh.ru search navigation failed: {navigation.error_category}")
        await self._raise_if_captcha(page)

        # hh.ru has used more than one data-qa naming scheme for search-result cards over time;
        # try known variants in order and log which (if any) actually matched, so a zero-result
        # search can be told apart from "these selectors are stale" instead of just returning [].
        card_selector_candidates = (
            "[data-qa='vacancy-serp__vacancy']",
            "[data-qa='vacancy-serp__vacancy_redesigned']",
            "[data-qa='serp-item']",
        )
        cards = page.locator(", ".join(card_selector_candidates))
        raw_count = await cards.count()
        count = min(raw_count, limit)
        if raw_count == 0:
            checkpoint = await self._browser_engine.capture_review_checkpoint(
                page, target="search-zero-results"
            )
            logger.warning(
                "headhunter_search_zero_results",
                url=page.url,
                page_title=await page.title(),
                screenshot_path=checkpoint.screenshot_path,
            )
        hits: list[HeadHunterSearchHit] = []
        for index in range(count):
            card = cards.nth(index)
            title_link = card.locator(
                "[data-qa='serp-item__title'], a[data-qa='vacancy-serp__vacancy-title']"
            ).first
            if await title_link.count() == 0:
                continue
            href = await title_link.get_attribute("href") or ""
            vacancy_id = _vacancy_id_from_href(href)
            if vacancy_id is None:
                continue
            title = (await title_link.inner_text()).strip()
            company_locator = card.locator("[data-qa='vacancy-serp__vacancy-employer']").first
            company = (
                (await company_locator.inner_text()).strip()
                if await company_locator.count() > 0
                else ""
            )
            hits.append(
                HeadHunterSearchHit(
                    vacancy_id=vacancy_id,
                    source_url=f"https://hh.ru/vacancy/{vacancy_id}",
                    title=title,
                    company=company,
                )
            )
        return hits

    async def extract_vacancy(self, url: str) -> ExtractedHeadHunterVacancy:
        """Read a public vacancy page (no login required) instead of api.hh.ru/vacancies/{id}."""
        if not self.supports_url(url):
            raise ValueError("URL is not a supported hh.ru host")
        page = await self._browser_engine.new_page()
        navigation = await self._browser_engine.navigate(page, url)
        if not navigation.is_successful:
            raise RuntimeError(f"hh.ru navigation failed: {navigation.error_category}")
        await self._raise_if_captcha(page)
        if not self.supports_url(page.url):
            raise RuntimeError("hh.ru navigation left the trusted host")

        title_locator = page.locator("[data-qa='vacancy-title']").first
        if await title_locator.count() == 0:
            raise ApplyBlocked("This does not look like a live hh.ru vacancy page")
        title = (await title_locator.inner_text()).strip()
        company_locator = page.locator(
            "[data-qa='vacancy-company-name'], a[data-qa='vacancy-serp__vacancy-employer']"
        ).first
        company = (
            (await company_locator.inner_text()).strip()
            if await company_locator.count() > 0
            else ""
        )
        location_locator = page.locator("[data-qa='vacancy-view-location']").first
        location = (
            (await location_locator.inner_text()).strip()
            if await location_locator.count() > 0
            else ""
        )
        description_locator = page.locator("[data-qa='vacancy-description']").first
        description_text = (
            (await description_locator.inner_text()).strip()
            if await description_locator.count() > 0
            else ""
        )
        publication_locator = page.locator(
            "time[data-qa='vacancy-creation-time'],[data-qa='vacancy-creation-time']"
        ).first
        publication_value = (
            await publication_locator.get_attribute("datetime")
            if await publication_locator.count() > 0
            else None
        )
        if publication_value is None and await publication_locator.count() > 0:
            publication_value = await publication_locator.inner_text()
        published_at = parse_publication_datetime(publication_value)
        skill_locator = page.locator("[data-qa='skills-element']")
        required_skills: list[str] = []
        for index in range(await skill_locator.count()):
            skill = (await skill_locator.nth(index).inner_text()).strip()
            if skill:
                required_skills.append(skill)
        response_letter_indicator = page.locator("[data-qa='vacancy-response-letter-informer']")
        response_letter_required = await response_letter_indicator.count() > 0
        has_test_indicator = page.get_by_text("необходимо пройти тест", exact=False)
        requires_sensitive_review = await has_test_indicator.count() > 0

        fields = [
            FormField(
                "resume",
                "Резюме",
                FormFieldType.FILE,
                True,
                semantic_category="resume",
                confidence=1.0,
                source_locator="headhunter-browser:resume",
            )
        ]
        if response_letter_required:
            fields.append(
                FormField(
                    "cover_letter",
                    "Сопроводительное письмо",
                    FormFieldType.TEXTAREA,
                    True,
                    semantic_category="cover_letter",
                    confidence=0.8,
                    source_locator="headhunter-browser:response-letter-informer",
                )
            )
        return ExtractedHeadHunterVacancy(
            source_url=url,
            title=title,
            company=company,
            location=location,
            description_text=description_text,
            required_skills=tuple(required_skills),
            form_fields=tuple(fields),
            requires_sensitive_review=requires_sensitive_review,
            published_at=published_at,
        )

    async def _find_cover_letter_field(self, page: Page) -> Locator | None:
        """Return the first known cover-letter control that is actually editable."""
        selectors = (*_COVER_LETTER_EDITABLE_SELECTORS, _COVER_LETTER_INFORMER_SELECTOR)
        for selector in selectors:
            candidate = page.locator(selector).first
            try:
                if await candidate.count() > 0 and await candidate.is_editable():
                    return candidate
            except Exception:  # noqa: BLE001 - stale/non-form candidates are skipped
                continue
        return None

    async def _reveal_cover_letter_field(
        self, page: Page, actions: list[BrowserActionResult]
    ) -> Locator | None:
        """Open hh.ru's optional cover-letter editor when the textarea starts collapsed."""
        for selector in _COVER_LETTER_REVEAL_SELECTORS:
            reveal_button = page.locator(selector).first
            try:
                if await reveal_button.count() == 0 or not await reveal_button.is_visible():
                    continue
            except Exception:  # noqa: BLE001 - stale candidates are skipped
                continue
            reveal_action = await self._click(
                page, reveal_button, "reveal-cover-letter", already_ok=False
            )
            actions.append(reveal_action)
            if not reveal_action.is_successful:
                continue
            with suppress(Exception):
                await page.wait_for_timeout(250)
            letter_field = await self._find_cover_letter_field(page)
            if letter_field is not None:
                return letter_field
        return None

    async def _wait_for_response_form(self, page: Page, response_button: Locator) -> None:
        """Wait until hh.ru finishes the async request started by the response button."""
        loading_indicator = response_button.locator("[role='status']").first
        try:
            if await loading_indicator.count() > 0:
                await loading_indicator.wait_for(state="hidden", timeout=20_000)
            await page.wait_for_timeout(250)
        except PlaywrightTimeoutError as error:
            await capture_browser_failure(
                page,
                artifact_directory=self._browser_engine.artifact_directory,
                action_name="wait",
                target="response-form-loading",
                error=error,
            )
            raise ApplyBlocked("hh.ru response form did not finish loading") from error

    async def _raise_if_logged_out(self, page: Page) -> None:
        login_link = page.locator("[data-qa='mainmenu_loginOrRegister'],a[href*='/account/login']")
        if await login_link.count() > 0:
            raise LoginRequired("hh.ru session is not authenticated")

    async def _raise_if_captcha(self, page: Page) -> None:
        current_url = page.url.casefold()
        is_captcha = any(marker in current_url for marker in _CAPTCHA_MARKERS) or (
            await page.locator("iframe[src*='captcha'],[class*='captcha']").count() > 0
        )
        if not is_captcha:
            return
        checkpoint = await self._browser_engine.capture_review_checkpoint(page, target="captcha")
        raise CaptchaChallenge(
            "hh.ru presented a CAPTCHA/verification checkpoint",
            screenshot_path=checkpoint.screenshot_path,
        )

    async def _click(
        self, page: Page, locator: Locator, target: str, *, already_ok: bool
    ) -> BrowserActionResult:
        started_at = time.monotonic()
        try:
            await locator.click(timeout=10_000)
            return BrowserActionResult(
                action_name="click",
                target=target,
                is_successful=True,
                duration_ms=round((time.monotonic() - started_at) * 1000),
                resulting_url=page.url,
            )
        except Exception as error:  # noqa: BLE001 - normalised into a typed failure below
            evidence = await capture_browser_failure(
                page,
                artifact_directory=self._browser_engine.artifact_directory,
                action_name="click",
                target=target,
                error=error,
            )
            return BrowserActionResult(
                action_name="click",
                target=target,
                is_successful=False,
                duration_ms=round((time.monotonic() - started_at) * 1000),
                resulting_url=page.url,
                error_category=FailureCategory.SELECTOR_FAILURE,
                screenshot_path=str(evidence.screenshot_path),
            )

    async def _safe_fill(self, locator: Locator, value: str) -> BrowserActionResult:
        started_at = time.monotonic()
        try:
            await locator.fill(value, timeout=10_000)
            return BrowserActionResult(
                action_name="fill",
                target="cover-letter",
                is_successful=True,
                duration_ms=round((time.monotonic() - started_at) * 1000),
                resulting_url="",
            )
        except Exception:  # noqa: BLE001
            return BrowserActionResult(
                action_name="fill",
                target="cover-letter",
                is_successful=False,
                duration_ms=round((time.monotonic() - started_at) * 1000),
                resulting_url="",
                error_category=FailureCategory.SELECTOR_FAILURE,
            )
