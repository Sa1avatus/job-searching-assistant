from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.storage.tables import LlmPreferenceRow, UserRow

LlmProviderName = Literal["anthropic", "gemini"]
SUPPORTED_LLM_PROVIDERS = frozenset({"anthropic", "gemini"})


class InvalidLlmPreference(ValueError):
    pass


class LlmPreferenceNotFound(LookupError):
    pass


class LlmModelDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LlmPreference:
    provider: LlmProviderName
    model: str
    api_key: str


class LlmPreferenceService:
    def __init__(self, session: Session, *, encryption_key: str) -> None:
        self._session = session
        try:
            self._cipher = Fernet(encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise InvalidLlmPreference("LLM credential encryption key is invalid") from error

    def save(
        self, *, user_id: str, provider: str, model: str, api_key: str | None
    ) -> LlmPreferenceRow:
        if self._session.get(UserRow, user_id) is None:
            raise LlmPreferenceNotFound("User not found")
        normalized_provider = provider.strip().casefold()
        normalized_model = model.strip()
        if normalized_provider not in SUPPORTED_LLM_PROVIDERS:
            raise InvalidLlmPreference("Unsupported LLM provider")
        if not normalized_model or len(normalized_model) > 200:
            raise InvalidLlmPreference("LLM model is invalid")
        row = self._session.get(LlmPreferenceRow, user_id)
        normalized_api_key = (api_key or "").strip()
        if not normalized_api_key and row is None:
            raise InvalidLlmPreference("API key is required")
        if normalized_api_key:
            encrypted_api_key = self._cipher.encrypt(normalized_api_key.encode("utf-8")).decode(
                "ascii"
            )
        else:
            assert row is not None
            encrypted_api_key = row.encrypted_api_key
        now = datetime.now(UTC)
        row = row or LlmPreferenceRow(user_id=user_id, created_at=now)
        row.provider = normalized_provider
        row.model = normalized_model
        row.encrypted_api_key = encrypted_api_key
        row.updated_at = now
        self._session.add(row)
        self._session.commit()
        return row

    def load(self, user_id: str) -> LlmPreference | None:
        row = self._session.get(LlmPreferenceRow, user_id)
        if row is None:
            return None
        try:
            api_key = self._cipher.decrypt(row.encrypted_api_key.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError) as error:
            raise InvalidLlmPreference("Saved LLM credential cannot be decrypted") from error
        return LlmPreference(provider=row.provider, model=row.model, api_key=api_key)  # type: ignore[arg-type]


async def fetch_available_models(
    http_client: httpx.AsyncClient, *, provider: str, api_key: str
) -> list[str]:
    normalized_provider = provider.strip().casefold()
    normalized_api_key = api_key.strip()
    if normalized_provider not in SUPPORTED_LLM_PROVIDERS:
        raise InvalidLlmPreference("Unsupported LLM provider")
    if not normalized_api_key:
        raise InvalidLlmPreference("API key is required")
    if normalized_provider == "anthropic":
        response = await http_client.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": normalized_api_key, "anthropic-version": "2023-06-01"},
        )
        models = (
            [entry.get("id") for entry in response.json().get("data", [])]
            if response.is_success
            else []
        )
    else:
        response = await http_client.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": normalized_api_key},
            params={"pageSize": "1000"},
        )
        models = (
            [
                str(entry.get("name", "")).removeprefix("models/")
                for entry in response.json().get("models", [])
                if "generateContent" in entry.get("supportedGenerationMethods", [])
            ]
            if response.is_success
            else []
        )
    if not response.is_success:
        raise LlmModelDiscoveryError(
            f"{normalized_provider} model API returned HTTP {response.status_code}"
        )
    return sorted({str(model) for model in models if model})
