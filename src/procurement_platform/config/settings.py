"""Typed configuration per environment (Fase 1).

All env vars are prefixed with PROCUREMENT_ or AGENT_STATION_ to avoid collisions.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_env: Literal["local", "ci", "staging", "production"] = Field(
        default="local", alias="PROCUREMENT_APP_ENV"
    )
    app_name: str = Field(default="procurement-platform", alias="PROCUREMENT_APP_NAME")
    log_level: str = Field(default="INFO", alias="PROCUREMENT_LOG_LEVEL")
    api_version: str = Field(default="v1", alias="PROCUREMENT_API_VERSION")

    # Database
    database_url: str = Field(
        default="sqlite:///./procurement.db",
        alias="PROCUREMENT_DATABASE_URL",
        description="SQLAlchemy URL. Use postgresql+psycopg://... for prod, sqlite for tests",
    )
    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", alias="PROCUREMENT_REDIS_URL")

    # Observability
    otel_exporter: str = Field(default="none", alias="PROCUREMENT_OTEL_EXPORTER")
    gcs_bucket: str | None = Field(default=None, alias="PROCUREMENT_GCS_BUCKET")
    bigquery_dataset: str | None = Field(default=None, alias="PROCUREMENT_BIGQUERY_DATASET")

    # Agent Station boundary (Fase 0)
    agent_station_base_url: str | None = Field(default=None, alias="AGENT_STATION_BASE_URL")
    agent_station_api_token: str | None = Field(default=None, alias="AGENT_STATION_API_TOKEN")
    agent_station_callback_enabled: bool = Field(
        default=False, alias="AGENT_STATION_CALLBACK_ENABLED"
    )
    agent_station_timeout_ms: int = Field(default=3000, alias="AGENT_STATION_TIMEOUT_MS")
    agent_station_callback_token: str | None = Field(
        default=None, alias="AGENT_STATION_CALLBACK_TOKEN"
    )
    platform_callback_token: str | None = Field(default=None, alias="PLATFORM_CALLBACK_TOKEN")

    # LLM / Agent (Fase 4)
    llm_provider: Literal["auto", "gemini", "deepseek", "fake"] = Field(
        default="auto", alias="PROCUREMENT_LLM_PROVIDER"
    )
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.0-flash", alias="GEMINI_MODEL")
    gemini_base_url: str = Field(
        default="https://generativelanguage.googleapis.com", alias="GEMINI_BASE_URL"
    )
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    llm_fallback_enabled: bool = Field(default=True, alias="PROCUREMENT_LLM_FALLBACK_ENABLED")
    llm_max_tokens: int = Field(default=2048, alias="PROCUREMENT_LLM_MAX_TOKENS")
    llm_temperature: float = Field(default=0.2, alias="PROCUREMENT_LLM_TEMPERATURE")
    llm_timeout_ms: int = Field(default=15000, alias="PROCUREMENT_LLM_TIMEOUT_MS")
    prompt_version: str = Field(default="procurement-v1", alias="PROCUREMENT_PROMPT_VERSION")
    graph_version: str = Field(default="procurement-graph-v1", alias="PROCUREMENT_GRAPH_VERSION")
    # Tool budgets (Fase 4)
    max_tool_calls_per_execution: int = Field(default=20, alias="PROCUREMENT_MAX_TOOL_CALLS")
    max_supplier_queries_per_execution: int = Field(
        default=5, alias="PROCUREMENT_MAX_SUPPLIER_QUERIES"
    )
    max_proposals_per_execution: int = Field(default=3, alias="PROCUREMENT_MAX_PROPOSALS")
    max_tokens_per_execution: int = Field(
        default=8000, alias="PROCUREMENT_MAX_TOKENS_PER_EXECUTION"
    )

    # Security / limits
    max_payload_bytes: int = Field(default=256 * 1024, alias="PROCUREMENT_MAX_PAYLOAD_BYTES")
    default_idempotency_ttl_seconds: int = Field(default=86400, alias="PROCUREMENT_IDEMPOTENCY_TTL")

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def is_ci(self) -> bool:
        return self.app_env == "ci"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def reset_settings_cache() -> None:
    get_settings.cache_clear()
