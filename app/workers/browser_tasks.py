from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from adapters.job_boards.browser_apply_common import ApplyBlocked, CaptchaChallenge, LoginRequired
from adapters.job_boards.greenhouse import GreenhouseAdapter
from adapters.job_boards.greenhouse_api import GreenhouseJobReference
from adapters.job_boards.headhunter_browser import HeadHunterBrowserAdapter
from adapters.job_boards.linkedin_browser import LinkedInBrowserAdapter
from app.browser.engine import PlaywrightEngine
from app.browser.session_service import BrowserSessionNotFound, BrowserSessionService
from app.browser.session_store import EncryptedBrowserStateStore, InvalidBrowserState
from app.browser.selector_library import SelectorLibrary
from app.config import Settings
from app.domain.failures import FailureCategory
from app.domain.models import TaskState
from app.storage.tables import ApplicationRow, CvFileRow, UserRow, VacancyRow
from app.storage.task_repository import ClaimedTask
from app.workers.dispatcher import ExecutionOutcome, HumanActionRequest, TaskHandler


class ControlledBrowserReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: Literal["controlled_review"]
    fixture_name: Literal["application"]


class GreenhouseBrowserReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: Literal["greenhouse_review"]


class HeadHunterApplyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: Literal["headhunter_apply"]


class LinkedInApplyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: Literal["linkedin_apply"]


class ControlledBrowserReviewHandler:
    """Exercises the durable browser queue without permitting an external target or submit."""

    def __init__(self, settings: Settings, fixture_directory: Path) -> None:
        self._settings = settings
        self._fixture_directory = fixture_directory.resolve()

    async def handle(self, claimed_task: ClaimedTask) -> ExecutionOutcome:
        try:
            payload = ControlledBrowserReviewPayload.model_validate(claimed_task.payload)
        except ValidationError:
            return ExecutionOutcome(TaskState.FAILED, "browser task payload is invalid")
        if payload.fixture_name != "application":
            return ExecutionOutcome(TaskState.FAILED, "browser fixture is not allowlisted")
        application_path = self._resolve_fixture("controlled_application.html")
        resume_path = self._resolve_fixture("controlled_resume.pdf")
        task_artifact_directory = (
            self._settings.artifact_directory / "browser-worker" / claimed_task.task_id
        )
        async with PlaywrightEngine(
            headless=self._settings.browser_headless,
            timeout_ms=self._settings.browser_timeout_ms,
            artifact_directory=task_artifact_directory,
            selector_library=SelectorLibrary(self._settings.artifact_directory),
        ) as browser_engine:
            page = await browser_engine.new_page()
            navigation = await browser_engine.navigate(page, application_path.as_uri())
            if not navigation.is_successful:
                return ExecutionOutcome(
                    TaskState.RETRY_SCHEDULED,
                    "controlled browser navigation failed",
                    (navigation.error_category or "unknown",),
                )
            form_actions = await browser_engine.fill_review_form(
                page,
                full_name="Controlled Candidate",
                email="candidate@example.test",
                resume_path=resume_path,
            )
        failures = tuple(action for action in form_actions if not action.is_successful)
        if failures:
            return ExecutionOutcome(
                TaskState.FAILED,
                "controlled browser review action failed",
                tuple(str(action.error_category or "unknown") for action in failures),
            )
        screenshot_path = form_actions[-1].screenshot_path
        return ExecutionOutcome(
            TaskState.WAITING_FOR_USER,
            "controlled application prepared at review boundary without submission",
            (
                "target:controlled_fixture",
                f"screenshot:{screenshot_path or 'unavailable'}",
                "submission:false",
            ),
            HumanActionRequest(
                kind="application_review",
                instructions="Review the prepared controlled form; no submission was performed.",
                screenshot_path=Path(screenshot_path) if screenshot_path else None,
            ),
        )

    def _resolve_fixture(self, filename: str) -> Path:
        candidate = (self._fixture_directory / filename).resolve()
        if not candidate.is_relative_to(self._fixture_directory) or not candidate.is_file():
            raise RuntimeError("Controlled browser fixture is unavailable")
        return candidate


class GreenhouseBrowserReviewHandler:
    """Prepare a persisted Greenhouse application for review without submitting it."""

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        *,
        adapter_factory: Callable[[PlaywrightEngine], GreenhouseAdapter] = GreenhouseAdapter,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._adapter_factory = adapter_factory
        self._document_directory = (settings.artifact_directory / "documents").resolve()

    async def handle(self, claimed_task: ClaimedTask) -> ExecutionOutcome:
        try:
            GreenhouseBrowserReviewPayload.model_validate(claimed_task.payload)
        except ValidationError:
            return ExecutionOutcome(
                TaskState.FAILED,
                "Greenhouse browser task payload is invalid",
                ("submission:false",),
            )
        if claimed_task.application_id is None:
            return ExecutionOutcome(TaskState.FAILED, "Greenhouse task has no application id")
        try:
            source_url, answers = self._load_review_inputs(claimed_task.application_id)
            GreenhouseJobReference.from_url(source_url)
        except (LookupError, RuntimeError, ValueError) as error:
            return ExecutionOutcome(
                TaskState.FAILED,
                "Greenhouse review inputs failed validation",
                (type(error).__name__, "submission:false"),
            )

        task_artifact_directory = (
            self._settings.artifact_directory / "browser-worker" / claimed_task.task_id
        )
        async with PlaywrightEngine(
            headless=self._settings.browser_headless,
            timeout_ms=self._settings.browser_timeout_ms,
            artifact_directory=task_artifact_directory,
            selector_library=SelectorLibrary(self._settings.artifact_directory),
        ) as browser_engine:
            try:
                review = await self._adapter_factory(browser_engine).prepare_review(
                    source_url, answers
                )
            except RuntimeError as error:
                return ExecutionOutcome(
                    TaskState.RETRY_SCHEDULED,
                    "Greenhouse browser navigation failed",
                    (type(error).__name__, "submission:false"),
                )

        if review.is_ready_for_review:
            screenshot_path = review.actions[-1].screenshot_path
            return ExecutionOutcome(
                TaskState.WAITING_FOR_USER,
                "Greenhouse form prepared at review boundary without submission",
                (
                    f"application:{claimed_task.application_id}",
                    f"screenshot:{screenshot_path or 'unavailable'}",
                    "submission:false",
                ),
                HumanActionRequest(
                    kind="application_review",
                    instructions=(
                        "Review the prepared Greenhouse form evidence. No submission was performed."
                    ),
                    screenshot_path=Path(screenshot_path) if screenshot_path else None,
                ),
            )
        validation_failures = tuple(
            action.target
            for action in review.actions
            if action.error_category is FailureCategory.VALIDATION_ERROR
        )
        if validation_failures:
            return ExecutionOutcome(
                TaskState.WAITING_FOR_USER,
                "Greenhouse form requires additional reviewed answers",
                (*("missing:" + target for target in validation_failures), "submission:false"),
            )
        return ExecutionOutcome(
            TaskState.FAILED,
            "Greenhouse form preparation failed before review",
            (
                *(
                    str(action.error_category or "unknown")
                    for action in review.actions
                    if not action.is_successful
                ),
                "submission:false",
            ),
        )

    def _load_review_inputs(self, application_id: str) -> tuple[str, dict[str, str | bool | None]]:
        with self._session_factory() as session:
            application = session.get(ApplicationRow, application_id)
            if application is None or application.status != "awaiting_review":
                raise LookupError("Application is unavailable for review")
            vacancy = session.get(VacancyRow, application.vacancy_id)
            if vacancy is None or vacancy.adapter_name != "greenhouse":
                raise LookupError("Application does not use the Greenhouse adapter")
            answers: dict[str, str | bool | None] = {
                answer.field_id: answer.answer
                for answer in application.answers
                if answer.answer is not None
            }
            cv_path: Path | None = None
            if application.selected_cv_file_id is not None:
                cv_file = session.get(CvFileRow, application.selected_cv_file_id)
                if cv_file is None or cv_file.user_id != application.user_id:
                    raise LookupError("Selected CV is unavailable")
                cv_path = Path(cv_file.storage_path).resolve()
                if not cv_path.is_relative_to(self._document_directory) or not cv_path.is_file():
                    raise RuntimeError("Selected CV path is outside managed document storage")
            for field in vacancy.application_fields:
                field_id = str(field.get("field_id") or "").strip()
                semantic_category = str(field.get("semantic_category") or "")
                field_type = str(field.get("field_type") or "")
                if not field_id:
                    continue
                if semantic_category == "resume" and field_type == "file" and cv_path:
                    answers[field_id] = str(cv_path)
                elif (
                    semantic_category == "cover_letter"
                    and field_type != "file"
                    and application.cover_letter_text
                ):
                    answers[field_id] = application.cover_letter_text
            return vacancy.source_url, answers


def _load_apply_inputs(
    session_factory: sessionmaker[Session],
    application_id: str,
    expected_adapter_name: str,
) -> tuple[str, str, str, dict[str, str | bool | None]]:
    """Return (user_id, vacancy source_url, cover_letter_text, answers) for an apply task."""
    with session_factory() as session:
        application = session.get(ApplicationRow, application_id)
        if application is None or application.status != "awaiting_review":
            raise LookupError("Application is unavailable for review")
        vacancy = session.get(VacancyRow, application.vacancy_id)
        if vacancy is None or vacancy.adapter_name != expected_adapter_name:
            raise LookupError(f"Application does not use the {expected_adapter_name} adapter")
        answers: dict[str, str | bool | None] = {
            answer.field_id: answer.answer
            for answer in application.answers
            if answer.answer is not None
        }
        return (
            application.user_id,
            vacancy.source_url,
            application.cover_letter_text,
            answers,
        )


def _restore_browser_session(
    session_factory: sessionmaker[Session],
    store: EncryptedBrowserStateStore,
    *,
    user_id: str,
    site_key: str,
) -> dict[str, object] | None:
    """Look up and decrypt the user's captured session for a site, or None if unavailable."""
    from sqlalchemy import select

    from app.storage.tables import BrowserSessionRow

    with session_factory() as session:
        row_id = session.scalar(
            select(BrowserSessionRow.id).where(
                BrowserSessionRow.user_id == user_id,
                BrowserSessionRow.site_key == site_key,
            )
        )
    if row_id is None:
        return None
    with session_factory() as session:
        try:
            _row, state = BrowserSessionService(session, store).restore(row_id)
        except (BrowserSessionNotFound, InvalidBrowserState):
            return None
    return state


def _persist_browser_session(
    session_factory: sessionmaker[Session],
    store: EncryptedBrowserStateStore,
    *,
    user_id: str,
    site_key: str,
    adapter_name: str,
    state: dict[str, object],
    last_url: str,
) -> None:
    """Best-effort refresh of the stored session after a successful run (cookies rotate)."""
    with session_factory() as session:
        try:
            BrowserSessionService(session, store).save(
                user_id=user_id,
                site_key=site_key,
                adapter_name=adapter_name,
                state=state,
                last_url=last_url,
            )
        except (BrowserSessionNotFound, InvalidBrowserState):
            pass


_REAUTH_INSTRUCTIONS = (
    "Run `python scripts/browser_login_capture.py {site_key}`, sign in by hand in the browser "
    "window that opens (including any verification step), then retry this application."
)


class HeadHunterApplyHandler:
    """Submits a real hh.ru response using a session the user captured by hand.

    This performs an irreversible submission when it succeeds (unlike the Greenhouse review
    handler, which deliberately stops before submitting). It only runs when
    ``APP_ENABLE_HEADHUNTER_APPLY=true`` and a captured "headhunter" browser session exists for
    the applicant; otherwise it waits for the user instead of guessing.
    """

    site_key = "headhunter"

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        session_store: EncryptedBrowserStateStore,
        *,
        adapter_factory: Callable[[PlaywrightEngine], HeadHunterBrowserAdapter] = (
            HeadHunterBrowserAdapter
        ),
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._session_store = session_store
        self._adapter_factory = adapter_factory

    async def handle(self, claimed_task: ClaimedTask) -> ExecutionOutcome:
        try:
            HeadHunterApplyPayload.model_validate(claimed_task.payload)
        except ValidationError:
            return ExecutionOutcome(TaskState.FAILED, "hh.ru apply task payload is invalid")
        if not self._settings.enable_headhunter_apply:
            return ExecutionOutcome(
                TaskState.FAILED,
                "hh.ru browser apply is disabled; set APP_ENABLE_HEADHUNTER_APPLY=true to enable it",
            )
        if claimed_task.application_id is None:
            return ExecutionOutcome(TaskState.FAILED, "hh.ru apply task has no application id")
        try:
            user_id, source_url, cover_letter, _answers = _load_apply_inputs(
                self._session_factory, claimed_task.application_id, "headhunter"
            )
        except LookupError as error:
            return ExecutionOutcome(
                TaskState.FAILED, "hh.ru apply inputs failed validation", (str(error),)
            )

        state = _restore_browser_session(
            self._session_factory, self._session_store, user_id=user_id, site_key=self.site_key
        )
        if state is None:
            return ExecutionOutcome(
                TaskState.WAITING_FOR_USER,
                "no usable hh.ru browser session is available",
                ("submission:false",),
                HumanActionRequest(
                    kind="reauthenticate",
                    instructions=_REAUTH_INSTRUCTIONS.format(site_key=self.site_key),
                ),
            )

        task_artifact_directory = (
            self._settings.artifact_directory / "browser-worker" / claimed_task.task_id
        )
        async with PlaywrightEngine(
            headless=self._settings.browser_headless,
            timeout_ms=self._settings.browser_timeout_ms,
            artifact_directory=task_artifact_directory,
            storage_state=state,
            selector_library=SelectorLibrary(self._settings.artifact_directory),
        ) as browser_engine:
            adapter = self._adapter_factory(browser_engine)
            try:
                result = await adapter.apply(source_url, cover_letter=cover_letter or None)
            except LoginRequired:
                return ExecutionOutcome(
                    TaskState.WAITING_FOR_USER,
                    "hh.ru session is no longer authenticated",
                    ("submission:false",),
                    HumanActionRequest(
                        kind="reauthenticate",
                        instructions=_REAUTH_INSTRUCTIONS.format(site_key=self.site_key),
                    ),
                )
            except CaptchaChallenge as challenge:
                return ExecutionOutcome(
                    TaskState.WAITING_FOR_USER,
                    "hh.ru presented a CAPTCHA/verification checkpoint",
                    ("submission:false",),
                    HumanActionRequest(
                        kind="captcha",
                        instructions=(
                            "Open hh.ru in your normal signed-in browser, resolve the "
                            "verification checkpoint, then retry this application."
                        ),
                        screenshot_path=(
                            Path(challenge.screenshot_path) if challenge.screenshot_path else None
                        ),
                    ),
                )
            except ApplyBlocked as blocked:
                return ExecutionOutcome(
                    TaskState.FAILED,
                    "hh.ru response could not be completed automatically",
                    (str(blocked), "submission:unknown"),
                )
            fresh_state = await browser_engine.storage_state()

        _persist_browser_session(
            self._session_factory,
            self._session_store,
            user_id=user_id,
            site_key=self.site_key,
            adapter_name="headhunter",
            state=fresh_state,
            last_url=result.confirmation_url,
        )
        if result.already_applied:
            return ExecutionOutcome(
                TaskState.COMPLETED,
                "hh.ru reports this vacancy was already responded to",
                (f"application:{claimed_task.application_id}", "submission:already_applied"),
            )
        return ExecutionOutcome(
            TaskState.COMPLETED,
            "hh.ru application response was submitted",
            (f"application:{claimed_task.application_id}", "submission:true"),
        )


class LinkedInApplyHandler:
    """Submits a real LinkedIn Easy Apply application using a hand-captured session.

    Same shape as :class:`HeadHunterApplyHandler`. Only native Easy Apply jobs are supported;
    jobs that redirect to an external site raise ``ApplyBlocked`` and fail with a clear reason
    rather than following an unknown third-party form.
    """

    site_key = "linkedin"

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        session_store: EncryptedBrowserStateStore,
        *,
        adapter_factory: Callable[[PlaywrightEngine], LinkedInBrowserAdapter] = (
            LinkedInBrowserAdapter
        ),
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._session_store = session_store
        self._adapter_factory = adapter_factory

    async def handle(self, claimed_task: ClaimedTask) -> ExecutionOutcome:
        try:
            LinkedInApplyPayload.model_validate(claimed_task.payload)
        except ValidationError:
            return ExecutionOutcome(TaskState.FAILED, "LinkedIn apply task payload is invalid")
        if not self._settings.enable_linkedin_apply:
            return ExecutionOutcome(
                TaskState.FAILED,
                "LinkedIn browser apply is disabled; set APP_ENABLE_LINKEDIN_APPLY=true to enable it",
            )
        if claimed_task.application_id is None:
            return ExecutionOutcome(TaskState.FAILED, "LinkedIn apply task has no application id")
        try:
            user_id, source_url, _cover_letter, answers = _load_apply_inputs(
                self._session_factory, claimed_task.application_id, "linkedin-reference"
            )
        except LookupError as error:
            return ExecutionOutcome(
                TaskState.FAILED, "LinkedIn apply inputs failed validation", (str(error),)
            )

        state = _restore_browser_session(
            self._session_factory, self._session_store, user_id=user_id, site_key=self.site_key
        )
        if state is None:
            return ExecutionOutcome(
                TaskState.WAITING_FOR_USER,
                "no usable LinkedIn browser session is available",
                ("submission:false",),
                HumanActionRequest(
                    kind="reauthenticate",
                    instructions=_REAUTH_INSTRUCTIONS.format(site_key=self.site_key),
                ),
            )

        task_artifact_directory = (
            self._settings.artifact_directory / "browser-worker" / claimed_task.task_id
        )
        async with PlaywrightEngine(
            headless=self._settings.browser_headless,
            timeout_ms=self._settings.browser_timeout_ms,
            artifact_directory=task_artifact_directory,
            storage_state=state,
            selector_library=SelectorLibrary(self._settings.artifact_directory),
        ) as browser_engine:
            adapter = self._adapter_factory(browser_engine)
            try:
                result = await adapter.apply(source_url, answers=answers)
            except LoginRequired:
                return ExecutionOutcome(
                    TaskState.WAITING_FOR_USER,
                    "LinkedIn session is no longer authenticated",
                    ("submission:false",),
                    HumanActionRequest(
                        kind="reauthenticate",
                        instructions=_REAUTH_INSTRUCTIONS.format(site_key=self.site_key),
                    ),
                )
            except CaptchaChallenge as challenge:
                return ExecutionOutcome(
                    TaskState.WAITING_FOR_USER,
                    "LinkedIn presented a verification checkpoint",
                    ("submission:false",),
                    HumanActionRequest(
                        kind="captcha",
                        instructions=(
                            "Open LinkedIn in your normal signed-in browser, resolve the "
                            "verification checkpoint, then retry this application."
                        ),
                        screenshot_path=(
                            Path(challenge.screenshot_path) if challenge.screenshot_path else None
                        ),
                    ),
                )
            except ApplyBlocked as blocked:
                return ExecutionOutcome(
                    TaskState.FAILED,
                    "LinkedIn Easy Apply could not be completed automatically",
                    (str(blocked), "submission:unknown"),
                )
            fresh_state = await browser_engine.storage_state()

        _persist_browser_session(
            self._session_factory,
            self._session_store,
            user_id=user_id,
            site_key=self.site_key,
            adapter_name="linkedin",
            state=fresh_state,
            last_url=result.confirmation_url,
        )
        return ExecutionOutcome(
            TaskState.COMPLETED,
            "LinkedIn Easy Apply application was submitted",
            (f"application:{claimed_task.application_id}", "submission:true"),
        )


class ApplicationBrowserTaskHandler:
    """Routes an ``application-review`` task to the adapter-specific handler by payload workflow.

    All applications share one durable task row (and idempotency key) created at
    ``prepare_application`` time; scheduling a browser step only changes ``queue_name`` and
    ``task_payload.workflow``. This dispatcher keeps that single-row/stable-idempotency-key
    design working as more adapters gain real browser automation.
    """

    def __init__(
        self,
        greenhouse_handler: "GreenhouseBrowserReviewHandler",
        headhunter_handler: HeadHunterApplyHandler,
        linkedin_handler: LinkedInApplyHandler,
    ) -> None:
        self._handlers: dict[str, TaskHandler] = {
            "greenhouse_review": greenhouse_handler,
            "headhunter_apply": headhunter_handler,
            "linkedin_apply": linkedin_handler,
        }

    async def handle(self, claimed_task: ClaimedTask) -> ExecutionOutcome:
        workflow = str(claimed_task.payload.get("workflow") or "")
        handler = self._handlers.get(workflow)
        if handler is None:
            return ExecutionOutcome(TaskState.FAILED, f"unknown application workflow: {workflow!r}")
        return await handler.handle(claimed_task)
