import pytest

from app.core.config import Settings


def test_cors_origins_parsed_from_comma_string() -> None:
    settings = Settings(cors_origins="https://a.example, https://b.example")  # type: ignore[arg-type]
    assert settings.cors_origins == ["https://a.example", "https://b.example"]


def test_empty_database_url_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(database_url="   ")


def test_invalid_environment_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(environment="staging")  # type: ignore[arg-type]
