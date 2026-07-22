import asyncio

import pytest
from pydantic import BaseModel

from app.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolPermissionError,
    ToolRegistry,
)


class EchoInput(BaseModel):
    message: str


class EchoOutput(BaseModel):
    echoed: str


async def echo_handler(tool_input: BaseModel) -> dict[str, object]:
    validated_input = EchoInput.model_validate(tool_input)
    return {"echoed": validated_input.message}


def test_tool_registry_validates_scope_input_output_and_audits() -> None:
    async def run_test() -> None:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="echo",
                required_scope="tools:echo",
                timeout_seconds=1,
                input_schema=EchoInput,
                output_schema=EchoOutput,
                handler=echo_handler,
            )
        )
        context = ToolContext("correlation-1", frozenset({"tools:echo"}))

        output = await registry.invoke("echo", {"message": "hello"}, context)

        assert output == EchoOutput(echoed="hello")
        assert registry.audit_events[-1].status == "completed"

    asyncio.run(run_test())


def test_tool_registry_denies_missing_scope_without_invoking_handler() -> None:
    async def run_test() -> None:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="echo",
                required_scope="tools:echo",
                timeout_seconds=1,
                input_schema=EchoInput,
                output_schema=EchoOutput,
                handler=echo_handler,
            )
        )
        context = ToolContext("correlation-2", frozenset())

        with pytest.raises(ToolPermissionError):
            await registry.invoke("echo", {"message": "hello"}, context)
        assert registry.audit_events[-1].status == "permission_denied"

    asyncio.run(run_test())
