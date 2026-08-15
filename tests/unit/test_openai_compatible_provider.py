from __future__ import annotations

import json

import httpx
import pytest

from app.llm.providers.openai_compatible import (
    OpenAICompatibleProvider,
    OpenAICompatibleResponseError,
    _is_response_format_error,
    simplify_json_schema_for_ollama,
)
from app.llm.router import ModelRequest, ModelTaskClass


def _request() -> ModelRequest:
    return ModelRequest(
        task_name="test",
        task_class=ModelTaskClass.LOW_COST,
        prompt="Return a result",
        max_cost_usd=0.01,
    )


@pytest.mark.asyncio
async def test_openai_compatible_provider_sends_chat_completion_request() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://models.example.test/v1/chat/completions")
        assert request.headers["Authorization"] == "Bearer sentinel-key"
        payload = json.loads(request.content)
        assert payload["model"] == "custom-model"
        assert payload["messages"][1] == {
            "role": "user",
            "content": "Return a result",
        }
        assert payload["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_response",
                "strict": True,
                "schema": {"type": "object"},
            },
        }
        assert (
            'Follow this JSON Schema exactly: {"type":"object"}'
            in payload["messages"][0]["content"]
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"result": "ok"}'}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            client,
            api_key=" sentinel-key ",
            model=" custom-model ",
            base_url="https://models.example.test/v1/",
        )
        request = _request()
        request = ModelRequest(
            task_name=request.task_name,
            task_class=request.task_class,
            prompt=request.prompt,
            max_cost_usd=request.max_cost_usd,
            response_schema={"type": "object"},
        )
        assert await provider.complete(request) == {"result": "ok"}


@pytest.mark.asyncio
async def test_ollama_provider_honors_model_override() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        # The per-task override replaces the provider's default model name.
        assert payload["model"] == "qwen3:1.5b"
        assert payload["think"] is False
        return httpx.Response(
            200,
            json={"message": {"content": '{"result": "ok"}'}, "done_reason": "stop"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            client,
            api_key="ollama",
            model="qwen3.5:4b",
            base_url="http://host.docker.internal:11434/v1",
        )
        request = ModelRequest(
            task_name="evaluate_evidence_entailment",
            task_class=ModelTaskClass.LOW_COST,
            prompt="Evaluate",
            max_cost_usd=0.03,
            model_override="qwen3:1.5b",
        )
        assert await provider.complete(request) == {"result": "ok"}


@pytest.mark.asyncio
async def test_openai_provider_honors_model_override() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3:1.5b"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"result": "ok"}'}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            client,
            api_key="key",
            model="qwen3.5:4b",
            base_url="https://models.example.test/v1/",
        )
        request = ModelRequest(
            task_name="evaluate_evidence_entailment",
            task_class=ModelTaskClass.LOW_COST,
            prompt="Evaluate",
            max_cost_usd=0.03,
            model_override="qwen3:1.5b",
        )
        assert await provider.complete(request) == {"result": "ok"}


@pytest.mark.asyncio
async def test_ollama_compatible_provider_disables_reasoning() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        # Ollama models use native /api/chat with think=false
        assert payload["think"] is False
        assert payload["options"]["num_predict"] == 16384
        return httpx.Response(
            200,
            json={"message": {"content": '{"result": "ok"}'}, "done_reason": "stop"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            client,
            api_key="ollama",
            model="qwen3:8b",
            base_url="http://host.docker.internal:11434/v1",
        )
        request = _request()
        request = ModelRequest(
            task_name=request.task_name,
            task_class=request.task_class,
            prompt=request.prompt,
            max_cost_usd=request.max_cost_usd,
            response_schema={"type": "object"},
        )
        assert await provider.complete(request) == {"result": "ok"}


def test_ollama_schema_simplification_inlines_definitions_and_drops_constraints() -> None:
    schema = {
        "$defs": {
            "Item": {
                "title": "Item",
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 100},
                },
                "required": ["name"],
                "additionalProperties": False,
            }
        },
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"$ref": "#/$defs/Item"},
                "maxItems": 200,
            }
        },
        "required": ["items"],
    }

    assert simplify_json_schema_for_ollama(schema) == {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
    }


def test_openai_compatible_provider_support_and_cost() -> None:
    provider = OpenAICompatibleProvider(
        httpx.AsyncClient(),
        api_key="key",
        model="model",
        base_url="http://localhost:11434/v1",
    )

    assert provider.supports(ModelTaskClass.LOW_COST)
    assert provider.supports(ModelTaskClass.STRONG_REASONING)
    assert not provider.supports(ModelTaskClass.EMBEDDING)
    assert provider.estimate_cost_usd(_request()) == 0.0


@pytest.mark.parametrize(
    ("api_key", "model", "base_url"),
    [
        (" ", "model", "https://example.test/v1"),
        ("key", " ", "https://example.test/v1"),
        ("key", "model", "ftp://example.test/v1"),
        ("key", "model", "https:///v1"),
        ("key", "model", "https://user:pass@example.test/v1"),
        ("key", "model", "https://example.test/v1?secret=value"),
        ("key", "model", "https://example.test/v1#fragment"),
    ],
)
def test_openai_compatible_provider_rejects_invalid_configuration(
    api_key: str, model: str, base_url: str
) -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(
            httpx.AsyncClient(), api_key=api_key, model=model, base_url=base_url
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]}),
    ],
)
async def test_openai_compatible_provider_rejects_invalid_responses(
    response: httpx.Response,
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
        provider = OpenAICompatibleProvider(
            client,
            api_key="unique-secret-sentinel",
            model="model",
            base_url="https://example.test/v1",
        )
        with pytest.raises(OpenAICompatibleResponseError) as captured:
            await provider.complete(_request())

    assert "unique-secret-sentinel" not in str(captured.value)


@pytest.mark.asyncio
async def test_openai_compatible_provider_redacts_key_from_http_error() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(401, text="rejected unique-secret-sentinel")
        )
    ) as client:
        provider = OpenAICompatibleProvider(
            client,
            api_key="unique-secret-sentinel",
            model="model",
            base_url="https://example.test/v1",
        )
        with pytest.raises(OpenAICompatibleResponseError) as captured:
            await provider.complete(_request())

    assert "unique-secret-sentinel" not in str(captured.value)
    assert "HTTP 401" in str(captured.value)


def test_is_response_format_error_detection() -> None:
    assert _is_response_format_error(
        '{"error": {"message": "only text and json_object response formats are supported"}}'
    )
    assert _is_response_format_error('{"error": {"message": "response_format is unsupported"}}')
    assert not _is_response_format_error('{"error": {"message": "invalid api key"}}')
    assert not _is_response_format_error("")


@pytest.mark.asyncio
async def test_openai_provider_falls_back_to_json_object_when_schema_rejected() -> None:
    calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if payload["response_format"]["type"] == "json_schema":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "only text and json_object response formats are supported",
                        "type": "invalid_request_error",
                    }
                },
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"result": "ok"}'}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            client,
            api_key="key",
            model="local-code-worker/auto",
            base_url="http://host.docker.internal:8765/v1",
        )
        request = ModelRequest(
            task_name="evaluate_evidence_entailment",
            task_class=ModelTaskClass.LOW_COST,
            prompt="Evaluate",
            max_cost_usd=0.03,
            response_schema={"type": "object"},
        )
        # First call: json_schema rejected → automatic retry with json_object.
        assert await provider.complete(request) == {"result": "ok"}
        assert [c["response_format"]["type"] for c in calls] == ["json_schema", "json_object"]
        # Subsequent calls go straight to json_object without a 400 round-trip.
        assert await provider.complete(request) == {"result": "ok"}
        assert calls[-1]["response_format"]["type"] == "json_object"
        assert len(calls) == 3


@pytest.mark.asyncio
async def test_openai_provider_does_not_fallback_on_unrelated_400() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "invalid api key"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            client,
            api_key="key",
            model="m",
            base_url="https://models.example.test/v1/",
        )
        request = ModelRequest(
            task_name="t",
            task_class=ModelTaskClass.LOW_COST,
            prompt="p",
            max_cost_usd=0.03,
            response_schema={"type": "object"},
        )
        with pytest.raises(OpenAICompatibleResponseError):
            await provider.complete(request)
