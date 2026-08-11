from __future__ import annotations

from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError


class _StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ReadyResponse(_StrictResponse):
    status: str
    model_ready: bool
    redis: str
    error: str | None


class _CurrentModelResponse(_StrictResponse):
    name: str
    revision: str
    device: str
    ready: bool
    max_length: int


@dataclass(frozen=True, slots=True)
class RerankerStatus:
    configured: bool
    status: str
    live: bool
    ready: bool
    degraded: bool
    model: str | None = None
    model_revision: str | None = None
    device: str | None = None
    error_code: str | None = None


class RerankerStatusProbe:
    async def probe(
        self,
        http_client: httpx.AsyncClient | None,
        *,
        api_key: str | None,
        partially_configured: bool = False,
    ) -> RerankerStatus:
        if http_client is None or api_key is None:
            return RerankerStatus(
                configured=False,
                status="disabled",
                live=False,
                ready=False,
                degraded=partially_configured,
                error_code="incomplete_configuration" if partially_configured else None,
            )
        try:
            live_response = await http_client.get("/health/live")
            if live_response.status_code >= 400:
                return self._http_failure(live_response.status_code)
            ready_response = await http_client.get("/health/ready")
            if ready_response.status_code == 503:
                return self._not_ready()
            if ready_response.status_code >= 400:
                return self._http_failure(ready_response.status_code, live=True)
            ready = _ReadyResponse.model_validate(ready_response.json())
            if not ready.model_ready:
                return self._not_ready()
            model_response = await http_client.get(
                "/v1/models/current",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if model_response.status_code >= 400:
                return self._http_failure(model_response.status_code, live=True, ready=True)
            model = _CurrentModelResponse.model_validate(model_response.json())
        except httpx.TimeoutException:
            return self._failure("timeout")
        except httpx.RequestError:
            return self._failure("transport_error")
        except (ValueError, ValidationError):
            return self._failure("contract_mismatch")
        return RerankerStatus(
            configured=True,
            status="ready" if model.ready else "degraded",
            live=True,
            ready=model.ready,
            degraded=not model.ready,
            model=model.name,
            model_revision=model.revision,
            device=model.device,
            error_code=None if model.ready else "not_ready",
        )

    @staticmethod
    def _not_ready() -> RerankerStatus:
        return RerankerStatus(
            configured=True,
            status="degraded",
            live=True,
            ready=False,
            degraded=True,
            error_code="not_ready",
        )

    @staticmethod
    def _failure(error_code: str) -> RerankerStatus:
        return RerankerStatus(
            configured=True,
            status="unavailable",
            live=False,
            ready=False,
            degraded=True,
            error_code=error_code,
        )

    @classmethod
    def _http_failure(
        cls,
        status_code: int,
        *,
        live: bool = False,
        ready: bool = False,
    ) -> RerankerStatus:
        error_code = {
            401: "authentication_error",
            403: "authentication_error",
            422: "contract_mismatch",
            429: "rate_limited",
            503: "not_ready",
        }.get(status_code, "service_error")
        return RerankerStatus(
            configured=True,
            status="degraded" if live else "unavailable",
            live=live,
            ready=ready,
            degraded=True,
            error_code=error_code,
        )
