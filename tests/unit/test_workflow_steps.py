import pytest

from app.domain.workflow_steps import (
    BROWSER_WORKFLOW_STEP_TYPES,
    PRIVILEGED_WORKFLOW_STEP_TYPES,
    WorkflowStatus,
    WorkflowStepType,
)


def test_step_values():
    assert [item.value for item in WorkflowStepType] == [
        "navigate",
        "fill",
        "upload",
        "select",
        "check",
        "click",
        "wait",
        "assert",
        "human_review",
        "submit",
    ]


def test_status_values():
    assert [item.value for item in WorkflowStatus] == [
        "draft",
        "testing",
        "active",
        "broken",
        "archived",
    ]


def test_set_expressions():
    assert (
        frozenset({WorkflowStepType.HUMAN_REVIEW, WorkflowStepType.SUBMIT})
        == PRIVILEGED_WORKFLOW_STEP_TYPES
    )
    assert (
        frozenset(
            {
                WorkflowStepType.NAVIGATE,
                WorkflowStepType.FILL,
                WorkflowStepType.UPLOAD,
                WorkflowStepType.SELECT,
                WorkflowStepType.CHECK,
                WorkflowStepType.CLICK,
                WorkflowStepType.WAIT,
                WorkflowStepType.ASSERT,
                WorkflowStepType.SUBMIT,
            }
        )
        == BROWSER_WORKFLOW_STEP_TYPES
    )
    assert WorkflowStepType.HUMAN_REVIEW not in BROWSER_WORKFLOW_STEP_TYPES


def test_unknown_values():
    with pytest.raises(ValueError):
        WorkflowStepType("javascript")
    with pytest.raises(ValueError):
        WorkflowStatus("unknown")
