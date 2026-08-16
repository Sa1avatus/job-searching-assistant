from app.domain.workflow_click import ClickWorkflowStep
from app.domain.workflow_steps import WorkflowStepType


def test_valid_click_workflow_step() -> None:
    step = ClickWorkflowStep.model_validate(
        {
            "selector_candidates": [{"kind": "css", "value": "button[data-action='continue']"}],
        },
    )

    assert step.action_type is WorkflowStepType.CLICK
    assert step.selector_candidates[0].kind == "css"
    assert step.selector_candidates[0].value == "button[data-action='continue']"
    assert step.timeout_ms == 10_000
    assert step.is_enabled is True
