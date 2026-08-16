from __future__ import annotations

from playwright.async_api import Page

from app.browser.workflow_wait_executor import execute_wait_workflow_step
from app.domain.workflow_assert import AssertWorkflowStep
from app.domain.workflow_selectors import WorkflowSelectorCandidate
from app.domain.workflow_wait import WaitWorkflowStep


async def execute_assert_workflow_step(
    page: Page, step: AssertWorkflowStep
) -> WorkflowSelectorCandidate | None:
    wait_step = WaitWorkflowStep(
        selector_candidates=step.selector_candidates,
        timeout_ms=step.timeout_ms,
        is_enabled=step.is_enabled,
    )

    if step.parameters.is_present:
        return await execute_wait_workflow_step(page, wait_step)

    try:
        await execute_wait_workflow_step(page, wait_step)
    except TimeoutError:
        return None

    raise AssertionError("A workflow selector unexpectedly became visible")
