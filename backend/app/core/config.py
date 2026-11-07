"""Environment-based settings. No secrets are hard-coded; see .env.example and docs/deployment.md."""

import os
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = Field(
        default="sqlite:///./collab_dev.db",
        description="SQLAlchemy URL. Deployed environments use PostgreSQL.",
    )
    # NoDecode: read the plain comma-separated env value as-is instead of expecting JSON.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"])
    session_idle_minutes: int = 60 * 24
    session_absolute_hours: int = 24 * 14
    allow_registration: bool = True
    # Built frontend to serve from the API (single-origin deployment). None disables static serving.
    frontend_dist: str | None = None

    # Bridge (local agent) settings. In development an unset secret is generated per process, so bridge
    # tokens stop working on restart. Production refuses to start without BRIDGE_TOKEN_SECRET.
    bridge_token_secret: str | None = None
    bridge_token_minutes: int = 15
    bridge_heartbeat_timeout_seconds: int = 60
    pairing_code_minutes: int = 10

    # Secure cookies are the production default. Set COOKIE_SECURE=false only for plain-HTTP local testing
    # of a production build; never on a public HTTPS deployment.
    cookies_secure_override: bool | None = Field(default=None, validation_alias="COOKIE_SECURE")

    @property
    def cookie_secure(self) -> bool:
        if self.cookies_secure_override is not None:
            return self.cookies_secure_override
        return self.environment == "production"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("DATABASE_URL must not be empty")
        # Hosting providers hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver named.
        if value.startswith("postgres://"):
            value = "postgresql+psycopg://" + value[len("postgres://") :]
        elif value.startswith("postgresql://"):
            value = "postgresql+psycopg://" + value[len("postgresql://") :]
        return value

    @model_validator(mode="after")
    def _apply_deployment_defaults(self) -> "Settings":
        # Render sets RENDER_EXTERNAL_URL to the public address. WebSocket origin checks need it allowed.
        external = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
        if external and external not in self.cors_origins:
            self.cors_origins = [*self.cors_origins, external]
        if self.environment == "production":
            if not self.bridge_token_secret or len(self.bridge_token_secret) < 32:
                raise ValueError("BRIDGE_TOKEN_SECRET must be set (at least 32 characters) in production")
            if self.database_url.startswith("sqlite"):
                raise ValueError("production must use PostgreSQL, not SQLite")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
