import pytest
from pydantic import ValidationError

from app.domain.workflow_select import SelectWorkflowStep
from app.domain.workflow_steps import WorkflowStepType


def test_valid_select_workflow_step() -> None:
    step = SelectWorkflowStep.model_validate(
        {
            "selector_candidates": [{"kind": "label", "value": " Country "}],
            "parameters": {"value_key": " location.country "},
        },
    )

    assert step.action_type is WorkflowStepType.SELECT
    assert step.selector_candidates[0].value == "Country"
    assert step.parameters.value_key == "location.country"
    assert step.timeout_ms == 10_000
    assert step.is_enabled is True


@pytest.mark.parametrize(
    "payload",
    [
        {
            "selector_candidates": [],
            "parameters": {"value_key": "location.country"},
        },
        {
            "action_type": "fill",
            "selector_candidates": [{"kind": "label", "value": "Country"}],
            "parameters": {"value_key": "location.country"},
        },
        {
            "selector_candidates": [{"kind": "label", "value": "Country"}],
            "parameters": {"value_key": "location.country"},
            "timeout_ms": 0,
        },
        {
            "selector_candidates": [{"kind": "label", "value": "Country"}],
            "parameters": {"value_key": "location.country"},
            "timeout_ms": 120_001,
        },
    ],
)
def test_invalid_select_workflow_step(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SelectWorkflowStep.model_validate(payload)


def test_select_parameters_reject_literal_value() -> None:
    with pytest.raises(ValidationError):
        SelectWorkflowStep.model_validate(
            {
                "selector_candidates": [{"kind": "label", "value": "Country"}],
                "parameters": {
                    "value_key": "location.country",
                    "value": True,
                },
            },
        )
