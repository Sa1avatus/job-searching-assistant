"""Browser-automated LinkedIn "Easy Apply" submission.

LinkedIn's User Agreement prohibits scraping and unauthorized automated access, and there is no
approved job-seeker application API for individual users. This adapter exists because the user
explicitly requested it and accepted the risk (primarily account restriction/ban) after being
informed of it; it is disabled by default (``APP_ENABLE_LINKEDIN_APPLY=false``) and requires a
session captured through ``scripts/browser_login_capture.py`` — the user logs in and solves any
verification challenge by hand, so the automation never sees a password and never solves a
challenge itself.

Scope, deliberately: only the native multi-step "Easy Apply" flow is automated. Jobs that redirect
to an external company site are reported as ``ApplyBlocked`` and left for manual/Greenhouse-style
handling, because blindly following an arbitrary redirect from LinkedIn is a separate, larger
risk surface (arbitrary third-party form, no host allowlist).

Selectors use LinkedIn's stable-ish ``aria-label``/``data-*`` hooks and have not been exercised
against the live site from this environment (no network egress here). Verify locally with
``APP_BROWSER_HEADLESS=false`` before unattended use, and expect to maintain these selectors —
LinkedIn changes its DOM more often than Greenhouse/hh.ru.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import quote, urlparse

from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from adapters.job_boards.browser_apply_common import ApplyBlocked, CaptchaChallenge, LoginRequired
from app.browser.engine import BrowserActionResult, PlaywrightEngine
from app.browser.evidence import capture_browser_failure
from app.browser.form_discovery import discover_form_fields
from app.domain.failures import FailureCategory

_HOST_SUFFIX = "linkedin.com"
_MAX_EASY_APPLY_STEPS = 8
_CHALLENGE_URL_MARKERS = ("/checkpoint/", "/uas/login", "/authwall")


@dataclass(frozen=True, slots=True)
class LinkedInSearchHit:
    job_id: str
    source_url: str
    title: str
    company: str


@dataclass(frozen=True, slots=True)
class ExtractedLinkedInVacancy:
    source_url: str
    title: str
    company: str
    location: str
    description_text: str
    has_easy_apply: bool


@dataclass(frozen=True, slots=True)
class LinkedInApplyResult:
    actions: tuple[BrowserActionResult, ...]
    steps_completed: int
    confirmation_url: str

    @property
    def is_successful(self) -> bool:
        return bool(self.actions) and all(action.is_successful for action in self.actions)


class LinkedInBrowserAdapter:
    name = "linkedin-browser"

    def __init__(self, browser_engine: PlaywrightEngine) -> None:
        self._browser_engine = browser_engine

    def supports_url(self, url: str) -> bool:
        hostname = (urlparse(url).hostname or "").casefold()
        return hostname == _HOST_SUFFIX or hostname.endswith(f".{_HOST_SUFFIX}")

    async def apply(
        self,
        url: str,
        *,
        answers: dict[str, str | bool | None] | None = None,
    ) -> LinkedInApplyResult:
        """Open the vacancy with the restored session and complete the Easy Apply flow.

        Raises ``LoginRequired`` if the session is not authenticated, ``CaptchaChallenge`` if
        LinkedIn presents a verification checkpoint, and ``ApplyBlocked`` if the job has no native
        Easy Apply control (external application) or is already applied to.
        """
        if not self.supports_url(url):
            raise ValueError("URL is not a supported linkedin.com host")
        answers = answers or {}
        page = await self._browser_engine.new_page()
        navigation = await self._browser_engine.navigate(page, url)
        if not navigation.is_successful:
            raise RuntimeError(f"LinkedIn navigation failed: {navigation.error_category}")
        await self._raise_if_challenge_url(page)
        if not self.supports_url(page.url):
            raise RuntimeError("LinkedIn navigation left the trusted host")

        actions: list[BrowserActionResult] = []
        applied_marker = page.get_by_text("Application submitted", exact=False)
        if await applied_marker.count() > 0:
            checkpoint = await self._browser_engine.capture_review_checkpoint(
                page, target="already-applied"
            )
            return LinkedInApplyResult((checkpoint,), 0, page.url)

        easy_apply_button = page.get_by_role("button", name="Easy Apply").first
        if await easy_apply_button.count() == 0:
            raise ApplyBlocked(
                "This job has no native Easy Apply control (likely an external application)"
            )
        actions.append(await self._click(page, easy_apply_button, "open-easy-apply-modal"))
        if not actions[-1].is_successful:
            raise ApplyBlocked("Could not open the Easy Apply modal")
        await self._raise_if_challenge_url(page)

        steps_completed = 0
        modal = page.locator(
            "div.jobs-easy-apply-modal, div[data-test-modal-id='easy-apply-modal']"
        )
        for _ in range(_MAX_EASY_APPLY_STEPS):
            await self._raise_if_challenge_url(page)
            submit_button = modal.get_by_role("button", name="Submit application")
            if await submit_button.count() > 0:
                actions.append(await self._click(page, submit_button.first, "submit-application"))
                if not actions[-1].is_successful:
                    raise ApplyBlocked("Could not click the final Submit application control")
                steps_completed += 1
                break

            fields = await discover_form_fields(page)
            for field in fields:
                answer = answers.get(field.field_id, field.current_value)
                if field.field_id in answers or (field.is_required and answer is not None):
                    actions.append(
                        await self._browser_engine.fill_discovered_field(
                            page, field, answer, adapter_name=self.name
                        )
                    )

            next_button = modal.get_by_role("button", name="Review")
            if await next_button.count() == 0:
                next_button = modal.get_by_role("button", name="Next")
            if await next_button.count() == 0:
                raise ApplyBlocked(
                    "No Next/Review/Submit control found; the Easy Apply flow may need "
                    "unsupported required answers"
                )
            actions.append(await self._click(page, next_button.first, "advance-easy-apply-step"))
            if not actions[-1].is_successful:
                error_banner = modal.locator("[role='alert'], .artdeco-inline-feedback--error")
                if await error_banner.count() > 0:
                    raise ApplyBlocked("Easy Apply step reported a validation error")
                raise ApplyBlocked("Could not advance the Easy Apply step")
            steps_completed += 1
        else:
            raise ApplyBlocked("Easy Apply exceeded the maximum number of expected steps")

        confirmation = page.get_by_text("Application submitted", exact=False)
        try:
            await confirmation.first.wait_for(state="visible", timeout=10_000)
            confirmed = True
        except PlaywrightTimeoutError:
            confirmed = False
        checkpoint = await self._browser_engine.capture_review_checkpoint(
            page, target="easy-apply-submitted" if confirmed else "easy-apply-status-unclear"
        )
        actions.append(checkpoint)
        if not confirmed:
            raise ApplyBlocked("Submission was not visibly confirmed; review the screenshot")
        return LinkedInApplyResult(tuple(actions), steps_completed, page.url)

    async def search(
        self, *, text: str, location_names: list[str] | None = None, limit: int = 15
    ) -> list[LinkedInSearchHit]:
        """Search LinkedIn's job search results page. Requires a signed-in session.

        Unlike hh.ru, LinkedIn's job search is effectively unusable anonymously (results are
        heavily truncated and quickly hit an auth wall), so this method expects the engine to
        have been constructed with a session captured via ``scripts/browser_login_capture.py``.
        """
        location = (location_names or [""])[0]
        query = f"keywords={quote(text)}"
        if location:
            query += f"&location={quote(location)}"
        page = await self._browser_engine.new_page()
        navigation = await self._browser_engine.navigate(
            page, f"https://www.linkedin.com/jobs/search/?{query}"
        )
        if not navigation.is_successful:
            raise RuntimeError(f"LinkedIn search navigation failed: {navigation.error_category}")
        await self._raise_if_challenge_url(page)

        cards = page.locator("[data-job-id]")
        count = min(await cards.count(), limit)
        hits: list[LinkedInSearchHit] = []
        for index in range(count):
            card = cards.nth(index)
            job_id = await card.get_attribute("data-job-id")
            if not job_id:
                continue
            title_locator = card.locator(".job-card-list__title, .job-card-container__link").first
            title = (
                (await title_locator.inner_text()).strip()
                if await title_locator.count() > 0
                else ""
            )
            company_locator = card.locator(".job-card-container__company-name").first
            company = (
                (await company_locator.inner_text()).strip()
                if await company_locator.count() > 0
                else ""
            )
            hits.append(
                LinkedInSearchHit(
                    job_id=job_id,
                    source_url=f"https://www.linkedin.com/jobs/view/{job_id}",
                    title=title,
                    company=company,
                )
            )
        return hits

    async def extract_vacancy(self, url: str) -> ExtractedLinkedInVacancy:
        """Read a job posting's detail pane. Requires a signed-in session (same as search)."""
        if not self.supports_url(url):
            raise ValueError("URL is not a supported linkedin.com host")
        page = await self._browser_engine.new_page()
        navigation = await self._browser_engine.navigate(page, url)
        if not navigation.is_successful:
            raise RuntimeError(f"LinkedIn navigation failed: {navigation.error_category}")
        await self._raise_if_challenge_url(page)
        if not self.supports_url(page.url):
            raise RuntimeError("LinkedIn navigation left the trusted host")

        title_locator = page.locator(".job-details-jobs-unified-top-card__job-title, h1").first
        if await title_locator.count() == 0:
            raise ApplyBlocked("This does not look like a live LinkedIn job posting page")
        title = (await title_locator.inner_text()).strip()
        company_locator = page.locator(
            ".job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name"
        ).first
        company = (
            (await company_locator.inner_text()).strip()
            if await company_locator.count() > 0
            else ""
        )
        location_locator = page.locator(
            ".job-details-jobs-unified-top-card__primary-description-container"
        ).first
        location = (
            (await location_locator.inner_text()).strip()
            if await location_locator.count() > 0
            else ""
        )
        description_locator = page.locator(".jobs-description__content").first
        description_text = (
            (await description_locator.inner_text()).strip()
            if await description_locator.count() > 0
            else ""
        )
        has_easy_apply = await page.get_by_role("button", name="Easy Apply").count() > 0
        return ExtractedLinkedInVacancy(
            source_url=url,
            title=title,
            company=company,
            location=location,
            description_text=description_text,
            has_easy_apply=has_easy_apply,
        )

    async def _raise_if_challenge_url(self, page: Page) -> None:
        current_url = page.url.casefold()
        if not any(marker in current_url for marker in _CHALLENGE_URL_MARKERS):
            return
        if "/uas/login" in current_url or "/authwall" in current_url:
            raise LoginRequired("LinkedIn session is not authenticated")
        checkpoint = await self._browser_engine.capture_review_checkpoint(page, target="captcha")
        raise CaptchaChallenge(
            "LinkedIn presented a verification checkpoint",
            screenshot_path=checkpoint.screenshot_path,
        )

    async def _click(self, page: Page, locator: Locator, target: str) -> BrowserActionResult:
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
