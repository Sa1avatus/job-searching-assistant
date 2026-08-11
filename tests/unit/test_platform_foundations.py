import asyncio

import pytest
from pydantic import BaseModel

from adapters.job_boards.contracts import detect_ats
from adapters.job_boards.greenhouse import GreenhouseAdapter
from app.config import Settings
from app.domain.forms import FormField, FormFieldType
from app.llm.router import ModelRequest, ModelRouter, ModelTaskClass
from app.observability.metrics import MetricsRegistry


class RoutedOutput(BaseModel):
    decision: str


class FailingProvider:
    name = "failing"

    def supports(self, task_class: ModelTaskClass) -> bool:
        return True

    def estimate_cost_usd(self, request: ModelRequest) -> float:
        return 0.001

    async def complete(self, request: ModelRequest) -> dict[str, object]:
        raise TimeoutError


class WorkingProvider:
    name = "working"

    def supports(self, task_class: ModelTaskClass) -> bool:
        return task_class is ModelTaskClass.LOW_COST

    def estimate_cost_usd(self, request: ModelRequest) -> float:
        return 0.001

    async def complete(self, request: ModelRequest) -> dict[str, object]:
        return {"decision": "review"}


def test_ats_detection_uses_exact_hostname_boundary() -> None:
    assert detect_ats("https://boards.greenhouse.io/acme/jobs/1").adapter_name == "greenhouse"
    assert detect_ats("https://evilboards.greenhouse.io.example.test/1").adapter_name == "generic"


def test_greenhouse_adapter_rejects_lookalike_hostname() -> None:
    adapter = GreenhouseAdapter(browser_engine=None)  # type: ignore[arg-type]

    assert adapter.supports_url("https://boards.greenhouse.io/acme/jobs/1") is True
    assert adapter.supports_url("https://boards.greenhouse.io.example.test/jobs/1") is False


def test_form_field_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError):
        FormField("email", "Email", FormFieldType.TEXT, True, confidence=1.2)


def test_model_router_falls_back_and_validates_typed_output() -> None:
    router = ModelRouter((FailingProvider(), WorkingProvider()))
    request = ModelRequest("classify", ModelTaskClass.LOW_COST, "Classify", 0.01)

    output = asyncio.run(router.route(request, RoutedOutput))

    assert output.decision == "review"


def test_metrics_registry_renders_prometheus_counter() -> None:
    registry = MetricsRegistry()
    registry.increment("applications_started_total", 2)
    registry.set_gauge("queue_depth", 3)

    assert "applications_started_total 2" in registry.render_prometheus()
    assert "# TYPE queue_depth gauge\nqueue_depth 3" in registry.render_prometheus()


def test_production_configuration_requires_api_key() -> None:
    with pytest.raises(ValueError):
        Settings(environment="production", api_key=None, _env_file=None)


def test_production_configuration_accepts_api_key() -> None:
    settings = Settings(environment="production", api_key="test-secret", _env_file=None)

    assert settings.api_key is not None


def test_production_configuration_accepts_scoped_clients() -> None:
    settings = Settings(
        environment="production",
        api_clients_json='{"review-key":["review:read"]}',
        _env_file=None,
    )

    assert settings.api_clients() == {"review-key": frozenset({"review:read"})}


def test_api_client_configuration_rejects_invalid_scope_shape() -> None:
    with pytest.raises(ValueError):
        Settings(api_clients_json='{"broken":"review:read"}', _env_file=None)


def test_embedding_service_uses_explicit_url_before_legacy_alias() -> None:
    explicit = Settings(
        matching_model_service_url="http://legacy.test",
        embedding_service_url="http://embedding.test",
        _env_file=None,
    )
    legacy = Settings(
        matching_model_service_url="http://legacy.test",
        embedding_service_url="",
        _env_file=None,
    )

    assert explicit.resolved_embedding_service_url == "http://embedding.test"
    assert legacy.resolved_embedding_service_url == "http://legacy.test"


def test_empty_reranker_configuration_is_disabled() -> None:
    settings = Settings(
        reranker_service_url="",
        reranker_api_key="",
        _env_file=None,
    )

    assert settings.reranker_service_url is None
    assert settings.reranker_api_key is None
