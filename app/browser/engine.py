from __future__ import annotations

import json
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import uuid4

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    StorageState,
    async_playwright,
)

from app.browser.evidence import capture_browser_failure
from app.browser.selector_library import (
    InvalidSelectorLibrary,
    SelectorCandidate,
    SelectorKind,
    SelectorLibrary,
)
from app.domain.failures import FailureCategory
from app.domain.forms import FormField, FormFieldType, validate_field_answer


@dataclass(frozen=True, slots=True)
class BrowserActionResult:
    action_name: str
    target: str
    is_successful: bool
    duration_ms: int
    resulting_url: str
    screenshot_path: str | None = None
    error_category: FailureCategory | None = None
    should_retry: bool = False
    selector_attempts: tuple[str, ...] = ()


class PlaywrightEngine:
    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_ms: int = 30_000,
        artifact_directory: Path = Path(".artifacts/browser"),
        storage_state: dict[str, object] | None = None,
        selector_library: SelectorLibrary | None = None,
        max_selector_attempts: int = 3,
    ) -> None:
        if not 1 <= max_selector_attempts <= 5:
            raise ValueError("max_selector_attempts must be between 1 and 5")
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._artifact_directory = artifact_directory
        self._storage_state = storage_state
        self._selector_library = selector_library
        self._max_selector_attempts = max_selector_attempts
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> PlaywrightEngine:
        self._artifact_directory.mkdir(parents=True, exist_ok=True)
        playwright = await async_playwright().start()
        self._playwright = playwright
        self._browser = await playwright.chromium.launch(headless=self._headless)
        playwright_state = cast(StorageState | None, self._storage_state)
        self._context = await self._browser.new_context(storage_state=playwright_state)
        self._context.set_default_timeout(self._timeout_ms)
        return self

    async def __aexit__(self, *_error: object) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    @property
    def artifact_directory(self) -> Path:
        return self._artifact_directory

    async def new_page(self) -> Page:
        if self._context is None:
            raise RuntimeError("PlaywrightEngine must be used as an async context manager")
        return await self._context.new_page()

    async def storage_state(self) -> dict[str, object]:
        if self._context is None:
            raise RuntimeError("PlaywrightEngine must be used as an async context manager")
        return cast(dict[str, object], await self._context.storage_state())

    async def navigate(self, page: Page, url: str) -> BrowserActionResult:
        started_at = time.monotonic()
        try:
            await page.goto(url, wait_until="domcontentloaded")
            return self._result("navigate", url, True, started_at, page.url)
        except Exception as error:
            evidence = await capture_browser_failure(
                page,
                artifact_directory=self._artifact_directory,
                action_name="navigate",
                target=url,
                error=error,
            )
            return self._result(
                "navigate",
                url,
                False,
                started_at,
                page.url,
                FailureCategory.TRANSIENT_NETWORK_ERROR,
                True,
                str(evidence.screenshot_path),
            )

    async def fill_review_form(
        self,
        page: Page,
        *,
        full_name: str,
        email: str,
        resume_path: Path,
    ) -> tuple[BrowserActionResult, ...]:
        actions: list[BrowserActionResult] = []
        actions.append(await self._fill(page, "Full name", full_name))
        actions.append(await self._fill(page, "Email", email))
        started_at = time.monotonic()
        try:
            await page.get_by_label("Resume").set_input_files(resume_path)
            actions.append(self._result("upload", "Resume", True, started_at, page.url))
        except Exception as error:
            evidence = await capture_browser_failure(
                page,
                artifact_directory=self._artifact_directory,
                action_name="upload",
                target="Resume",
                error=error,
            )
            actions.append(
                self._result(
                    "upload",
                    "Resume",
                    False,
                    started_at,
                    page.url,
                    FailureCategory.SELECTOR_FAILURE,
                    screenshot_path=str(evidence.screenshot_path),
                )
            )
        actions.append(await self.capture_review_checkpoint(page))
        return tuple(actions)

    async def capture_review_checkpoint(
        self, page: Page, *, target: str = "review-before-submit"
    ) -> BrowserActionResult:
        """Capture evidence without interacting with any submit control."""
        started_at = time.monotonic()
        screenshot_path = self._artifact_directory / f"review-ready-{uuid4().hex}.png"
        try:
            await page.screenshot(path=screenshot_path, full_page=True)
            return self._result(
                "checkpoint",
                target,
                True,
                started_at,
                page.url,
                screenshot_path=str(screenshot_path),
            )
        except Exception as error:
            evidence = await capture_browser_failure(
                page,
                artifact_directory=self._artifact_directory,
                action_name="checkpoint",
                target=target,
                error=error,
            )
            return self._result(
                "checkpoint",
                target,
                False,
                started_at,
                page.url,
                FailureCategory.INTERNAL_DEFECT,
                screenshot_path=str(evidence.screenshot_path),
            )

    async def fill_discovered_field(
        self,
        page: Page,
        field: FormField,
        answer: str | bool | None,
        *,
        adapter_name: str = "generic",
    ) -> BrowserActionResult:
        started_at = time.monotonic()
        validation_errors = validate_field_answer(field, answer)
        if validation_errors:
            return self._result(
                "fill",
                field.label or field.field_id,
                False,
                started_at,
                page.url,
                FailureCategory.VALIDATION_ERROR,
            )
        if answer is None or answer == "":
            return self._result("fill", field.label or field.field_id, True, started_at, page.url)
        if field.field_type is FormFieldType.UNKNOWN:
            return self._result(
                "fill",
                field.label or field.field_id,
                False,
                started_at,
                page.url,
                FailureCategory.UNSUPPORTED_WORKFLOW,
            )
        candidates = self._selector_candidates(field, answer, adapter_name)
        attempted: list[str] = []
        last_error: Exception = LookupError("No selector candidate was available")
        for candidate in candidates[: self._max_selector_attempts]:
            attempted.append(candidate.evidence)
            try:
                await self._apply_field_answer(
                    self._locator_for_candidate(page, candidate), field, answer
                )
            except Exception as error:
                last_error = error
                continue
            if len(attempted) > 1 and self._selector_library is not None:
                with suppress(InvalidSelectorLibrary, OSError):
                    self._selector_library.promote(adapter_name, field.field_id, candidate)
            return self._result(
                "fill",
                field.label or field.field_id,
                True,
                started_at,
                page.url,
                selector_attempts=tuple(attempted),
            )
        evidence = await capture_browser_failure(
            page,
            artifact_directory=self._artifact_directory,
            action_name="fill",
            target=field.label or field.field_id,
            error=last_error,
        )
        return self._result(
            "fill",
            field.label or field.field_id,
            False,
            started_at,
            page.url,
            FailureCategory.SELECTOR_FAILURE,
            screenshot_path=str(evidence.screenshot_path),
            selector_attempts=tuple(attempted),
        )

    def _selector_candidates(
        self, field: FormField, answer: str | bool, adapter_name: str
    ) -> tuple[SelectorCandidate, ...]:
        if field.field_type is FormFieldType.RADIO:
            return (SelectorCandidate(SelectorKind.LABEL, str(answer)),)
        candidates: list[SelectorCandidate] = []
        if self._selector_library is not None:
            with suppress(InvalidSelectorLibrary):
                candidates.extend(self._selector_library.candidates(adapter_name, field.field_id))
        if field.label:
            candidates.append(SelectorCandidate(SelectorKind.LABEL, field.label))
            candidates.append(SelectorCandidate(SelectorKind.PLACEHOLDER, field.label))
        candidates.append(SelectorCandidate(SelectorKind.ID, field.field_id))
        unique: dict[str, SelectorCandidate] = {}
        for candidate in candidates:
            unique.setdefault(candidate.evidence, candidate)
        return tuple(unique.values())

    @staticmethod
    def _locator_for_candidate(page: Page, candidate: SelectorCandidate) -> Locator:
        if candidate.kind is SelectorKind.LABEL:
            return page.get_by_label(candidate.value, exact=True)
        if candidate.kind is SelectorKind.PLACEHOLDER:
            return page.get_by_placeholder(candidate.value, exact=True)
        attribute = "id" if candidate.kind is SelectorKind.ID else "name"
        return page.locator(f"[{attribute}={json.dumps(candidate.value)}]")

    @staticmethod
    async def _apply_field_answer(control: Locator, field: FormField, answer: str | bool) -> None:
        if field.field_type in {FormFieldType.RADIO, FormFieldType.CHECKBOX}:
            if answer is True or field.field_type is FormFieldType.RADIO:
                await control.check()
            else:
                await control.uncheck()
        elif field.field_type is FormFieldType.SELECT:
            await control.select_option(label=str(answer))
        elif field.field_type is FormFieldType.FILE:
            await control.set_input_files(Path(str(answer)))
        else:
            await control.fill(str(answer))

    async def _fill(self, page: Page, label: str, value: str) -> BrowserActionResult:
        started_at = time.monotonic()
        try:
            await page.get_by_label(label).fill(value)
            return self._result("fill", label, True, started_at, page.url)
        except Exception as error:
            evidence = await capture_browser_failure(
                page,
                artifact_directory=self._artifact_directory,
                action_name="fill",
                target=label,
                error=error,
            )
            return self._result(
                "fill",
                label,
                False,
                started_at,
                page.url,
                FailureCategory.SELECTOR_FAILURE,
                screenshot_path=str(evidence.screenshot_path),
            )

    @staticmethod
    def _result(
        action_name: str,
        target: str,
        is_successful: bool,
        started_at: float,
        resulting_url: str,
        error_category: FailureCategory | None = None,
        should_retry: bool = False,
        screenshot_path: str | None = None,
        selector_attempts: tuple[str, ...] = (),
    ) -> BrowserActionResult:
        return BrowserActionResult(
            action_name=action_name,
            target=target,
            is_successful=is_successful,
            duration_ms=round((time.monotonic() - started_at) * 1000),
            resulting_url=resulting_url,
            error_category=error_category,
            should_retry=should_retry,
            screenshot_path=screenshot_path,
            selector_attempts=selector_attempts,
        )
