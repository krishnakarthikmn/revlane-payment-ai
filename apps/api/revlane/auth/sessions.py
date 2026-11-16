"""Session tokens: who is signed in, signed so they cannot be forged.

A token carries the tenant, the user and when it expires, with an HMAC under
`SESSION_SECRET`. It is kept in an HttpOnly cookie. The user's row is read on
every request, so removing a user or changing their role takes effect at once.
"""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

COOKIE = "revlane_session"
VERSION = 1


class SessionError(Exception):
    """The token is missing, forged, malformed or expired."""


@dataclass(frozen=True, slots=True)
class Principal:
    """The person a request is made by."""

    tenant_id: UUID
    user_id: UUID
    email: str
    name: str
    role: str  # viewer, approver or admin


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def issue(tenant_id: UUID, user_id: UUID, secret: str, now: datetime, lasts: timedelta) -> str:
    if not secret:
        raise SessionError("SESSION_SECRET is not set.")
    payload = {
        "v": VERSION,
        "t": str(tenant_id),
        "u": str(user_id),
        "e": int((now + lasts).timestamp()),
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).rstrip(b"=")
    return f"{body.decode()}.{_sign(body, secret)}"


def read(token: str | None, secret: str, now: datetime) -> tuple[UUID, UUID]:
    """The tenant and user a token names. Raises `SessionError` otherwise."""
    if not token or not secret:
        raise SessionError("Not signed in.")
    body, _, signature = token.partition(".")
    if not hmac.compare_digest(_sign(body.encode(), secret), signature):
        raise SessionError("Not signed in.")
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload["v"] != VERSION or int(payload["e"]) <= int(now.timestamp()):
            raise SessionError("The session has expired.")
        return UUID(payload["t"]), UUID(payload["u"])
    except (ValueError, KeyError, TypeError) as error:
        raise SessionError("Not signed in.") from error
