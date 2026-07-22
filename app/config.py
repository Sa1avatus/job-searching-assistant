import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.models import SubmissionMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")

    environment: str = "development"
    database_url: str = "sqlite:///./.artifacts/recruitment.db"
    artifact_directory: Path = Path(".artifacts")
    submission_mode: SubmissionMode = SubmissionMode.REVIEW
    browser_headless: bool = True
    browser_timeout_ms: int = Field(default=30_000, ge=1_000, le=120_000)
    browser_state_encryption_key: SecretStr | None = None
    max_browser_state_bytes: int = Field(default=2_097_152, ge=1_024, le=10_485_760)
    global_browser_concurrency: int = Field(default=2, ge=1, le=10)
    api_key: SecretStr | None = None
    api_clients_json: SecretStr | None = None
    redis_url: str = "redis://localhost:6379/0"
    worker_lease_seconds: int = Field(default=120, ge=10, le=3600)
    worker_poll_seconds: float = Field(default=2.0, ge=0.1, le=60)
    worker_retry_seconds: int = Field(default=30, ge=1, le=3600)
    worker_max_attempts: int = Field(default=3, ge=1, le=20)
    max_document_bytes: int = Field(default=5_242_880, ge=1_024, le=20_971_520)
    max_evidence_bytes: int = Field(default=10_485_760, ge=1_024, le=52_428_800)
    retention_days: int = Field(default=30, ge=1, le=3650)
    retention_interval_seconds: int = Field(default=86_400, ge=60, le=604_800)
    hh_user_agent: str = Field(
        default="JobSearchingAssistant/0.1 (local personal assistant)", min_length=1, max_length=300
    )
    hh_access_token: SecretStr | None = None

    # Real, submitting browser automation against sites that prohibit automated access in their
    # terms of service (hh.ru, LinkedIn). Off by default: enabling this is a deliberate,
    # informed choice by the operator, not something that should turn on accidentally via a
    # generic settings profile. See docs/known-limitations.md.
    enable_headhunter_apply: bool = False
    enable_linkedin_apply: bool = False

    # Anthropic Messages API key used to draft cover letters and free-text screening answers
    # (app/llm/providers/anthropic.py). Optional: materials-generation endpoints return a clear
    # 503 instead of failing obscurely when this is unset. The user supplies their own key and
    # is billed directly by Anthropic for usage; this project performs no proxying or markup.
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-haiku-4-5-20251001"
    anthropic_max_cost_usd_per_request: float = 0.05

    # Google Gemini (generativelanguage.googleapis.com) is an alternative/additional provider for
    # the same materials-generation and resume-analysis features. Set this instead of, or in
    # addition to, APP_ANTHROPIC_API_KEY. The user supplies their own key and is billed directly
    # by Google; this project performs no proxying or markup.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"

    @field_validator(
        "api_key",
        "api_clients_json",
        "hh_access_token",
        "browser_state_encryption_key",
        mode="before",
    )
    @classmethod
    def empty_secret_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def require_production_api_key(self) -> "Settings":
        if (
            self.environment == "production"
            and self.api_key is None
            and self.api_clients_json is None
        ):
            raise ValueError("APP_API_KEY or APP_API_CLIENTS_JSON is required in production")
        self.api_clients()
        return self

    def api_clients(self) -> dict[str, frozenset[str]]:
        clients: dict[str, frozenset[str]] = {}
        if self.api_key is not None:
            clients[self.api_key.get_secret_value()] = frozenset({"*"})
        if self.api_clients_json is None:
            return clients
        try:
            payload = json.loads(self.api_clients_json.get_secret_value())
        except json.JSONDecodeError as error:
            raise ValueError("APP_API_CLIENTS_JSON must be valid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("APP_API_CLIENTS_JSON must be a JSON object")
        for key, scopes in payload.items():
            if not isinstance(key, str) or not key or not isinstance(scopes, list):
                raise ValueError("Each API client must map a non-empty key to a scope list")
            if not scopes or any(not isinstance(scope, str) or not scope for scope in scopes):
                raise ValueError("API client scopes must be non-empty strings")
            clients[key] = frozenset(scopes)
        return clients


@lru_cache
def get_settings() -> Settings:
    return Settings()
