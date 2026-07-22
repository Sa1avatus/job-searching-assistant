"""Google Gemini provider for app/llm/router.py.

The user supplies their own Gemini API key (APP_GEMINI_API_KEY, from Google AI Studio) and is
billed directly by Google; this module performs no proxying, caching, or markup. It only ever
sends the prompt built by the caller (see app/services/materials_generation.py and
app/services/resume_intake.py), which is instructed to draft text strictly from the candidate's
own verified profile facts.
"""

from __future__ import annotations

import json

import httpx
import structlog

from app.llm.router import ModelProvider, ModelRequest, ModelTaskClass

logger = structlog.get_logger(__name__)

_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
# Rough, conservative per-token pricing used only to enforce ModelRequest.max_cost_usd before
# spending real money; not billing-accurate. ~4 chars/token estimate, output capped below.
_INPUT_USD_PER_TOKEN = 0.10 / 1_000_000
_OUTPUT_USD_PER_TOKEN = 0.40 / 1_000_000
_MAX_OUTPUT_TOKENS = 4096


class GeminiResponseError(RuntimeError):
    """The API returned an error, a blocked/empty response, or non-JSON content."""


class GeminiProvider(ModelProvider):
    name = "gemini"

    def __init__(self, http_client: httpx.AsyncClient, *, api_key: str, model: str) -> None:
        if not api_key.strip():
            raise ValueError("Gemini API key must not be empty")
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
            f"{_API_BASE}/{self._model}:generateContent",
            params={"key": self._api_key},
            json={
                "system_instruction": {
                    "parts": [
                        {
                            "text": (
                                "You output only a single valid JSON object and nothing else: "
                                "no preamble, no markdown code fences, no trailing commentary."
                            )
                        }
                    ]
                },
                "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": _MAX_OUTPUT_TOKENS,
                    "responseMimeType": "application/json",
                    # gemini-2.5-* models "think" by default, spending part of maxOutputTokens on
                    # internal reasoning before the final answer; for a deterministic extraction
                    # task like this, that only risks silently truncating the actual JSON output
                    # (finishReason=MAX_TOKENS with empty content). Disable it explicitly. Older
                    # models without thinking support simply ignore this field.
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
        )
        if response.status_code >= 400:
            raise GeminiResponseError(
                f"Gemini API returned HTTP {response.status_code}: {response.text[:500]}"
            )
        payload = response.json()
        candidates = payload.get("candidates") or []
        if not candidates:
            block_reason = (payload.get("promptFeedback") or {}).get("blockReason")
            logger.warning("gemini_no_candidates", block_reason=block_reason, payload=payload)
            raise GeminiResponseError(f"Gemini returned no candidates (blockReason={block_reason})")
        finish_reason = candidates[0].get("finishReason")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
        if not text:
            logger.warning("gemini_empty_content", finish_reason=finish_reason, payload=payload)
            raise GeminiResponseError(
                f"Gemini returned empty content (finishReason={finish_reason})"
            )
        if text.startswith("```"):
            text = text.strip("`")
            text = text[4:] if text.lower().startswith("json") else text
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            logger.warning("gemini_invalid_json", finish_reason=finish_reason, text=text[:2000])
            raise GeminiResponseError(f"Gemini response was not valid JSON: {error}") from error
        if not isinstance(parsed, dict):
            raise GeminiResponseError("Gemini response JSON was not an object")
        return parsed
