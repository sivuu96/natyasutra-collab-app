"""Sending email. One provider: Resend, through its HTTP API.

Nothing here logs a recipient or a message body. A reset link is a credential.
"""

import logging
from typing import Protocol

import httpx

from app.core.config import Settings

log = logging.getLogger("collab.email")

RESEND_URL = "https://api.resend.com/emails"


class EmailError(Exception):
    """The provider refused or could not be reached."""


class EmailSender(Protocol):
    def send(self, *, to: str, subject: str, text: str) -> None: ...


class ResendSender:
    def __init__(self, api_key: str, sender: str, http: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._sender = sender
        self._http = http or httpx.Client(timeout=httpx.Timeout(15.0))

    def send(self, *, to: str, subject: str, text: str) -> None:
        try:
            response = self._http.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"from": self._sender, "to": [to], "subject": subject, "text": text},
            )
        except httpx.HTTPError as error:
            raise EmailError(f"email provider unreachable: {type(error).__name__}") from error
        if response.status_code >= 300:
            raise EmailError(f"email provider returned HTTP {response.status_code}")


def build_sender(settings: Settings) -> EmailSender | None:
    """The configured sender, or None when email is not set up."""
    if settings.email_api_key is None or not settings.email_from:
        return None
    return ResendSender(settings.email_api_key.get_secret_value(), settings.email_from)


__all__ = ["EmailError", "EmailSender", "ResendSender", "build_sender"]
