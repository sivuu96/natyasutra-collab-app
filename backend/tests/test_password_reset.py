"""Password reset by emailed link: the request, the one-time link, expiry, and what a reset ends."""

import datetime as dt
import re
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API, csrf_headers, register
from sqlalchemy import select, update

from app.db.base import utcnow
from app.models import PasswordReset, User

OLD = "original-password-1"
NEW = "brand-new-password-2"
FORGOT = f"{API}/auth/forgot-password"
RESET = f"{API}/auth/reset-password"


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send(self, *, to: str, subject: str, text: str) -> None:
        self.sent.append({"to": to, "subject": subject, "text": text})


@pytest.fixture
def client_factory(fake_app: FastAPI) -> Iterator[Callable[[], TestClient]]:
    """Clients for the same app the tests configure, so the settings and the email sender apply to every request."""
    clients: list[TestClient] = []

    def make() -> TestClient:
        client = TestClient(fake_app)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def _configure(app: FastAPI) -> RecordingSender:
    sender = RecordingSender()
    app.state.email_sender = sender
    app.state.settings.public_app_url = "https://hub.example"
    return sender


def _token_from(sender: RecordingSender) -> str:
    match = re.search(r"reset-password\?token=([A-Za-z0-9_\-]+)", sender.sent[-1]["text"])
    assert match, "the email must contain a reset link"
    return match.group(1)


def _post(client: TestClient, url: str, body: dict[str, str]) -> Any:
    """An anonymous write: fetch the CSRF cookie first, then echo it in the header, as the browser does."""
    if "ns_csrf" not in client.cookies:
        client.get(f"{API}/auth/me")
    return client.post(url, json=body, headers=csrf_headers(client))


def _login(client: TestClient, email: str, password: str) -> int:
    return _post(client, f"{API}/auth/login", {"email": email, "password": password}).status_code


def test_a_known_account_gets_a_link_that_resets_the_password(fake_app: FastAPI, client_factory: Any) -> None:
    sender = _configure(fake_app)
    owner = client_factory()
    register(owner, "reset-known@example.com", "Known", password=OLD)

    anonymous = client_factory()
    response = _post(anonymous, FORGOT, {"email": "reset-known@example.com"})
    assert response.status_code == 202
    assert len(sender.sent) == 1
    assert sender.sent[0]["to"] == "reset-known@example.com"
    assert sender.sent[0]["text"].count("https://hub.example/reset-password?token=") == 1

    token = _token_from(sender)
    done = _post(anonymous, RESET, {"token": token, "password": NEW})
    assert done.status_code == 204
    assert _login(anonymous, "reset-known@example.com", OLD) == 401
    assert _login(anonymous, "reset-known@example.com", NEW) == 200


def test_an_unknown_account_gets_the_same_answer_and_no_email(fake_app: FastAPI, client_factory: Any) -> None:
    sender = _configure(fake_app)
    anonymous = client_factory()
    known = register(anonymous, "reset-same@example.com", "Same", password=OLD)
    assert known["email"] == "reset-same@example.com"

    unknown = _post(anonymous, FORGOT, {"email": "nobody-here@example.com"})
    listed = _post(anonymous, FORGOT, {"email": "reset-same@example.com"})
    assert unknown.status_code == listed.status_code == 202
    assert unknown.json() == listed.json()
    assert [mail["to"] for mail in sender.sent] == ["reset-same@example.com"]


def test_a_newer_link_replaces_the_older_one(fake_app: FastAPI, client_factory: Any) -> None:
    sender = _configure(fake_app)
    anonymous = client_factory()
    register(anonymous, "reset-newer@example.com", "Newer", password=OLD)
    _post(anonymous, FORGOT, {"email": "reset-newer@example.com"})
    first = _token_from(sender)
    _post(anonymous, FORGOT, {"email": "reset-newer@example.com"})
    second = _token_from(sender)

    assert _post(anonymous, RESET, {"token": first, "password": NEW}).status_code == 400
    assert _post(anonymous, RESET, {"token": second, "password": NEW}).status_code == 204


def test_a_link_works_once(fake_app: FastAPI, client_factory: Any) -> None:
    sender = _configure(fake_app)
    anonymous = client_factory()
    register(anonymous, "reset-once@example.com", "Once", password=OLD)
    _post(anonymous, FORGOT, {"email": "reset-once@example.com"})
    token = _token_from(sender)

    assert _post(anonymous, RESET, {"token": token, "password": NEW}).status_code == 204
    again = _post(anonymous, RESET, {"token": token, "password": "third-password-3"})
    assert again.status_code == 400
    assert _login(anonymous, "reset-once@example.com", NEW) == 200


def test_an_expired_link_is_refused(fake_app: FastAPI, client_factory: Any) -> None:
    sender = _configure(fake_app)
    anonymous = client_factory()
    register(anonymous, "reset-expired@example.com", "Expired", password=OLD)
    _post(anonymous, FORGOT, {"email": "reset-expired@example.com"})
    token = _token_from(sender)
    with fake_app.state.session_factory() as db:
        db.execute(update(PasswordReset).values(expires_at=utcnow() - dt.timedelta(minutes=1)))
        db.commit()

    response = _post(anonymous, RESET, {"token": token, "password": NEW})
    assert response.status_code == 400
    assert _login(anonymous, "reset-expired@example.com", OLD) == 200


def test_a_wrong_token_is_refused_and_nothing_changes(fake_app: FastAPI, client_factory: Any) -> None:
    _configure(fake_app)
    anonymous = client_factory()
    register(anonymous, "reset-wrong@example.com", "Wrong", password=OLD)
    response = _post(anonymous, RESET, {"token": "not-a-real-token-value", "password": NEW})
    assert response.status_code == 400
    assert _login(anonymous, "reset-wrong@example.com", OLD) == 200


def test_a_reset_ends_every_session_of_the_account(fake_app: FastAPI, client_factory: Any) -> None:
    sender = _configure(fake_app)
    signed_in = client_factory()
    register(signed_in, "reset-sessions@example.com", "Sessions", password=OLD)
    assert signed_in.get(f"{API}/auth/me").status_code == 200

    anonymous = client_factory()
    _post(anonymous, FORGOT, {"email": "reset-sessions@example.com"})
    token = _token_from(sender)
    assert _post(anonymous, RESET, {"token": token, "password": NEW}).status_code == 204

    assert signed_in.get(f"{API}/auth/me").status_code == 401


def test_only_a_hash_of_the_token_is_stored(fake_app: FastAPI, client_factory: Any) -> None:
    sender = _configure(fake_app)
    anonymous = client_factory()
    register(anonymous, "reset-hash@example.com", "Hash", password=OLD)
    _post(anonymous, FORGOT, {"email": "reset-hash@example.com"})
    token = _token_from(sender)
    with fake_app.state.session_factory() as db:
        stored = db.scalars(select(PasswordReset.token_hash)).all()
        user = db.scalar(select(User).where(User.email == "reset-hash@example.com"))
        assert user is not None
    assert token not in stored
    assert len(stored) == 1 and len(stored[0]) == 64


def test_without_email_set_up_the_request_is_refused_clearly(fake_app: FastAPI, client_factory: Any) -> None:
    fake_app.state.email_sender = None
    fake_app.state.settings.public_app_url = "https://hub.example"
    anonymous = client_factory()
    response = _post(anonymous, FORGOT, {"email": "anyone@example.com"})
    assert response.status_code == 503


def test_a_short_new_password_is_rejected(fake_app: FastAPI, client_factory: Any) -> None:
    _configure(fake_app)
    anonymous = client_factory()
    response = _post(anonymous, RESET, {"token": "some-token-value", "password": "short"})
    assert response.status_code == 422


def test_requests_are_rate_limited_per_email(fake_app: FastAPI, client_factory: Any) -> None:
    _configure(fake_app)
    anonymous = client_factory()
    codes = [_post(anonymous, FORGOT, {"email": "ratelimit@example.com"}).status_code for _ in range(4)]
    assert codes[:3] == [202, 202, 202]
    assert codes[3] == 429
