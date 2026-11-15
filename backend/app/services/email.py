"""Sending email. One provider: Brevo, through its transactional email API.

Brevo accepts any recipient from a sender address that has been verified in the Brevo dashboard. No domain is needed.
Nothing here logs a recipient, a message body or the response, because a reset link is a credential.
"""

import logging
from email.utils import parseaddr
from typing import Protocol

import httpx

from app.core.config import Settings

log = logging.getLogger("collab.email")

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
DEFAULT_SENDER_NAME = "NatyaSutra Hub"


class EmailError(Exception):
    """The provider refused or could not be reached."""


class EmailSender(Protocol):
    def send(self, *, to: str, subject: str, text: str) -> None: ...


class BrevoSender:
    def __init__(self, api_key: str, sender_name: str, sender_email: str, http: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._sender = {"name": sender_name, "email": sender_email}
        self._http = http or httpx.Client(timeout=httpx.Timeout(15.0))

    def send(self, *, to: str, subject: str, text: str) -> None:
        try:
            response = self._http.post(
                BREVO_URL,
                headers={"api-key": self._api_key, "accept": "application/json"},
                json={"sender": self._sender, "to": [{"email": to}], "subject": subject, "textContent": text},
            )
        except httpx.HTTPError as error:
            raise EmailError(f"email provider unreachable: {type(error).__name__}") from error
        if response.status_code >= 300:
            raise EmailError(f"email provider returned HTTP {response.status_code}")


def parse_sender(value: str) -> tuple[str, str]:
    """`NatyaSutra <no-reply@x.com>` or a bare address -> (name, address)."""
    name, address = parseaddr(value)
    if "@" not in address:
        raise ValueError("EMAIL_FROM must be an email address, optionally with a name in angle brackets")
    return (name or DEFAULT_SENDER_NAME), address


def build_sender(settings: Settings) -> EmailSender | None:
    """The configured sender, or None when email is not set up."""
    if settings.email_api_key is None or not settings.email_from:
        return None
    name, address = parse_sender(settings.email_from)
    return BrevoSender(settings.email_api_key.get_secret_value(), name, address)


__all__ = ["BrevoSender", "EmailError", "EmailSender", "build_sender", "parse_sender"]
