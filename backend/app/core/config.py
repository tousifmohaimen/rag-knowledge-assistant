"""Application settings, read from environment variables (and a local .env file)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
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

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
