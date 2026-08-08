import pytest
from pydantic import ValidationError

from app.domain.workflow_schemas import (
    NavigateStepParameters,
    NavigateWorkflowStep,
)
from app.domain.workflow_steps import WorkflowStepType


def test_valid_navigate_workflow_step() -> None:
    step = NavigateWorkflowStep(
        parameters=NavigateStepParameters(
            url="   https://example.com/path?query=value   ",
        ),
    )

    assert step.action_type is WorkflowStepType.NAVIGATE
    assert step.parameters.url == "https://example.com/path?query=value"
    assert step.timeout_ms == 10_000
    assert step.is_enabled is True


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https:///path",
        "https://user:pass@example.com",
    ],
)
def test_invalid_navigate_url(url: str) -> None:
    with pytest.raises(ValidationError):
        NavigateStepParameters(url=url)


@pytest.mark.parametrize("timeout_ms", [0, 120_001])
def test_invalid_navigate_timeout(timeout_ms: int) -> None:
    with pytest.raises(ValidationError):
        NavigateWorkflowStep(
            parameters=NavigateStepParameters(url="https://example.com"),
            timeout_ms=timeout_ms,
        )


def test_navigate_step_rejects_unexpected_field() -> None:
    with pytest.raises(ValidationError):
        NavigateWorkflowStep(
            parameters=NavigateStepParameters(url="https://example.com"),
            unexpected_field=True,
        )
