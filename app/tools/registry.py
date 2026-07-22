from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from pydantic import BaseModel

ToolInput = TypeVar("ToolInput", bound=BaseModel)
ToolOutput = TypeVar("ToolOutput", bound=BaseModel)


class ToolPermissionError(PermissionError):
    pass


class ToolNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ToolContext:
    correlation_id: str
    granted_scopes: frozenset[str]


@dataclass(frozen=True, slots=True)
class ToolAuditEvent:
    tool_name: str
    correlation_id: str
    status: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    required_scope: str
    timeout_seconds: float
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[dict[str, object]]]


class ToolRegistry:
    def __init__(self) -> None:
        self._definition_by_name: dict[str, ToolDefinition] = {}
        self.audit_events: list[ToolAuditEvent] = []

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definition_by_name:
            raise ValueError(f"Tool already registered: {definition.name}")
        if definition.timeout_seconds <= 0:
            raise ValueError("Tool timeout must be positive")
        self._definition_by_name[definition.name] = definition

    async def invoke(
        self,
        tool_name: str,
        payload: dict[str, object],
        context: ToolContext,
    ) -> BaseModel:
        definition = self._definition_by_name.get(tool_name)
        if definition is None:
            raise ToolNotFoundError(tool_name)
        if definition.required_scope not in context.granted_scopes:
            self._audit(definition.name, context.correlation_id, "permission_denied")
            raise ToolPermissionError(definition.required_scope)
        validated_input = definition.input_schema.model_validate(payload)
        try:
            raw_output = await asyncio.wait_for(
                definition.handler(validated_input),
                timeout=definition.timeout_seconds,
            )
            validated_output = definition.output_schema.model_validate(raw_output)
        except TimeoutError:
            self._audit(definition.name, context.correlation_id, "timeout")
            raise
        except Exception:
            self._audit(definition.name, context.correlation_id, "failed")
            raise
        self._audit(definition.name, context.correlation_id, "completed")
        return validated_output

    def _audit(self, tool_name: str, correlation_id: str, status: str) -> None:
        self.audit_events.append(
            ToolAuditEvent(tool_name, correlation_id, status, datetime.now(UTC))
        )
