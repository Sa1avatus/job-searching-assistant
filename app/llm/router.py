from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeVar

import structlog
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

OutputSchema = TypeVar("OutputSchema", bound=BaseModel)


class ModelTaskClass(StrEnum):
    LOW_COST = "low_cost"
    STRONG_REASONING = "strong_reasoning"
    EMBEDDING = "embedding"


@dataclass(frozen=True, slots=True)
class ModelRequest:
    task_name: str
    task_class: ModelTaskClass
    prompt: str
    max_cost_usd: float
    timeout_seconds: float = 30


class ModelProvider(Protocol):
    name: str

    def supports(self, task_class: ModelTaskClass) -> bool: ...

    def estimate_cost_usd(self, request: ModelRequest) -> float: ...

    async def complete(self, request: ModelRequest) -> dict[str, object]: ...


class NoModelAvailableError(RuntimeError):
    pass


class ModelRouter:
    def __init__(self, providers: tuple[ModelProvider, ...]) -> None:
        self._providers = providers

    async def route(self, request: ModelRequest, output_schema: type[OutputSchema]) -> OutputSchema:
        if request.max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be positive")
        if request.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        provider_errors: list[str] = []
        for provider in self._providers:
            if not provider.supports(request.task_class):
                continue
            estimated_cost_usd = provider.estimate_cost_usd(request)
            if estimated_cost_usd < 0 or estimated_cost_usd > request.max_cost_usd:
                provider_errors.append(f"{provider.name}:budget_exceeded")
                continue
            try:
                payload = await asyncio.wait_for(
                    provider.complete(request), timeout=request.timeout_seconds
                )
                return output_schema.model_validate(payload)
            except Exception as error:
                logger.warning(
                    "llm_provider_failed",
                    provider=provider.name,
                    task_name=request.task_name,
                    error_type=type(error).__name__,
                    error=str(error),
                )
                provider_errors.append(f"{provider.name}:{type(error).__name__}: {error}")
        details = ", ".join(provider_errors) or "no compatible provider"
        raise NoModelAvailableError(details)
