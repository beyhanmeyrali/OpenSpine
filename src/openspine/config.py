"""Application settings — loaded from environment with sensible local defaults.

Read from `.env` in development; in production the environment is the source of
truth and `.env` is ignored.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENSPINE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Postgres
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "openspine"
    db_user: str = "openspine"
    db_password: str = "openspine_dev_only"

    # Redis (event bus)
    redis_url: RedisDsn = Field(default="redis://localhost:6379/0")  # type: ignore[assignment]

    # Qdrant (semantic index)
    qdrant_url: str = "http://localhost:6333"

    # Ollama (embeddings)
    ollama_url: str = "http://localhost:11434"
    embedding_model: str = "qwen2.5:1.5b"

    # OpenTelemetry
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "openspine-api"

    # Auth / sessions
    secret_key: str = "change-me-in-real-deployments-this-is-dev-only"
    session_idle_minutes: int = 30
    session_absolute_hours: int = 12

    @property
    def database_url(self) -> str:
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.db_user,
                password=self.db_password,
                host=self.db_host,
                port=self.db_port,
                path=self.db_name,
            )
        )

    @property
    def sync_database_url(self) -> str:
        # Alembic uses the synchronous driver. We standardise on psycopg3
        # (`psycopg`, installed via the `psycopg[binary]` extra) — newer
        # protocol support, simpler maintenance story than psycopg2.
        return str(
            PostgresDsn.build(
                scheme="postgresql+psycopg",
                username=self.db_user,
                password=self.db_password,
                host=self.db_host,
                port=self.db_port,
                path=self.db_name,
            )
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
