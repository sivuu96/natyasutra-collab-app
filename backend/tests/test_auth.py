import datetime as dt

from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API, PASSWORD, csrf_headers, login, register
from sqlalchemy import select

from app.core.security import hash_password, hash_token, verify_password
from app.db.base import utcnow
from app.models import AuthSession, User


def test_password_hash_roundtrip_and_no_plaintext() -> None:
    hashed = hash_password(PASSWORD)
    assert PASSWORD not in hashed
    assert verify_password(hashed, PASSWORD)
    assert not verify_password(hashed, "wrong password here")
    assert not verify_password(None, PASSWORD)


def test_register_sets_session_cookie_and_returns_user(client: TestClient) -> None:
    body = register(client, "Owner@Example.com", "Owner")
    assert body["email"] == "owner@example.com"
    assert "ns_session" in client.cookies
    me = client.get(f"{API}/auth/me")
    assert me.status_code == 200
    assert me.json()["display_name"] == "Owner"


def test_short_password_rejected(client: TestClient) -> None:
    response = client.post(
        f"{API}/auth/register",
        json={"email": "a@example.com", "display_name": "A", "password": "short"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 422


def test_duplicate_registration_gives_generic_error(client: TestClient) -> None:
    register(client, "dup@example.com")
    response = client.post(
        f"{API}/auth/register",
        json={"email": "dup@example.com", "display_name": "X", "password": PASSWORD},
        headers=csrf_headers(client),
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Registration failed"


def test_registration_can_be_closed(app: FastAPI, client: TestClient) -> None:
    app.state.settings.allow_registration = False
    response = client.post(
        f"{API}/auth/register",
        json={"email": "b@example.com", "display_name": "B", "password": PASSWORD},
        headers=csrf_headers(client),
    )
    assert response.status_code == 403


def test_login_wrong_password_is_generic_401(client_factory) -> None:  # type: ignore[no-untyped-def]
    client = client_factory()
    register(client, "carol@example.com")
    other = client_factory()
    response = other.post(
        f"{API}/auth/login",
        json={"email": "carol@example.com", "password": "not-the-password"},
        headers=csrf_headers(other),
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"
    unknown = other.post(
        f"{API}/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}, headers=csrf_headers(other)
    )
    assert unknown.json() == response.json()


def test_login_rate_limited_per_email(client_factory) -> None:  # type: ignore[no-untyped-def]
    client = client_factory()
    register(client, "limited@example.com")
    attacker = client_factory()
    statuses = []
    for _ in range(12):
        statuses.append(
            attacker.post(
                f"{API}/auth/login",
                json={"email": "limited@example.com", "password": "wrong-password-x"},
                headers=csrf_headers(attacker),
            ).status_code
        )
    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429


def test_logout_revokes_server_side_session(client: TestClient, db) -> None:  # type: ignore[no-untyped-def]
    register(client, "dana@example.com")
    token = client.cookies["ns_session"]
    response = client.post(f"{API}/auth/logout", headers=csrf_headers(client))
    assert response.status_code == 204
    # Replaying the old cookie must fail even though the browser copy might still exist.
    client.cookies.set("ns_session", token)
    assert client.get(f"{API}/auth/me").status_code == 401
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    assert row is not None and row.revoked_at is not None


def test_session_token_stored_only_as_hash(client: TestClient, db) -> None:  # type: ignore[no-untyped-def]
    register(client, "erin@example.com")
    token = client.cookies["ns_session"]
    stored = db.scalars(select(AuthSession.token_hash)).all()
    assert token not in stored
    assert hash_token(token) in stored


def test_me_requires_session(client: TestClient) -> None:
    assert client.get(f"{API}/auth/me").status_code == 401


def test_state_changing_request_without_csrf_header_is_rejected(client: TestClient) -> None:
    register(client, "frank@example.com")
    response = client.post(f"{API}/teams", json={"name": "No CSRF"})
    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF check failed"


def test_csrf_header_must_match_cookie(client: TestClient) -> None:
    register(client, "gina@example.com")
    response = client.post(f"{API}/teams", json={"name": "Bad CSRF"}, headers={"X-CSRF-Token": "forged-value"})
    assert response.status_code == 403


def test_idle_expired_session_is_rejected(client: TestClient, db) -> None:  # type: ignore[no-untyped-def]
    register(client, "hank@example.com")
    row = db.scalar(select(AuthSession))
    assert row is not None
    row.idle_expires_at = utcnow() - dt.timedelta(minutes=1)
    db.commit()
    assert client.get(f"{API}/auth/me").status_code == 401


def test_absolute_expiry_cannot_be_extended_by_activity(client: TestClient, db) -> None:  # type: ignore[no-untyped-def]
    register(client, "ivy@example.com")
    row = db.scalar(select(AuthSession))
    assert row is not None
    row.absolute_expires_at = utcnow() - dt.timedelta(seconds=1)
    db.commit()
    assert client.get(f"{API}/auth/me").status_code == 401


def test_deactivated_user_cannot_use_existing_session(client: TestClient, db) -> None:  # type: ignore[no-untyped-def]
    register(client, "jay@example.com")
    user = db.scalar(select(User).where(User.email == "jay@example.com"))
    assert user is not None
    user.is_active = False
    db.commit()
    assert client.get(f"{API}/auth/me").status_code == 401


def test_login_issues_new_session(client_factory) -> None:  # type: ignore[no-untyped-def]
    client = client_factory()
    register(client, "kim@example.com", password=PASSWORD)
    fresh = client_factory()
    body = login(fresh, "kim@example.com")
    assert body["email"] == "kim@example.com"
    assert fresh.get(f"{API}/auth/me").status_code == 200
