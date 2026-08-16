from __future__ import annotations

from playwright.async_api import Page

from app.domain.workflow_execution import (
    WorkflowStepExecutionOutcome,
    WorkflowStepExecutionStatus,
)
from app.domain.workflow_step_parser import WorkflowStep
from app.workflows.workflow_dispatch import execute_workflow_step
from app.workflows.workflow_input_resolver import WorkflowExecutionInputResolver


async def execute_workflow_steps(
    page: Page,
    steps: list[WorkflowStep],
    input_resolver: WorkflowExecutionInputResolver,
    *,
    allowed_hosts: tuple[str, ...],
    is_submit_confirmed: bool,
) -> list[WorkflowStepExecutionOutcome]:
    outcomes: list[WorkflowStepExecutionOutcome] = []
    for position, step in enumerate(steps):
        if not step.is_enabled:
            outcomes.append(
                WorkflowStepExecutionOutcome(
                    position=position,
                    action_type=step.action_type,
                    status=WorkflowStepExecutionStatus.SKIPPED,
                )
            )
            continue
        await execute_workflow_step(
            page,
            step,
            input_resolver,
            allowed_hosts=allowed_hosts,
            is_submit_confirmed=is_submit_confirmed,
        )
        outcomes.append(
            WorkflowStepExecutionOutcome(
                position=position,
                action_type=step.action_type,
                status=WorkflowStepExecutionStatus.SUCCEEDED,
            )
        )
    return outcomes
