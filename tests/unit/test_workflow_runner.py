from typing import cast
from unittest.mock import AsyncMock, call

import pytest
from playwright.async_api import Page

from app.domain.workflow_execution import WorkflowStepExecutionStatus
from app.domain.workflow_schemas import NavigateWorkflowStep
from app.domain.workflow_steps import WorkflowStepType
from app.workflows import workflow_runner
from app.workflows.workflow_input_resolver import WorkflowExecutionInputResolver


@pytest.mark.asyncio
async def test_execute_workflow_steps_skips_disabled_steps_and_records_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = AsyncMock()
    monkeypatch.setattr(workflow_runner, "execute_workflow_step", dispatcher)
    steps = [
        NavigateWorkflowStep(
            parameters={"url": "https://jobs.example/skip"},
            is_enabled=False,
        ),
        NavigateWorkflowStep(parameters={"url": "https://jobs.example/one"}),
        NavigateWorkflowStep(parameters={"url": "https://jobs.example/two"}),
    ]
    page = cast(Page, object())
    input_resolver = cast(WorkflowExecutionInputResolver, object())

    outcomes = await workflow_runner.execute_workflow_steps(
        page,
        steps,
        input_resolver,
        allowed_hosts=("jobs.example",),
        is_submit_confirmed=False,
    )

    assert dispatcher.await_count == 2
    assert dispatcher.await_args_list == [
        call(
            page,
            steps[1],
            input_resolver,
            allowed_hosts=("jobs.example",),
            is_submit_confirmed=False,
        ),
        call(
            page,
            steps[2],
            input_resolver,
            allowed_hosts=("jobs.example",),
            is_submit_confirmed=False,
        ),
    ]
    assert [(outcome.position, outcome.action_type, outcome.status) for outcome in outcomes] == [
        (0, WorkflowStepType.NAVIGATE, WorkflowStepExecutionStatus.SKIPPED),
        (1, WorkflowStepType.NAVIGATE, WorkflowStepExecutionStatus.SUCCEEDED),
        (2, WorkflowStepType.NAVIGATE, WorkflowStepExecutionStatus.SUCCEEDED),
    ]


@pytest.mark.asyncio
async def test_execute_workflow_steps_stops_after_dispatch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("dispatch failed")
    dispatcher = AsyncMock(side_effect=[None, failure])
    monkeypatch.setattr(workflow_runner, "execute_workflow_step", dispatcher)
    steps = [
        NavigateWorkflowStep(parameters={"url": "https://jobs.example/one"}),
        NavigateWorkflowStep(parameters={"url": "https://jobs.example/two"}),
        NavigateWorkflowStep(parameters={"url": "https://jobs.example/three"}),
    ]
    page = cast(Page, object())
    input_resolver = cast(WorkflowExecutionInputResolver, object())

    with pytest.raises(RuntimeError, match="dispatch failed") as exc_info:
        await workflow_runner.execute_workflow_steps(
            page,
            steps,
            input_resolver,
            allowed_hosts=("jobs.example",),
            is_submit_confirmed=False,
        )

    assert exc_info.value is failure
    assert dispatcher.await_count == 2
    assert dispatcher.await_args_list == [
        call(
            page,
            steps[0],
            input_resolver,
            allowed_hosts=("jobs.example",),
            is_submit_confirmed=False,
        ),
        call(
            page,
            steps[1],
            input_resolver,
            allowed_hosts=("jobs.example",),
            is_submit_confirmed=False,
        ),
    ]
