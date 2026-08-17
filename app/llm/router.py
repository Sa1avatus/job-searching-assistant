from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol, TypeVar

import httpx
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
    response_schema: dict[str, object] | None = None
    # Per-task inference bounds. Providers apply provider-appropriate defaults when unset.
    # Keep entailment/decompose small: their outputs are tiny JSON objects, and oversized
    # context/prediction budgets dominate local-CPU inference latency.
    max_output_tokens: int | None = None
    context_size: int | None = None
    # Optional per-request model name that overrides the provider's default model
    # (task-specific routing, e.g. a small local model for entailment).
    model_override: str | None = None


class ModelProvider(Protocol):
    name: str

    def supports(self, task_class: ModelTaskClass) -> bool: ...

    def estimate_cost_usd(self, request: ModelRequest) -> float: ...

    async def complete(self, request: ModelRequest) -> dict[str, object]: ...


class NoModelAvailableError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMTimeoutError(TimeoutError):
    """LLM request timed out."""


class LLMTruncatedOutputError(RuntimeError):
    """LLM output was truncated (finish_reason=length or abort)."""

    def __init__(self, message: str, *, finish_reason: str | None = None) -> None:
        super().__init__(message)
        self.finish_reason = finish_reason


class LLMInvalidJSONError(RuntimeError):
    """LLM returned content that is not valid JSON."""


def _is_retryable_provider_error(error: Exception) -> bool:
    if isinstance(error, TimeoutError | ConnectionError | httpx.TimeoutException):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code == 429 or error.response.status_code >= 500
    return isinstance(error, httpx.NetworkError | httpx.RemoteProtocolError)


class ModelRouter:
    def __init__(self, providers: tuple[ModelProvider, ...]) -> None:
        self._providers = providers

    async def route(self, request: ModelRequest, output_schema: type[OutputSchema]) -> OutputSchema:
        if request.max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be positive")
        if request.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        provider_errors: list[str] = []
        retryable_results: list[bool] = []
        for provider in self._providers:
            if not provider.supports(request.task_class):
                continue
            estimated_cost_usd = provider.estimate_cost_usd(request)
            if estimated_cost_usd < 0 or estimated_cost_usd > request.max_cost_usd:
                provider_errors.append(f"{provider.name}:budget_exceeded")
                retryable_results.append(False)
                continue
            try:
                import time as _time

                start = _time.monotonic()
                provider_request = replace(
                    request,
                    response_schema=output_schema.model_json_schema(),
                )
                payload = await asyncio.wait_for(
                    provider.complete(provider_request), timeout=request.timeout_seconds
                )
                duration_s = round(_time.monotonic() - start, 3)
                logger.debug(
                    "llm_request",
                    provider=provider.name,
                    task_name=request.task_name,
                    task_class=request.task_class.value,
                    duration_s=duration_s,
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
                # Let truncated/invalid JSON errors propagate as-is —
                # these are NOT "model unavailable" errors.
                if isinstance(
                    error,
                    LLMTruncatedOutputError | LLMInvalidJSONError | LLMTimeoutError,
                ):
                    raise
                provider_errors.append(f"{provider.name}:{type(error).__name__}: {error}")
                retryable_results.append(_is_retryable_provider_error(error))
        details = ", ".join(provider_errors) or "no compatible provider"
        raise NoModelAvailableError(
            details,
            retryable=bool(retryable_results) and all(retryable_results),
        )
