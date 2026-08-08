from enum import StrEnum
from typing import Literal

from pydantic import Field

from app.domain.workflow_schemas import StrictWorkflowStepModel
from app.domain.workflow_steps import WorkflowStepType


class WorkflowStepExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"


class WorkflowStepExecutionOutcome(StrictWorkflowStepModel):
    position: int = Field(ge=0)
    action_type: WorkflowStepType
    status: Literal[
        WorkflowStepExecutionStatus.SUCCEEDED,
        WorkflowStepExecutionStatus.SKIPPED,
    ]
