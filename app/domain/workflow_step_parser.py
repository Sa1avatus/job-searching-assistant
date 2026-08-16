from __future__ import annotations

from typing import Annotated

from pydantic import Field, TypeAdapter

from app.domain.workflow_assert import AssertWorkflowStep
from app.domain.workflow_check import CheckWorkflowStep
from app.domain.workflow_click import ClickWorkflowStep
from app.domain.workflow_fill import FillWorkflowStep
from app.domain.workflow_human_review import HumanReviewWorkflowStep
from app.domain.workflow_schemas import NavigateWorkflowStep
from app.domain.workflow_select import SelectWorkflowStep
from app.domain.workflow_submit import SubmitWorkflowStep
from app.domain.workflow_upload import UploadWorkflowStep
from app.domain.workflow_wait import WaitWorkflowStep

WorkflowStep = Annotated[
    (
        NavigateWorkflowStep
        | FillWorkflowStep
        | UploadWorkflowStep
        | SelectWorkflowStep
        | CheckWorkflowStep
        | ClickWorkflowStep
        | WaitWorkflowStep
        | AssertWorkflowStep
        | HumanReviewWorkflowStep
        | SubmitWorkflowStep
    ),
    Field(discriminator="action_type"),
]

_WORKFLOW_STEP_ADAPTER: TypeAdapter[WorkflowStep] = TypeAdapter(WorkflowStep)


def parse_workflow_step(payload: object) -> WorkflowStep:
    return _WORKFLOW_STEP_ADAPTER.validate_python(payload)
