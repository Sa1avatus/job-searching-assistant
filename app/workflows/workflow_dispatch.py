from __future__ import annotations

from playwright.async_api import Page

from app.domain.workflow_step_parser import WorkflowStep
from app.workflows.workflow_browser_dispatch import (
    BasicBrowserWorkflowStep,
    execute_basic_browser_workflow_step,
)
from app.workflows.workflow_input_resolver import WorkflowExecutionInputResolver
from app.workflows.workflow_privileged_dispatch import (
    PrivilegedWorkflowStep,
    execute_privileged_workflow_step,
)
from app.workflows.workflow_text_dispatch import (
    TextBrowserWorkflowStep,
    execute_text_browser_workflow_step,
)
from app.workflows.workflow_typed_dispatch import (
    TypedBrowserWorkflowStep,
    execute_typed_browser_workflow_step,
)


async def execute_workflow_step(
    page: Page,
    step: WorkflowStep,
    input_resolver: WorkflowExecutionInputResolver,
    *,
    allowed_hosts: tuple[str, ...],
    is_submit_confirmed: bool,
) -> None:
    if isinstance(step, BasicBrowserWorkflowStep):
        await execute_basic_browser_workflow_step(page, step, allowed_hosts)
        return
    if isinstance(step, TextBrowserWorkflowStep):
        await execute_text_browser_workflow_step(page, step, input_resolver)
        return
    if isinstance(step, TypedBrowserWorkflowStep):
        await execute_typed_browser_workflow_step(page, step, input_resolver)
        return
    if isinstance(step, PrivilegedWorkflowStep):
        await execute_privileged_workflow_step(
            page,
            step,
            is_submit_confirmed=is_submit_confirmed,
        )
        return
    raise TypeError("Unsupported workflow step")
