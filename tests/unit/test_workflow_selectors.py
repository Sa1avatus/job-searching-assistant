import pytest
from pydantic import ValidationError

from app.domain.workflow_selectors import WorkflowSelectorCandidate


@pytest.mark.parametrize(
    "kind",
    ["role", "label", "placeholder", "test_id", "id", "name", "css"],
)
def test_valid_workflow_selector_kind(kind: str) -> None:
    selector = WorkflowSelectorCandidate(
        kind=kind,
        value=f"  {kind}  ",
    )

    assert selector.kind == kind
    assert selector.value == kind


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "xpath", "value": "valid value"},
        {"kind": "role", "value": "   "},
        {"kind": "role", "value": "a" * 1_001},
        {"kind": "role", "value": "valid value", "extra_field": True},
    ],
)
def test_invalid_workflow_selector_payload(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WorkflowSelectorCandidate.model_validate(payload)
