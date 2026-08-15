from __future__ import annotations

import json
from typing import Any

import httpx
import structlog

from app.llm.router import ModelProvider, ModelRequest, ModelTaskClass

logger = structlog.get_logger(__name__)


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


def _is_response_format_error(body: str) -> bool:
    """Detect a 400 that rejects the ``response_format`` parameter.

    Some OpenAI-compatible servers (e.g. the local-code-worker gateway) only
    support ``text`` and ``json_object`` response formats. The provider adapts
    to them by falling back to ``json_object`` (the schema stays embedded in the
    system prompt). The check is conservative so unrelated 400s still raise.
    """
    lowered = body.casefold()
    return "response_format" in lowered or "response format" in lowered


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
        self._ollama_base = str(self._completion_url.copy_with(path="")) if self._is_ollama else ""
        self._http_client = http_client
        self._api_key = normalized_api_key
        self._model = normalized_model
        # Set to False after a 400 that rejects json_schema response_format; the
        # provider then uses json_object for every subsequent request of this
        # provider instance (schema stays in the system prompt).
        self._supports_json_schema = True

    def supports(self, task_class: ModelTaskClass) -> bool:
        return task_class in {
            ModelTaskClass.LOW_COST,
            ModelTaskClass.STRONG_REASONING,
        }

    def estimate_cost_usd(self, request: ModelRequest) -> float:
        return 0.0

    def _response_format_for(self, request: ModelRequest) -> dict[str, object]:
        """Pick the response_format for the current endpoint capability.

        Servers that reject ``json_schema`` (detected once, then cached on the
        instance) get ``json_object``; the full schema stays embedded in the
        system prompt either way, so the model still receives it.
        """
        if request.response_schema is None:
            return {"type": "json_object"}
        if not self._supports_json_schema:
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_response",
                "strict": True,
                "schema": request.response_schema,
            },
        }

    def _retry_with_json_object_if_unsupported(
        self,
        request: ModelRequest,
        payload: dict[str, object],
        response: httpx.Response,
    ) -> bool:
        """Fall back to json_object once when the server rejects json_schema.

        Mutates ``payload`` in place and remembers the capability for subsequent
        requests, so only the first call pays the 400 round-trip. Returns True
        when the caller should re-send the (updated) payload.
        """
        if (
            response.status_code != 400
            or not self._supports_json_schema
            or request.response_schema is None
        ):
            return False
        if not _is_response_format_error(response.text):
            return False
        self._supports_json_schema = False
        logger.info(
            "openai_compatible_json_schema_unsupported_fallback",
            task_name=request.task_name,
            status_code=response.status_code,
        )
        payload["response_format"] = {"type": "json_object"}
        return True

    async def _complete_ollama_native(
        self,
        request: ModelRequest,
        system_prompt: str,
    ) -> dict[str, object]:
        """Use Ollama native /api/chat with think=false to avoid reasoning token drain."""
        payload: dict[str, object] = {
            "model": request.model_override or self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.prompt},
            ],
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0,
                "num_predict": request.max_output_tokens or 16384,
                "num_ctx": request.context_size or 8192,
            },
        }
        if request.response_schema is not None:
            payload["format"] = simplify_json_schema_for_ollama(request.response_schema)

        ollama_url = f"{self._ollama_base}/api/chat"
        response = await self._http_client.post(
            ollama_url,
            json=payload,
        )

        if response.status_code >= 400:
            raise OpenAICompatibleResponseError(
                f"Ollama native API returned HTTP {response.status_code}; "
                f"body={response.text[:1000]!r}"
            )

        data = response.json()
        content = data.get("message", {}).get("content", "")
        done_reason = data.get("done_reason", "")

        if not content or not content.strip():
            raise OpenAICompatibleResponseError(
                f"Ollama native API returned empty content; done_reason={done_reason!r}"
            )

        return parse_model_json(content)

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
        system_prompt = (
            "Return exactly one valid JSON object. "
            "Do not use Markdown code fences. "
            "Do not include explanations, comments, "
            "headings, or text before or after the JSON." + schema_instruction
        )

        # For Ollama: use native API with think=false to prevent reasoning token drain
        if self._is_ollama:
            return await self._complete_ollama_native(request, system_prompt)

        request_payload: dict[str, object] = {
            "model": request.model_override or self._model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": request.prompt,
                },
            ],
            "temperature": 0,
            "stream": False,
            "max_tokens": request.max_output_tokens or 16384,
            "response_format": self._response_format_for(request),
        }
        response = await self._http_client.post(
            self._completion_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=request_payload,
        )

        if self._retry_with_json_object_if_unsupported(request, request_payload, response):
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
