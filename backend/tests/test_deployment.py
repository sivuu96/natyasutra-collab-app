"""Deployment behaviour: production guards, URL handling, single-origin frontend and security headers."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.base import Base
from app.db.session import make_engine
from app.main import create_app

SECRET = "x" * 40


def test_production_requires_a_bridge_secret() -> None:
    with pytest.raises(ValueError, match="BRIDGE_TOKEN_SECRET"):
        Settings(environment="production", database_url="postgresql+psycopg://u:p@h/db", bridge_token_secret=None)


def test_production_refuses_short_secrets_and_sqlite() -> None:
    with pytest.raises(ValueError):
        Settings(environment="production", database_url="postgresql+psycopg://u:p@h/db", bridge_token_secret="short")
    with pytest.raises(ValueError, match="PostgreSQL"):
        Settings(environment="production", database_url="sqlite:///x.db", bridge_token_secret=SECRET)


def test_production_settings_load_when_correct() -> None:
    settings = Settings(
        environment="production", database_url="postgresql+psycopg://u:p@h/db", bridge_token_secret=SECRET
    )
    assert settings.cookie_secure is True


@pytest.mark.parametrize("given", ["postgres://u:p@h/db", "postgresql://u:p@h/db"])
def test_hosting_provider_urls_are_normalised_for_the_driver(given: str) -> None:
    settings = Settings(database_url=given)
    assert settings.database_url == "postgresql+psycopg://u:p@h/db"


def test_comma_separated_origins_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: the container sets CORS_ORIGINS as plain text, which failed to start the app.
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example")
    settings = Settings(database_url="sqlite:///:memory:")
    assert settings.cors_origins == ["https://a.example", "https://b.example"]


def test_render_external_url_is_trusted_for_websocket_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://natyasutra-hub.onrender.com/")
    settings = Settings(database_url="sqlite:///:memory:")
    assert "https://natyasutra-hub.onrender.com" in settings.cors_origins


@pytest.fixture
def spa(tmp_path: Path):  # type: ignore[no-untyped-def]
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>hub</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("do not serve", encoding="utf-8")
    settings = Settings(
        environment="test", database_url="sqlite:///:memory:", log_level="WARNING", frontend_dist=str(dist)
    )
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    app = create_app(settings=settings, engine=engine)
    with TestClient(app) as client:
        yield client
    engine.dispose()


def test_root_and_client_routes_serve_the_app_shell(spa: TestClient) -> None:
    assert "hub" in spa.get("/").text
    assert "hub" in spa.get("/projects/123/discussions").text


def test_built_assets_are_served(spa: TestClient) -> None:
    response = spa.get("/assets/app.js")
    assert response.status_code == 200
    assert "console.log" in response.text


def test_unknown_api_paths_are_real_404s_not_the_app_shell(spa: TestClient) -> None:
    response = spa.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert "hub" not in response.text


def test_path_traversal_cannot_read_files_outside_the_build(spa: TestClient) -> None:
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt"):
        response = spa.get(path)
        assert "do not serve" not in response.text, path


def test_health_still_answers_with_the_frontend_mounted(spa: TestClient) -> None:
    assert spa.get("/health").json()["status"] == "ok"
    assert spa.get("/ready").status_code == 200


def test_security_headers_are_set(spa: TestClient) -> None:
    headers = spa.get("/").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_operator_password_from_stdin_ignores_windows_line_endings(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    from scripts import manage

    monkeypatch.setattr("sys.stdin", io.StringIO("correct-horse-battery\r\n"))
    assert manage._password(from_stdin=True) == "correct-horse-battery"


def test_hsts_only_in_production(tmp_path: Path) -> None:
    settings = Settings(
        environment="production",
        database_url="postgresql+psycopg://u:p@h/db",
        bridge_token_secret=SECRET,
        log_level="WARNING",
    )
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    client = TestClient(create_app(settings=settings, engine=engine))
    assert "Strict-Transport-Security" in client.get("/health").headers
    engine.dispose()
