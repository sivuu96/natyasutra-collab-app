"""The email adapter: the request it sends to Brevo, sender parsing, and what a failure looks like."""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.services.email import BREVO_URL, BrevoSender, EmailError, build_sender, parse_sender


def _sender(handler) -> BrevoSender:  # type: ignore[no-untyped-def]
    return BrevoSender(
        "brevo-key-value",
        "NatyaSutra Hub",
        "no-reply@example.com",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_the_request_to_brevo_carries_the_key_sender_recipient_and_text() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["api-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"messageId": "x"})

    _sender(handler).send(to="user@example.com", subject="Reset", text="the link")

    assert seen["url"] == BREVO_URL
    assert seen["key"] == "brevo-key-value"
    assert seen["body"] == {
        "sender": {"name": "NatyaSutra Hub", "email": "no-reply@example.com"},
        "to": [{"email": "user@example.com"}],
        "subject": "Reset",
        "textContent": "the link",
    }


def test_a_refused_send_is_an_error_without_the_response_body() -> None:
    sender = _sender(lambda request: httpx.Response(401, text="secret detail"))
    with pytest.raises(EmailError, match="HTTP 401") as error:
        sender.send(to="user@example.com", subject="s", text="t")
    assert "secret detail" not in str(error.value)


def test_an_unreachable_provider_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(EmailError, match="unreachable"):
        _sender(handler).send(to="user@example.com", subject="s", text="t")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("NatyaSutra <no-reply@example.com>", ("NatyaSutra", "no-reply@example.com")),
        ("plain@example.com", ("NatyaSutra Hub", "plain@example.com")),
    ],
)
def test_the_sender_setting_is_split_into_name_and_address(value: str, expected: tuple[str, str]) -> None:
    assert parse_sender(value) == expected


def test_a_sender_setting_without_an_address_is_refused() -> None:
    with pytest.raises(ValueError):
        parse_sender("NatyaSutra")


def test_email_is_off_until_a_key_and_sender_are_set() -> None:
    assert build_sender(Settings(environment="test")) is None
    configured = Settings(
        environment="test",
        email_api_key=SecretStr("brevo-key-value"),
        email_from="NatyaSutra <no-reply@example.com>",
    )
    assert isinstance(build_sender(configured), BrevoSender)
