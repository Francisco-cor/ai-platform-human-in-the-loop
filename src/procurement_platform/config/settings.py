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
    platform_callback_token: str | None = Field(
        default=None, alias="PLATFORM_CALLBACK_TOKEN"
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
