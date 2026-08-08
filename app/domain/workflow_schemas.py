from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.workflow_steps import WorkflowStepType


class StrictWorkflowStepModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NavigateStepParameters(StrictWorkflowStepModel):
    url: str = Field(min_length=1, max_length=2_000)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        normalized_value = value.strip()
        parsed_url = urlsplit(normalized_value)
        if parsed_url.scheme != "https":
            raise ValueError("Navigate URL must use HTTPS")
        if not parsed_url.hostname:
            raise ValueError("Navigate URL must include a hostname")
        if parsed_url.username is not None or parsed_url.password is not None:
            raise ValueError("Navigate URL must not include credentials")
        return normalized_value


class NavigateWorkflowStep(StrictWorkflowStepModel):
    action_type: Literal[WorkflowStepType.NAVIGATE] = WorkflowStepType.NAVIGATE
    parameters: NavigateStepParameters
    timeout_ms: int = Field(default=10_000, ge=1, le=120_000)
    is_enabled: bool = True
