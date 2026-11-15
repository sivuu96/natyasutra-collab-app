"""Environment-based settings. No secrets are hard-coded; see .env.example and docs/deployment.md."""

import os
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

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

    # Signs the short-lived tokens that let an agent run's runtime call the hub. In development an unset secret
    # is generated per process, so tokens stop working on restart. Production refuses to start without it.
    runtime_token_secret: str | None = None

    # Where agent runs execute. "fake" runs the outbox with a deterministic in-process provider (tests and local
    # runs). "cloud_run_service" sends runs to the warm Cloud Run service (production; see RUNTIME_SERVICE_URL).
    execution_provider: Literal["fake", "cloud_run_service"] = "fake"
    # URL of the private Cloud Run service that runs agent invocations in warm-instance mode.
    runtime_service_url: str | None = None
    # Password reset emails, sent through Resend. Without a key, reset requests are refused with a clear message.
    # The sender address must be on a domain verified with the provider.
    email_api_key: SecretStr | None = None
    email_from: str | None = None
    # Public address of the web app, used in reset links. Render sets RENDER_EXTERNAL_URL.
    public_app_url: str | None = None
    # Base64 of the dispatcher service-account JSON key. A secret: never logged or returned by the API.
    gcp_dispatcher_key: SecretStr | None = None
    # Repositories an agent run may clone, as "owner/name". A run for any other repository is refused.
    github_allowed_repos: Annotated[list[str], NoDecode] = Field(default_factory=list)
    execution_worker_enabled: bool = True
    execution_worker_interval_seconds: float = 5.0

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

    @field_validator("github_allowed_repos", mode="before")
    @classmethod
    def _split_repos(cls, value: object) -> object:
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
        if self.public_app_url is None and external:
            self.public_app_url = external
        if self.execution_provider == "cloud_run_service":
            if self.gcp_dispatcher_key is None:
                raise ValueError("GCP_DISPATCHER_KEY is required when EXECUTION_PROVIDER=cloud_run_service")
            if not self.runtime_service_url:
                raise ValueError("RUNTIME_SERVICE_URL is required when EXECUTION_PROVIDER=cloud_run_service")
        if self.environment == "production":
            if not self.runtime_token_secret or len(self.runtime_token_secret) < 32:
                raise ValueError("RUNTIME_TOKEN_SECRET must be set (at least 32 characters) in production")
            if self.database_url.startswith("sqlite"):
                raise ValueError("production must use PostgreSQL, not SQLite")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
