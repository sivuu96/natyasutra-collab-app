"""Short-lived bridge access tokens (JWT, HS256) and device secret helpers.

Access tokens identify a device only. They are verified on every connection, and the device's
revocation state is checked against the database, so revoking a device cuts access immediately.
"""

import datetime as dt
import secrets
import uuid

import jwt

from app.db.base import utcnow

ALGORITHM = "HS256"
ISSUER = "collab-hub"
AUDIENCE = "collab-bridge"


class InvalidBridgeToken(Exception):
    pass


def issue_bridge_token(secret: str, device_id: uuid.UUID, minutes: int) -> tuple[str, dt.datetime]:
    issued = utcnow()
    expires = issued + dt.timedelta(minutes=minutes)
    token = jwt.encode(
        {"sub": str(device_id), "iss": ISSUER, "aud": AUDIENCE, "iat": issued, "exp": expires, "typ": "bridge"},
        secret,
        algorithm=ALGORITHM,
    )
    return token, expires


def verify_bridge_token(secret: str, token: str) -> uuid.UUID:
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["sub", "exp", "iat", "aud", "iss"]},
        )
    except jwt.PyJWTError as error:
        raise InvalidBridgeToken("invalid or expired bridge token") from error
    if claims.get("typ") != "bridge":
        raise InvalidBridgeToken("wrong token type")
    try:
        return uuid.UUID(str(claims["sub"]))
    except ValueError as error:
        raise InvalidBridgeToken("malformed subject") from error


def new_device_secret() -> str:
    return secrets.token_urlsafe(32)


def new_pairing_code() -> str:
    """8 characters from an alphabet without look-alikes (no 0/O, 1/I/L), shown as XXXX-XXXX."""
    alphabet = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalise_pairing_code(code: str) -> str:
    return code.strip().upper().replace("-", "").replace(" ", "")
