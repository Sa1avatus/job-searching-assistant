from __future__ import annotations

from playwright.async_api import Page

from app.browser.workflow_fill_executor import execute_fill_workflow_step
from app.browser.workflow_select_executor import execute_select_workflow_step
from app.domain.workflow_fill import FillWorkflowStep
from app.domain.workflow_select import SelectWorkflowStep
from app.workflows.workflow_input_resolver import WorkflowExecutionInputResolver

TextBrowserWorkflowStep = FillWorkflowStep | SelectWorkflowStep


async def execute_text_browser_workflow_step(
    page: Page,
    step: TextBrowserWorkflowStep,
    input_resolver: WorkflowExecutionInputResolver,
) -> None:
    if isinstance(step, FillWorkflowStep):
        value = input_resolver.resolve_text(step.parameters.value_key)
        await execute_fill_workflow_step(page, step, value)
        return
    if isinstance(step, SelectWorkflowStep):
        value = input_resolver.resolve_text(step.parameters.value_key)
        await execute_select_workflow_step(page, step, value)
        return
    raise TypeError("Unsupported text browser workflow step")
