"""Anthropic Messages API provider for app/llm/router.py.

The user supplies their own Anthropic API key (APP_ANTHROPIC_API_KEY) and is billed directly by
Anthropic; this module performs no proxying, caching, or markup. It only ever sends the prompt
built by the caller (see app/services/materials_generation.py), which is instructed to draft
text strictly from the candidate's own verified profile facts.
"""

from __future__ import annotations

import json

import httpx

from app.llm.router import ModelProvider, ModelRequest, ModelTaskClass

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
# Rough, conservative per-token pricing used only to enforce ModelRequest.max_cost_usd before
# spending real money; not billing-accurate. ~4 chars/token estimate, output capped at 1024 tokens.
_INPUT_USD_PER_TOKEN = 1.00 / 1_000_000
_OUTPUT_USD_PER_TOKEN = 5.00 / 1_000_000
_MAX_OUTPUT_TOKENS = 1024


class AnthropicResponseError(RuntimeError):
    """The API returned an error, an unexpected shape, or non-JSON content."""


class AnthropicMessagesProvider(ModelProvider):
    name = "anthropic"

    def __init__(self, http_client: httpx.AsyncClient, *, api_key: str, model: str) -> None:
        if not api_key.strip():
            raise ValueError("Anthropic API key must not be empty")
        self._http_client = http_client
        self._api_key = api_key
        self._model = model

    def supports(self, task_class: ModelTaskClass) -> bool:
        return task_class in {ModelTaskClass.LOW_COST, ModelTaskClass.STRONG_REASONING}

    def estimate_cost_usd(self, request: ModelRequest) -> float:
        estimated_input_tokens = max(1, len(request.prompt) // 4)
        return (
            estimated_input_tokens * _INPUT_USD_PER_TOKEN
            + _MAX_OUTPUT_TOKENS * _OUTPUT_USD_PER_TOKEN
        )

    async def complete(self, request: ModelRequest) -> dict[str, object]:
        response = await self._http_client.post(
            _API_URL,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self._model,
                "max_tokens": _MAX_OUTPUT_TOKENS,
                "system": (
                    "You output only a single valid JSON object and nothing else: no preamble, "
                    "no markdown code fences, no trailing commentary."
                ),
                "messages": [{"role": "user", "content": request.prompt}],
            },
        )
        if response.status_code >= 400:
            raise AnthropicResponseError(
                f"Anthropic API returned HTTP {response.status_code}: {response.text[:500]}"
            )
        payload = response.json()
        blocks = payload.get("content") or []
        text = "".join(
            block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text"
        )
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[4:] if text.lower().startswith("json") else text
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise AnthropicResponseError(f"Anthropic response was not valid JSON: {error}") from error
        if not isinstance(parsed, dict):
            raise AnthropicResponseError("Anthropic response JSON was not an object")
        return parsed
