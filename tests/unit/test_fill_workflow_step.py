import pytest
from pydantic import ValidationError

from app.domain.workflow_fill import FillWorkflowStep
from app.domain.workflow_steps import WorkflowStepType


def test_valid_fill_workflow_step() -> None:
    step = FillWorkflowStep.model_validate(
        {
            "selector_candidates": [{"kind": "label", "value": " Email "}],
            "parameters": {"value_key": " contact.email "},
        },
    )

    assert step.action_type is WorkflowStepType.FILL
    assert step.selector_candidates[0].value == "Email"
    assert step.parameters.value_key == "contact.email"
    assert step.timeout_ms == 10_000
    assert step.is_enabled is True


@pytest.mark.parametrize(
    "payload",
    [
        {
            "selector_candidates": [],
            "parameters": {"value_key": "contact.email"},
        },
        {
            "selector_candidates": [{"kind": "id", "value": str(index)} for index in range(21)],
            "parameters": {"value_key": "contact.email"},
        },
        {
            "action_type": "click",
            "selector_candidates": [{"kind": "label", "value": "Email"}],
            "parameters": {"value_key": "contact.email"},
        },
        {
            "selector_candidates": [{"kind": "label", "value": "Email"}],
            "parameters": {"value_key": "contact.email"},
            "timeout_ms": 0,
        },
        {
            "selector_candidates": [{"kind": "label", "value": "Email"}],
            "parameters": {"value_key": "contact.email"},
            "timeout_ms": 120_001,
        },
    ],
)
def test_invalid_fill_workflow_step(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FillWorkflowStep.model_validate(payload)


def test_fill_parameters_reject_literal_value() -> None:
    with pytest.raises(ValidationError):
        FillWorkflowStep.model_validate(
            {
                "selector_candidates": [{"kind": "label", "value": "Email"}],
                "parameters": {
                    "value_key": "contact.email",
                    "value": "literal personal value",
                },
            },
        )
