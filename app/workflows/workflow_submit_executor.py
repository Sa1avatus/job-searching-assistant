from __future__ import annotations

from playwright.async_api import Page

from app.browser.workflow_click_executor import execute_click_workflow_step
from app.domain.workflow_click import ClickWorkflowStep
from app.domain.workflow_selectors import WorkflowSelectorCandidate
from app.domain.workflow_submit import SubmitWorkflowStep


class WorkflowSubmitConfirmationRequired(RuntimeError):
    pass


async def execute_submit_workflow_step(
    page: Page,
    step: SubmitWorkflowStep,
    *,
    is_confirmed: bool,
) -> WorkflowSelectorCandidate:
    if not is_confirmed:
        raise WorkflowSubmitConfirmationRequired("Workflow submit requires explicit confirmation")

    click_step = ClickWorkflowStep(
        selector_candidates=step.selector_candidates,
        timeout_ms=step.timeout_ms,
        is_enabled=step.is_enabled,
    )
    return await execute_click_workflow_step(page, click_step)
