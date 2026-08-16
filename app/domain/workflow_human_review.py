from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.domain.workflow_schemas import StrictWorkflowStepModel
from app.domain.workflow_steps import WorkflowStepType


class HumanReviewStepParameters(StrictWorkflowStepModel):
    checkpoint_key: str = Field(min_length=1, max_length=100)


class HumanReviewWorkflowStep(StrictWorkflowStepModel):
    action_type: Literal[WorkflowStepType.HUMAN_REVIEW] = WorkflowStepType.HUMAN_REVIEW
    parameters: HumanReviewStepParameters
    timeout_ms: int = Field(default=120_000, ge=1, le=120_000)
    is_enabled: bool = True
