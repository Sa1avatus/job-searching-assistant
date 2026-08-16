from collections.abc import AsyncIterator

import httpx
from fastapi.testclient import TestClient

from app.api.main import app, reranker_http_client
from app.config import Settings, get_settings


def _client(
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TestClient:
    app.dependency_overrides[get_settings] = lambda: settings

    async def override_http_client() -> AsyncIterator[httpx.AsyncClient | None]:
        if transport is None:
            yield None
            return
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://reranker.test",
        ) as client:
            yield client

    app.dependency_overrides[reranker_http_client] = override_http_client
    return TestClient(app)


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def test_reranker_status_is_disabled_without_configuration() -> None:
    try:
        with _client(Settings(_env_file=None)) as client:
            response = client.get("/api/v1/admin/reranker/status")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "status": "disabled",
        "live": False,
        "ready": False,
        "degraded": False,
        "model": None,
        "model_revision": None,
        "device": None,
        "error_code": None,
    }


def test_reranker_status_uses_public_health_and_service_bearer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/live":
            assert "Authorization" not in request.headers
            return httpx.Response(200, json={"status": "live"})
        if request.url.path == "/health/ready":
            assert "Authorization" not in request.headers
            return httpx.Response(
                200,
                json={"status": "ready", "model_ready": True, "redis": "up", "error": None},
            )
        assert request.url.path == "/v1/models/current"
        assert request.headers["Authorization"] == "Bearer service-secret"
        return httpx.Response(
            200,
            json={
                "name": "BAAI/bge-reranker-v2-m3",
                "revision": "953dc6f",
                "device": "cpu",
                "ready": True,
                "max_length": 1024,
            },
        )

    settings = Settings(
        reranker_service_url="http://reranker.test",
        reranker_api_key="service-secret",
        _env_file=None,
    )
    try:
        with _client(settings, httpx.MockTransport(handler)) as client:
            response = client.get("/api/v1/admin/reranker/status")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["model_revision"] == "953dc6f"


def test_reranker_status_reports_not_ready_without_502() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/live":
            return httpx.Response(200, json={"status": "live"})
        return httpx.Response(503, json={"error": {"code": "model_not_ready"}})

    settings = Settings(
        reranker_service_url="http://reranker.test",
        reranker_api_key="service-secret",
        _env_file=None,
    )
    try:
        with _client(settings, httpx.MockTransport(handler)) as client:
            response = client.get("/api/v1/admin/reranker/status")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["error_code"] == "not_ready"


def test_reranker_status_reports_authentication_failure_without_response_body() -> None:
    secret = "service-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/live":
            return httpx.Response(200, json={"status": "live"})
        if request.url.path == "/health/ready":
            return httpx.Response(
                200,
                json={"status": "ready", "model_ready": True, "redis": "up", "error": None},
            )
        return httpx.Response(401, text=f"private {secret}")

    settings = Settings(
        reranker_service_url="http://reranker.test",
        reranker_api_key=secret,
        _env_file=None,
    )
    try:
        with _client(settings, httpx.MockTransport(handler)) as client:
            response = client.get("/api/v1/admin/reranker/status")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.json()["error_code"] == "authentication_error"
    assert secret not in response.text


def test_reranker_status_reports_transport_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private transport detail", request=request)

    settings = Settings(
        reranker_service_url="http://reranker.test",
        reranker_api_key="service-secret",
        _env_file=None,
    )
    try:
        with _client(settings, httpx.MockTransport(handler)) as client:
            response = client.get("/api/v1/admin/reranker/status")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["error_code"] == "transport_error"
    assert "private transport detail" not in response.text
