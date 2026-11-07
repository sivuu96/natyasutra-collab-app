"""Environment-based settings. No secrets are hard-coded; see .env.example."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = Field(
        default="sqlite:///./collab_dev.db",
        description="SQLAlchemy URL. Use postgresql+psycopg://... in deployed environments.",
    )
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    session_idle_minutes: int = 60 * 24
    session_absolute_hours: int = 24 * 14
    allow_registration: bool = True

    # Bridge (local agent) settings. If the signing secret is unset, a random one is generated at
    # startup: bridge tokens then become invalid on restart, which is acceptable for development.
    # Production must set BRIDGE_TOKEN_SECRET from a secret manager.
    bridge_token_secret: str | None = None
    bridge_token_minutes: int = 15
    bridge_heartbeat_timeout_seconds: int = 60
    pairing_code_minutes: int = 10

    @property
    def cookie_secure(self) -> bool:
        return self.environment == "production"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def _require_database_url(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("DATABASE_URL must not be empty")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
