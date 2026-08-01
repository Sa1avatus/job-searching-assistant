import json

import httpx
import pytest

from app.llm.providers.gemini import (
    GeminiProvider,
    GeminiResponseError,
    _gemini_response_schema,
)
from app.llm.router import ModelRequest, ModelTaskClass


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_estimate_cost_scales_with_prompt_length() -> None:
    provider = GeminiProvider(httpx.AsyncClient(), api_key="key", model="gemini-2.5-flash")
    short_request = ModelRequest("t", ModelTaskClass.LOW_COST, "hi", max_cost_usd=1)
    long_request = ModelRequest("t", ModelTaskClass.LOW_COST, "hi " * 5000, max_cost_usd=1)
    assert provider.estimate_cost_usd(long_request) > provider.estimate_cost_usd(short_request)


def test_rejects_empty_api_key() -> None:
    with pytest.raises(ValueError):
        GeminiProvider(httpx.AsyncClient(), api_key="   ", model="gemini-2.5-flash")


def test_gemini_response_schema_resolves_pydantic_definitions() -> None:
    schema = {
        "$defs": {
            "Item": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"name": {"type": "string", "maxLength": 20}},
                "required": ["name"],
            }
        },
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "items": {"type": "array", "items": {"$ref": "#/$defs/Item"}},
            "note": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
            },
        },
    }

    assert _gemini_response_schema(schema) == {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            "note": {"type": "string"},
        },
    }


@pytest.mark.asyncio
async def test_complete_parses_json_text_part() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "test-key"
        assert "key" not in request.url.params
        body = json.loads(request.content)
        assert body["contents"][0]["parts"][0]["text"] == "draft a letter"
        assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
        assert body["generationConfig"]["responseJsonSchema"] == {
            "type": "object",
            "properties": {"cover_letter_text": {"type": "string"}},
        }
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": '{"cover_letter_text": "Hello"}'}]},
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    async with _client(handler) as client:
        provider = GeminiProvider(client, api_key="test-key", model="gemini-2.5-flash")
        result = await provider.complete(
            ModelRequest(
                "t",
                ModelTaskClass.LOW_COST,
                "draft a letter",
                max_cost_usd=1,
                response_schema={
                    "type": "object",
                    "properties": {"cover_letter_text": {"type": "string"}},
                },
            )
        )
    assert result == {"cover_letter_text": "Hello"}


@pytest.mark.asyncio
async def test_gemini_3_uses_thinking_level_and_retries_without_it_on_http_400() -> None:
    request_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_bodies.append(json.loads(request.content))
        if len(request_bodies) == 1:
            return httpx.Response(
                400,
                json={"error": {"status": "INVALID_ARGUMENT"}},
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": '{"skills": ["Python"]}'}]},
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    async with _client(handler) as client:
        provider = GeminiProvider(
            client,
            api_key="test-key",
            model="gemini-3.5-flash-lite",
        )
        result = await provider.complete(
            ModelRequest("extract", ModelTaskClass.LOW_COST, "resume", max_cost_usd=1)
        )

    first_config = request_bodies[0]["generationConfig"]
    second_config = request_bodies[1]["generationConfig"]
    assert isinstance(first_config, dict)
    assert isinstance(second_config, dict)
    assert first_config["thinkingConfig"] == {"thinkingLevel": "low"}
    assert "thinkingConfig" not in second_config
    assert result == {"skills": ["Python"]}


@pytest.mark.asyncio
async def test_complete_strips_markdown_code_fence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": '```json\n{"cover_letter_text": "Hi"}\n```'}]
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    async with _client(handler) as client:
        provider = GeminiProvider(client, api_key="k", model="m")
        result = await provider.complete(
            ModelRequest("t", ModelTaskClass.LOW_COST, "prompt", max_cost_usd=1)
        )
    assert result == {"cover_letter_text": "Hi"}


@pytest.mark.asyncio
async def test_complete_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    async with _client(handler) as client:
        provider = GeminiProvider(client, api_key="k", model="m")
        with pytest.raises(GeminiResponseError):
            await provider.complete(
                ModelRequest("t", ModelTaskClass.LOW_COST, "prompt", max_cost_usd=1)
            )


@pytest.mark.asyncio
async def test_complete_raises_on_blocked_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
        )

    async with _client(handler) as client:
        provider = GeminiProvider(client, api_key="k", model="m")
        with pytest.raises(GeminiResponseError, match="SAFETY"):
            await provider.complete(
                ModelRequest("t", ModelTaskClass.LOW_COST, "prompt", max_cost_usd=1)
            )


@pytest.mark.asyncio
async def test_complete_raises_on_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "not json"}]}, "finishReason": "STOP"}
                ]
            },
        )

    async with _client(handler) as client:
        provider = GeminiProvider(client, api_key="k", model="m")
        with pytest.raises(GeminiResponseError):
            await provider.complete(
                ModelRequest("t", ModelTaskClass.LOW_COST, "prompt", max_cost_usd=1)
            )
