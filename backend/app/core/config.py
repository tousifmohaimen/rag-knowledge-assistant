"""Application settings, read from environment variables (and a local .env file)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "RAG Knowledge Assistant"
    environment: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # The API connects as a restricted role so Postgres row-level security applies to it.
    database_url: str = "postgresql+asyncpg://rka_app:rka_app@localhost:5432/rka"
    # Migrations run as the schema owner, which can create tables, indexes and policies.
    migrations_database_url: str = "postgresql+asyncpg://rka:rka@localhost:5432/rka"

    # No default on purpose: the app refuses to start without a real secret.
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_minutes: int = Field(default=15, ge=1, le=120)
    refresh_token_days: int = Field(default=7, ge=1, le=90)

    cors_origins: list[str] = ["http://localhost:5173"]

    # Background jobs (document ingestion) go through Redis.
    redis_url: str = "redis://localhost:6379/0"

    # Uploaded files. Local disk for now; the storage interface allows S3 later.
    storage_dir: Path = Path("data/uploads")
    max_upload_mb: int = Field(default=20, ge=1, le=200)

    # "fake" needs no API key: it builds vectors from the words in the text, which is
    # enough for local development and tests. Use "openai" for real semantic search.
    embedding_provider: Literal["fake", "openai"] = "fake"
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_batch_size: int = Field(default=64, ge=1, le=2048)

    # Chunk sizes are starting values; tune them with the evaluation set.
    chunk_target_tokens: int = Field(default=600, ge=50, le=4000)
    chunk_overlap_tokens: int = Field(default=100, ge=0, le=1000)

    @model_validator(mode="after")
    def _check_consistency(self) -> "Settings":
        if self.embedding_provider == "openai" and self.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai")
        if self.chunk_overlap_tokens >= self.chunk_target_tokens:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be smaller than CHUNK_TARGET_TOKENS")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
