from __future__ import annotations

from pathlib import Path

from app.browser.engine import BrowserActionResult, PlaywrightEngine
from app.domain.models import TaskState
from app.storage.task_repository import SqlTaskRepository


class ApplicationReviewWorkflow:
    """Runs the controlled, resumable browser path up to the human review boundary."""

    def __init__(
        self, task_repository: SqlTaskRepository, browser_engine: PlaywrightEngine
    ) -> None:
        self._task_repository = task_repository
        self._browser_engine = browser_engine

    async def run(
        self,
        *,
        idempotency_key: str,
        application_url: str,
        full_name: str,
        email: str,
        resume_path: Path,
    ) -> tuple[BrowserActionResult, ...]:
        task = self._task_repository.get_or_create(idempotency_key)
        if task.state is TaskState.PENDING:
            task.transition(TaskState.SCHEDULED, reason="review flow queued", worker="orchestrator")
        if task.state in {TaskState.SCHEDULED, TaskState.RETRY_SCHEDULED}:
            task.transition(TaskState.RUNNING, reason="browser worker claimed", worker="browser-1")
        self._task_repository.save(task)

        page = await self._browser_engine.new_page()
        navigation = await self._browser_engine.navigate(page, application_url)
        if not navigation.is_successful:
            task.transition(
                TaskState.RETRY_SCHEDULED,
                reason="navigation failed",
                worker="browser-1",
                evidence=(navigation.error_category or "unknown",),
            )
            self._task_repository.save(task)
            return (navigation,)

        form_actions = await self._browser_engine.fill_review_form(
            page,
            full_name=full_name,
            email=email,
            resume_path=resume_path,
        )
        actions = (navigation, *form_actions)
        failures = tuple(action for action in actions if not action.is_successful)
        if failures:
            task.transition(
                TaskState.FAILED,
                reason="deterministic form action failed",
                worker="browser-1",
                evidence=tuple(action.error_category or "unknown" for action in failures),
            )
        else:
            checkpoint = form_actions[-1]
            task.transition(
                TaskState.WAITING_FOR_USER,
                reason="application prepared for review",
                worker="browser-1",
                evidence=(checkpoint.screenshot_path or "screenshot unavailable",),
            )
        self._task_repository.save(task)
        return actions
