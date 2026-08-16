from __future__ import annotations

from playwright.async_api import Page

from app.browser.workflow_check_executor import execute_check_workflow_step
from app.browser.workflow_upload_executor import execute_upload_workflow_step
from app.domain.workflow_check import CheckWorkflowStep
from app.domain.workflow_upload import UploadWorkflowStep
from app.workflows.workflow_input_resolver import WorkflowExecutionInputResolver

TypedBrowserWorkflowStep = CheckWorkflowStep | UploadWorkflowStep


async def execute_typed_browser_workflow_step(
    page: Page,
    step: TypedBrowserWorkflowStep,
    input_resolver: WorkflowExecutionInputResolver,
) -> None:
    if isinstance(step, CheckWorkflowStep):
        value = input_resolver.resolve_boolean(step.parameters.value_key)
        await execute_check_workflow_step(page, step, value)
        return
    if isinstance(step, UploadWorkflowStep):
        file_path = input_resolver.resolve_file(step.parameters.file_value_key)
        await execute_upload_workflow_step(page, step, file_path)
        return
    raise TypeError("Unsupported typed browser workflow step")
