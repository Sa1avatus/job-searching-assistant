from __future__ import annotations

import json

import httpx

from app.llm.router import ModelProvider, ModelRequest, ModelTaskClass


class OpenAICompatibleResponseError(RuntimeError):
    """An OpenAI-compatible endpoint returned an unusable response."""


def normalize_openai_compatible_base_url(base_url: str) -> str:
    try:
        parsed_url = httpx.URL(base_url.strip())
    except (httpx.InvalidURL, TypeError) as error:
        raise ValueError("OpenAI-compatible base URL is invalid") from error
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.host
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ValueError("OpenAI-compatible base URL is invalid")
    return str(parsed_url.copy_with(path=parsed_url.path.rstrip("/"))).rstrip("/")


class OpenAICompatibleProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        api_key: str,
        model: str,
        base_url: str,
    ) -> None:
        normalized_api_key = api_key.strip()
        normalized_model = model.strip()
        if not normalized_api_key:
            raise ValueError("OpenAI-compatible API key must not be empty")
        if not normalized_model:
            raise ValueError("OpenAI-compatible model must not be empty")
        normalized_base_url = normalize_openai_compatible_base_url(base_url)
        self._completion_url = httpx.URL(f"{normalized_base_url}/chat/completions")
        self._http_client = http_client
        self._api_key = normalized_api_key
        self._model = normalized_model

    def supports(self, task_class: ModelTaskClass) -> bool:
        return task_class in {ModelTaskClass.LOW_COST, ModelTaskClass.STRONG_REASONING}

    def estimate_cost_usd(self, request: ModelRequest) -> float:
        return 0.0

    async def complete(self, request: ModelRequest) -> dict[str, object]:
        response = await self._http_client.post(
            self._completion_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Return only one valid JSON object with no extra text.",
                    },
                    {"role": "user", "content": request.prompt},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
        )
        if response.status_code >= 400:
            excerpt = response.text[:500].replace(self._api_key, "[redacted]")
            raise OpenAICompatibleResponseError(
                f"OpenAI-compatible API returned HTTP {response.status_code}: {excerpt}"
            )
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise OpenAICompatibleResponseError(
                "OpenAI-compatible response has an invalid shape"
            ) from error
        if not isinstance(content, str) or not content.strip():
            raise OpenAICompatibleResponseError(
                "OpenAI-compatible response content is empty"
            )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as error:
            raise OpenAICompatibleResponseError(
                f"OpenAI-compatible response was not valid JSON: {error}"
            ) from error
        if not isinstance(parsed, dict):
            raise OpenAICompatibleResponseError(
                "OpenAI-compatible response JSON was not an object"
            )
        return parsed
