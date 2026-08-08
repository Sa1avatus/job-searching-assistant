from __future__ import annotations

from playwright.async_api import Page

from app.browser.workflow_assert_executor import execute_assert_workflow_step
from app.browser.workflow_click_executor import execute_click_workflow_step
from app.browser.workflow_navigate_executor import execute_navigate_workflow_step
from app.browser.workflow_wait_executor import execute_wait_workflow_step
from app.domain.workflow_assert import AssertWorkflowStep
from app.domain.workflow_click import ClickWorkflowStep
from app.domain.workflow_schemas import NavigateWorkflowStep
from app.domain.workflow_wait import WaitWorkflowStep

BasicBrowserWorkflowStep = (
    NavigateWorkflowStep | WaitWorkflowStep | AssertWorkflowStep | ClickWorkflowStep
)


async def execute_basic_browser_workflow_step(
    page: Page,
    step: BasicBrowserWorkflowStep,
    allowed_hosts: tuple[str, ...],
) -> None:
    if isinstance(step, NavigateWorkflowStep):
        await execute_navigate_workflow_step(page, step, allowed_hosts)
        return
    if isinstance(step, WaitWorkflowStep):
        await execute_wait_workflow_step(page, step)
        return
    if isinstance(step, AssertWorkflowStep):
        await execute_assert_workflow_step(page, step)
        return
    if isinstance(step, ClickWorkflowStep):
        await execute_click_workflow_step(page, step)
        return
    raise TypeError("Unsupported basic browser workflow step")
