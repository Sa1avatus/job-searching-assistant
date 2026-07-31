import json

import httpx
import pytest

from app.llm.providers.anthropic import AnthropicMessagesProvider, AnthropicResponseError
from app.llm.router import ModelRequest, ModelTaskClass


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_estimate_cost_scales_with_prompt_length() -> None:
    provider = AnthropicMessagesProvider(httpx.AsyncClient(), api_key="key", model="claude-haiku")
    short_request = ModelRequest("t", ModelTaskClass.LOW_COST, "hi", max_cost_usd=1)
    long_request = ModelRequest("t", ModelTaskClass.LOW_COST, "hi " * 5000, max_cost_usd=1)
    assert provider.estimate_cost_usd(long_request) > provider.estimate_cost_usd(short_request)


def test_rejects_empty_api_key() -> None:
    with pytest.raises(ValueError):
        AnthropicMessagesProvider(httpx.AsyncClient(), api_key="   ", model="claude-haiku")


@pytest.mark.asyncio
async def test_complete_parses_json_text_block() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "test-key"
        body = json.loads(request.content)
        assert body["model"] == "claude-haiku"
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": '{"cover_letter_text": "Hello"}'}]},
        )

    async with _client(handler) as client:
        provider = AnthropicMessagesProvider(client, api_key="test-key", model="claude-haiku")
        result = await provider.complete(
            ModelRequest("t", ModelTaskClass.LOW_COST, "draft a letter", max_cost_usd=1)
        )
    assert result == {"cover_letter_text": "Hello"}


@pytest.mark.asyncio
async def test_complete_strips_markdown_code_fence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": '```json\n{"cover_letter_text": "Hi"}\n```'}]
            },
        )

    async with _client(handler) as client:
        provider = AnthropicMessagesProvider(client, api_key="k", model="m")
        result = await provider.complete(
            ModelRequest("t", ModelTaskClass.LOW_COST, "prompt", max_cost_usd=1)
        )
    assert result == {"cover_letter_text": "Hi"}


@pytest.mark.asyncio
async def test_complete_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    async with _client(handler) as client:
        provider = AnthropicMessagesProvider(client, api_key="k", model="m")
        with pytest.raises(AnthropicResponseError):
            await provider.complete(
                ModelRequest("t", ModelTaskClass.LOW_COST, "prompt", max_cost_usd=1)
            )


@pytest.mark.asyncio
async def test_complete_raises_on_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "text", "text": "not json"}]})

    async with _client(handler) as client:
        provider = AnthropicMessagesProvider(client, api_key="k", model="m")
        with pytest.raises(AnthropicResponseError):
            await provider.complete(
                ModelRequest("t", ModelTaskClass.LOW_COST, "prompt", max_cost_usd=1)
            )
