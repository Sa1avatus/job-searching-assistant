from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.domain.workflow_schemas import StrictWorkflowStepModel
from app.domain.workflow_selectors import WorkflowSelectorCandidate
from app.domain.workflow_steps import WorkflowStepType


class WaitWorkflowStep(StrictWorkflowStepModel):
    action_type: Literal[WorkflowStepType.WAIT] = WorkflowStepType.WAIT
    selector_candidates: list[WorkflowSelectorCandidate] = Field(min_length=1, max_length=20)
    timeout_ms: int = Field(default=10_000, ge=1, le=120_000)
    is_enabled: bool = True
