import pytest
from pydantic import ValidationError

from app.domain.workflow_upload import UploadWorkflowStep
from app.domain.workflow_steps import WorkflowStepType


def test_valid_upload_workflow_step() -> None:
    step = UploadWorkflowStep.model_validate(
        {
            "selector_candidates": [{"kind": "label", "value": " Resume "}],
            "parameters": {"file_value_key": " resume.file "},
        },
    )

    assert step.action_type is WorkflowStepType.UPLOAD
    assert step.selector_candidates[0].value == "Resume"
    assert step.parameters.file_value_key == "resume.file"
    assert step.timeout_ms == 10_000
    assert step.is_enabled is True


@pytest.mark.parametrize(
    "payload",
    [
        {
            "selector_candidates": [],
            "parameters": {"file_value_key": "resume.file"},
        },
        {
            "action_type": "fill",
            "selector_candidates": [{"kind": "label", "value": "Resume"}],
            "parameters": {"file_value_key": "resume.file"},
        },
        {
            "selector_candidates": [{"kind": "label", "value": "Resume"}],
            "parameters": {"file_value_key": "resume.file"},
            "timeout_ms": 0,
        },
        {
            "selector_candidates": [{"kind": "label", "value": "Resume"}],
            "parameters": {"file_value_key": "resume.file"},
            "timeout_ms": 120_001,
        },
    ],
)
def test_invalid_upload_workflow_step(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        UploadWorkflowStep.model_validate(payload)


@pytest.mark.parametrize("forbidden_field", ["path", "content"])
def test_upload_parameters_reject_literal_file_data(forbidden_field: str) -> None:
    parameters: dict[str, object] = {
        "file_value_key": "resume.file",
        forbidden_field: True,
    }
    with pytest.raises(ValidationError):
        UploadWorkflowStep.model_validate(
            {
                "selector_candidates": [{"kind": "label", "value": "Resume"}],
                "parameters": parameters,
            },
        )
