from __future__ import annotations

from playwright.async_api import Page

from app.domain.workflow_human_review import HumanReviewWorkflowStep
from app.domain.workflow_submit import SubmitWorkflowStep
from app.workflows.workflow_human_review_executor import (
    execute_human_review_workflow_step,
)
from app.workflows.workflow_submit_executor import execute_submit_workflow_step

PrivilegedWorkflowStep = HumanReviewWorkflowStep | SubmitWorkflowStep


async def execute_privileged_workflow_step(
    page: Page,
    step: PrivilegedWorkflowStep,
    *,
    is_submit_confirmed: bool,
) -> None:
    if isinstance(step, HumanReviewWorkflowStep):
        execute_human_review_workflow_step(step)
        return
    if isinstance(step, SubmitWorkflowStep):
        await execute_submit_workflow_step(
            page,
            step,
            is_confirmed=is_submit_confirmed,
        )
        return
    raise TypeError("Unsupported privileged workflow step")
