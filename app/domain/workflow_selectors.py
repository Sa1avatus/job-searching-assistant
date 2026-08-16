from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type WorkflowSelectorKind = Literal["role", "label", "placeholder", "test_id", "id", "name", "css"]


class WorkflowSelectorCandidate(BaseModel):
    kind: WorkflowSelectorKind
    value: str = Field(min_length=1, max_length=1_000)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
