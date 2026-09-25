from __future__ import annotations

import json
import re
from typing import Any

import httpx
import structlog

from app.llm.router import ModelProvider, ModelRequest, ModelTaskClass

logger = structlog.get_logger(__name__)

# A retry-with-larger-budget ceiling shared by every matching task (entailment/decompose/
# extraction all cap max_output_tokens at or below this today, see app/config.py).
_TRUNCATION_RETRY_MAX_TOKENS_CEILING = 8192

_LEADING_THINK_BLOCK = re.compile(r"\A\s*<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


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


def _is_grammar_parse_error(body: str) -> bool:
    """Detect a llama.cpp-family 400 where the json_schema→GBNF grammar failed to parse.

    Message observed verbatim (2026-09-25) from a llama.cpp-based local gateway:
    ``{"error":{"code":400,"message":"Failed to initialize samplers: failed to parse
    grammar","type":"invalid_request_error"}}``. This exact wording is llama.cpp-
    specific (traced to ``common/sampling.cpp``'s ``llama_sampler_init_grammar``
    failure path) and never appears in OpenRouter or other cloud providers' error
    bodies, so matching on it is inherently provider-scoped without needing a
    separate config flag. It was NOT reproducible on demand with the identical
    schema/payload on retry - falling back to json_object (still Pydantic-validated
    client-side) is a robust response regardless of whether the underlying cause is
    a genuine schema incompatibility or transient server-side state.
    """
    lowered = body.casefold()
    return "failed to parse grammar" in lowered or "failed to initialize samplers" in lowered


class OpenAICompatibleProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        api_key: str,
        model: str,
        base_url: str,
        reasoning_mitigation_enabled: bool = False,
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
        self._reasoning_mitigation_enabled = reasoning_mitigation_enabled
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
        """Fall back to json_object once when the server rejects/can't apply json_schema.

        Two distinct triggers, both scoped by matching the server's own error text
        (never a generic "any 400 retries" rule, so unrelated 400s like a bad API key
        still raise): the server rejecting ``response_format`` outright, or a
        llama.cpp-family grammar-parse failure while trying to apply the schema as a
        GBNF grammar. Either way, the schema stays embedded in the system prompt and
        the response is still Pydantic-validated client-side, so this trades strict
        server-side enforcement for a working call - not a silent correctness gap.

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
        if not (_is_response_format_error(response.text) or _is_grammar_parse_error(response.text)):
            return False
        self._supports_json_schema = False
        logger.info(
            "openai_compatible_json_schema_unsupported_fallback",
            task_name=request.task_name,
            status_code=response.status_code,
            reason="grammar_parse_error"
            if _is_grammar_parse_error(response.text)
            else "response_format_rejected",
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

        logger.debug(
            "openai_compatible_completion",
            task_name=request.task_name,
            finish_reason=done_reason,
            usage={
                "prompt_tokens": data.get("prompt_eval_count"),
                "completion_tokens": data.get("eval_count"),
            },
            has_separate_reasoning_field=False,
            reasoning_field_len=None,
            content_len=len(content),
            retry=False,
        )

        try:
            return parse_model_json(content)
        except OpenAICompatibleResponseError as error:
            if done_reason in ("length", "abort", "load"):
                from app.llm.router import LLMTruncatedOutputError

                raise LLMTruncatedOutputError(
                    f"Ollama output truncated (done_reason={done_reason!r}): "
                    f"{error}; content_len={len(content)}",
                    finish_reason=str(done_reason),
                ) from error
            from app.llm.router import LLMInvalidJSONError

            raise LLMInvalidJSONError(
                f"Ollama returned invalid JSON (done_reason={done_reason!r}): {error}"
            ) from error

    async def _post_chat_completion(self, request_payload: dict[str, object]) -> httpx.Response:
        return await self._http_client.post(
            self._completion_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=request_payload,
        )

    async def _send_and_parse_choice(
        self,
        request: ModelRequest,
        request_payload: dict[str, object],
    ) -> tuple[str, str | None, dict[str, object], object]:
        """POST the payload and return (content, finish_reason, message, usage).

        Handles the json_schema→json_object fallback retry and every HTTP/shape
        validation that used to live inline in ``complete()``.
        """
        response = await self._post_chat_completion(request_payload)

        if self._retry_with_json_object_if_unsupported(request, request_payload, response):
            response = await self._post_chat_completion(request_payload)

        response_content_type = response.headers.get("content-type", "")
        response_text = response.text

        if response.status_code >= 400:
            excerpt = response_text[:1000].replace(self._api_key, "[redacted]")
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

        return content, finish_reason, message, payload.get("usage")

    def _log_completion(
        self,
        request: ModelRequest,
        *,
        finish_reason: str | None,
        message: dict[str, object],
        usage: object,
        content: str,
        retry: bool = False,
    ) -> None:
        # Pure observability: never affects control flow. usage/finish_reason let us
        # tell "the model reasoned and ran out of budget" apart from "genuinely
        # verbose JSON" without re-running the request by hand.
        reasoning = message.get("reasoning") or message.get("reasoning_content")
        logger.debug(
            "openai_compatible_completion",
            task_name=request.task_name,
            finish_reason=finish_reason,
            usage=usage,
            has_separate_reasoning_field=reasoning is not None,
            reasoning_field_len=len(reasoning) if isinstance(reasoning, str) else None,
            content_len=len(content),
            retry=retry,
        )

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
        # Reasoning models (qwen3.x) drain the token budget on thinking; all
        # structured outputs here need none, so disable it explicitly. This is not
        # a documented OpenAI Chat Completions field — it is a no-op on servers
        # (e.g. OpenRouter) that don't recognize it, confirmed by direct testing
        # (2026-09-25): reasoning_tokens usage was identical with and without it.
        request_payload["think"] = False
        if self._reasoning_mitigation_enabled:
            # OpenRouter's own unified reasoning control. Verified (2026-09-25) to
            # correctly suppress the separate `message.reasoning` field, but it does
            # NOT reduce reasoning token usage — the model still "thinks" internally
            # on routes that support it, so this alone does not prevent truncation.
            request_payload["reasoning"] = {"exclude": True}
            # vLLM/llama.cpp convention for Qwen3-family models specifically. Gated
            # behind the same flag as the OpenRouter reasoning control above (both are
            # "reasoning mitigation", just different mechanisms per provider) so this
            # never changes the request sent to OpenRouter or any other provider
            # unless the flag is explicitly turned on.
            request_payload["chat_template_kwargs"] = {"enable_thinking": False}
        if request.context_size is not None:
            # Matching passes its tuned per-sequence context; the local-code-worker
            # gateway honors it per request (winning over the routed tier default).
            request_payload["context_length"] = request.context_size

        content, finish_reason, message, usage = await self._send_and_parse_choice(
            request, request_payload
        )
        self._log_completion(
            request, finish_reason=finish_reason, message=message, usage=usage, content=content
        )

        parse_target = content
        if self._reasoning_mitigation_enabled:
            # Some OpenRouter-routed backends emit chain-of-thought as plain prose
            # ahead of the JSON with no <think> delimiter at all (observed directly
            # in production logs) — this strip only helps the minority of backends
            # that do wrap it in <think>...</think>; it is not a general fix.
            parse_target = _LEADING_THINK_BLOCK.sub("", content, count=1)

        try:
            return parse_model_json(parse_target)
        except OpenAICompatibleResponseError as error:
            # Distinguish truncated/aborted output from genuine JSON parse errors
            if finish_reason not in ("length", "abort"):
                from app.llm.router import LLMInvalidJSONError

                raise LLMInvalidJSONError(
                    f"LLM returned invalid JSON (finish_reason={finish_reason!r}): {error}"
                ) from error

            if self._reasoning_mitigation_enabled and finish_reason == "length":
                retried = await self._retry_truncated_with_larger_budget(request, request_payload)
                if retried is not None:
                    return retried

            from app.llm.router import LLMTruncatedOutputError

            raise LLMTruncatedOutputError(
                f"LLM output truncated (finish_reason={finish_reason!r}): "
                f"{error}; content_len={len(content)}",
                finish_reason=str(finish_reason),
            ) from error

    async def _retry_truncated_with_larger_budget(
        self,
        request: ModelRequest,
        request_payload: dict[str, object],
    ) -> dict[str, object] | None:
        """One retry with a larger max_tokens after a finish_reason=length truncation.

        Returns the parsed JSON on success, or None if the retry also failed (the
        caller then raises the original truncation error — never a second, different
        one, so this stays a single well-defined failure mode from the caller's view).
        """
        raw_max_tokens = request_payload.get("max_tokens")
        original_max_tokens = raw_max_tokens if isinstance(raw_max_tokens, int) else 0
        retry_max_tokens = min(
            max(original_max_tokens * 2, original_max_tokens + 1),
            _TRUNCATION_RETRY_MAX_TOKENS_CEILING,
        )
        if retry_max_tokens <= original_max_tokens:
            return None
        retry_payload = dict(request_payload)
        retry_payload["max_tokens"] = retry_max_tokens
        logger.info(
            "openai_compatible_truncation_retry",
            task_name=request.task_name,
            original_max_tokens=original_max_tokens,
            retry_max_tokens=retry_max_tokens,
        )
        try:
            (
                retry_content,
                retry_finish_reason,
                retry_message,
                retry_usage,
            ) = await self._send_and_parse_choice(request, retry_payload)
        except OpenAICompatibleResponseError:
            return None
        self._log_completion(
            request,
            finish_reason=retry_finish_reason,
            message=retry_message,
            usage=retry_usage,
            content=retry_content,
            retry=True,
        )
        retry_parse_target = _LEADING_THINK_BLOCK.sub("", retry_content, count=1)
        try:
            return parse_model_json(retry_parse_target)
        except OpenAICompatibleResponseError:
            return None
