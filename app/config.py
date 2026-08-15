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
    opensearch_url: str = "http://localhost:9200"
    opensearch_evidence_index_prefix: str = "candidate-evidence"
    opensearch_evidence_read_alias: str = "candidate-evidence-read"
    opensearch_evidence_write_alias: str = "candidate-evidence-write"
    embedding_dimensions: int = Field(default=384, ge=1, le=65_536)
    matching_v2_enabled: bool = False
    matching_v2_shadow_mode: bool = True
    matching_v2_fallback_enabled: bool = True
    matching_model_service_url: str = "http://localhost:8090"
    matching_retrieval_top_k: int = Field(default=20, ge=1, le=200)
    matching_reranker_top_k: int = Field(default=5, ge=1, le=50)
    embedding_service_url: str | None = None
    matching_model_timeout_seconds: float = Field(default=120, ge=1, le=600)
    reranker_service_url: str | None = None
    reranker_api_key: SecretStr | None = None
    rag_service_url: str | None = None
    browser_worker_url: str = "http://browser-worker:8080"
    rag_api_key: SecretStr | None = None
    rag_project_id: str | None = None
    rag_collection: str = "default"
    rag_timeout_seconds: float = Field(default=30, ge=1, le=300)
    rag_enabled: bool = False
    worker_lease_seconds: int = Field(default=120, ge=10, le=3600)
    worker_poll_seconds: float = Field(default=2.0, ge=0.1, le=60)
    worker_retry_seconds: int = Field(default=30, ge=1, le=3600)
    worker_max_attempts: int = Field(default=3, ge=1, le=20)
    matching_worker_concurrency: int = Field(default=2, ge=1, le=8)
    matching_llm_concurrency: int = Field(default=10, ge=1, le=50)
    matching_cache_ttl_seconds: int = Field(default=604_800, ge=60, le=31_536_000)
    matching_entailment_max_candidates: int = Field(default=3, ge=1, le=10)
    matching_entailment_max_tokens: int = Field(default=512, ge=64, le=8192)
    matching_entailment_context_size: int = Field(default=4096, ge=512, le=32768)
    # Pairs per batched entailment LLM call (1 disables batching). The batch prompt
    # evaluates several (claim, evidence) pairs in one request, amortizing
    # per-request overhead; batches are packed to fit the context window.
    matching_entailment_batch_size: int = Field(default=5, ge=1, le=20)
    matching_decompose_max_tokens: int = Field(default=2048, ge=64, le=8192)
    # MUST match matching_entailment_context_size: Ollama reloads the model whenever
    # num_ctx changes, and reloading a 3GB model between every decompose/entailment
    # group dominated local-model matching latency.
    matching_decompose_context_size: int = Field(default=4096, ge=512, le=32768)
    max_document_bytes: int = Field(default=5_242_880, ge=1_024, le=20_971_520)
    max_evidence_bytes: int = Field(default=10_485_760, ge=1_024, le=52_428_800)
    retention_days: int = Field(default=30, ge=1, le=3650)
    retention_interval_seconds: int = Field(default=86_400, ge=60, le=604_800)
    hh_user_agent: str = Field(
        default="JobSearchingAssistant/1.0 (local personal assistant)", min_length=1, max_length=300
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
    materials_generation_timeout_seconds: float = Field(default=120, ge=10, le=600)

    @field_validator(
        "api_key",
        "api_clients_json",
        "hh_access_token",
        "browser_state_encryption_key",
        "reranker_api_key",
        "rag_api_key",
        mode="before",
    )
    @classmethod
    def empty_secret_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator(
        "embedding_service_url", "reranker_service_url", "rag_service_url", mode="before"
    )
    @classmethod
    def empty_service_url_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def resolved_embedding_service_url(self) -> str:
        """Resolve the legacy matching URL only for the embedding service migration."""
        return self.embedding_service_url or self.matching_model_service_url

    @field_validator(
        "opensearch_evidence_index_prefix",
        "opensearch_evidence_read_alias",
        "opensearch_evidence_write_alias",
    )
    @classmethod
    def validate_opensearch_name(cls, value: str) -> str:
        if (
            not value
            or value != value.casefold()
            or any(
                character in value for character in (" ", "\\", "/", "*", "?", '"', "<", ">", "|")
            )
        ):
            raise ValueError("OpenSearch index and alias names must be lowercase and path-safe")
        return value

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
