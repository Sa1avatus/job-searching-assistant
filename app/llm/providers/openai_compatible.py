from __future__ import annotations

import json
from typing import Any

import httpx

from app.llm.router import ModelProvider, ModelRequest, ModelTaskClass


class OpenAICompatibleResponseError(RuntimeError):
    """An OpenAI-compatible endpoint returned an unusable response."""


_OLLAMA_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "default",
        "description",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "title",
    }
)


def simplify_json_schema_for_ollama(schema: dict[str, object]) -> dict[str, object]:
    """Inline local definitions and retain only grammar-relevant constraints."""

    definitions = schema.get("$defs")
    known_definitions = definitions if isinstance(definitions, dict) else {}

    def simplify(value: object) -> object:
        if isinstance(value, list):
            return [simplify(item) for item in value]
        if not isinstance(value, dict):
            return value

        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            definition_name = reference.removeprefix("#/$defs/")
            resolved = known_definitions.get(definition_name)
            if isinstance(resolved, dict):
                merged = dict(resolved)
                merged.update({key: item for key, item in value.items() if key != "$ref"})
                return simplify(merged)

        return {
            key: simplify(item)
            for key, item in value.items()
            if key != "$defs" and key not in _OLLAMA_UNSUPPORTED_SCHEMA_KEYWORDS
        }

    simplified = simplify(schema)
    if not isinstance(simplified, dict):
        raise ValueError("JSON schema must be an object")
    return simplified


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


def remove_json_markdown_fence(content: str) -> str:
    stripped = content.strip()

    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()

    if not lines:
        return stripped

    opening_fence = lines[0].strip().lower()

    if opening_fence not in {"```", "```json", "```javascript"}:
        return stripped

    lines = lines[1:]

    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    return "\n".join(lines).strip()


def parse_model_json(content: str) -> dict[str, object]:
    normalized_content = remove_json_markdown_fence(content)

    try:
        parsed = json.loads(normalized_content)
    except json.JSONDecodeError as error:
        excerpt = normalized_content[:2000]

        raise OpenAICompatibleResponseError(
            f"OpenAI-compatible model content was not valid JSON: {error}; content={excerpt!r}"
        ) from error

    if not isinstance(parsed, dict):
        raise OpenAICompatibleResponseError(
            f"OpenAI-compatible response JSON was not an object: received {type(parsed).__name__}"
        )

    return parsed


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
        self._is_ollama = self._completion_url.port == 11434
        self._http_client = http_client
        self._api_key = normalized_api_key
        self._model = normalized_model

    def supports(self, task_class: ModelTaskClass) -> bool:
        return task_class in {
            ModelTaskClass.LOW_COST,
            ModelTaskClass.STRONG_REASONING,
        }

    def estimate_cost_usd(self, request: ModelRequest) -> float:
        return 0.0

    async def complete(
        self,
        request: ModelRequest,
    ) -> dict[str, object]:
        response_schema = request.response_schema
        schema_instruction = (
            "\nFollow this JSON Schema exactly: "
            + json.dumps(response_schema, ensure_ascii=False, separators=(",", ":"))
            if response_schema is not None
            else ""
        )
        response_format: dict[str, object] = {"type": "json_object"}
        if response_schema is not None:
            grammar_schema = (
                simplify_json_schema_for_ollama(response_schema)
                if self._is_ollama
                else response_schema
            )
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "strict": True,
                    "schema": grammar_schema,
                },
            }
        request_payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return exactly one valid JSON object. "
                        "Do not use Markdown code fences. "
                        "Do not include explanations, comments, "
                        "headings, or text before or after the JSON." + schema_instruction
                    ),
                },
                {
                    "role": "user",
                    "content": request.prompt,
                },
            ],
            "temperature": 0,
            "stream": False,
            "max_tokens": 16384,
            "response_format": response_format,
        }
        if self._is_ollama:
            request_payload["reasoning_effort"] = "none"
        response = await self._http_client.post(
            self._completion_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=request_payload,
        )

        response_content_type = response.headers.get(
            "content-type",
            "",
        )
        response_text = response.text

        if response.status_code >= 400:
            excerpt = response_text[:1000].replace(
                self._api_key,
                "[redacted]",
            )

            raise OpenAICompatibleResponseError(
                "OpenAI-compatible API returned "
                f"HTTP {response.status_code}; "
                f"content_type={response_content_type!r}; "
                f"body={excerpt!r}"
            )

        if not response_text.strip():
            raise OpenAICompatibleResponseError(
                "OpenAI-compatible API returned an empty HTTP body; "
                f"HTTP {response.status_code}; "
                f"content_type={response_content_type!r}"
            )

        if "text/event-stream" in response_content_type.lower():
            raise OpenAICompatibleResponseError(
                "OpenAI-compatible API unexpectedly returned an SSE "
                "stream despite stream=false; "
                f"body={response_text[:1000]!r}"
            )

        try:
            payload: Any = response.json()
        except ValueError as error:
            raise OpenAICompatibleResponseError(
                "OpenAI-compatible HTTP response was not valid JSON; "
                f"HTTP {response.status_code}; "
                f"content_type={response_content_type!r}; "
                f"body={response_text[:2000]!r}"
            ) from error

        if not isinstance(payload, dict):
            raise OpenAICompatibleResponseError(
                "OpenAI-compatible HTTP response must be a JSON object; "
                f"received {type(payload).__name__}"
            )

        try:
            choices = payload["choices"]
            first_choice = choices[0]
            message = first_choice["message"]
            content = message["content"]
            finish_reason = first_choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as error:
            raise OpenAICompatibleResponseError(
                f"OpenAI-compatible response has an invalid shape; payload={str(payload)[:2000]}"
            ) from error

        if not isinstance(content, str) or not content.strip():
            raise OpenAICompatibleResponseError(
                "OpenAI-compatible response content is empty; "
                f"finish_reason={finish_reason!r}; "
                f"payload={str(payload)[:2000]}"
            )

        try:
            return parse_model_json(content)
        except OpenAICompatibleResponseError as error:
            raise OpenAICompatibleResponseError(
                f"{error}; finish_reason={finish_reason!r}"
            ) from error
