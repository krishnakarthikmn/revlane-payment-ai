"""Signing in and out."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from revlane.api.deps import SignedIn
from revlane.auth import Principal, issue
from revlane.auth.accounts import authenticate
from revlane.auth.providers import NotConfigured, second_factor, single_sign_on
from revlane.auth.sessions import COOKIE
from revlane.config import get_settings

router = APIRouter(prefix="/auth")

# One answer for a wrong email and a wrong password alike.
REFUSED = "That email and password do not match an account."


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=500)
    # A second-factor code, once a tenant requires one.
    code: str | None = Field(default=None, max_length=20)


def person(principal: Principal) -> dict[str, Any]:
    return {
        "id": str(principal.user_id),
        "tenant_id": str(principal.tenant_id),
        "email": principal.email,
        "name": principal.name,
        "role": principal.role,
    }


@router.post("/login")
def login(body: Credentials, response: Response) -> dict[str, Any]:
    settings = get_settings()
    if not settings.session_secret:
        raise HTTPException(503, "Sign-in is not set up: SESSION_SECRET is empty.")
    principal = authenticate(body.email, body.password)
    if principal is None:
        raise HTTPException(401, REFUSED)
    factor = second_factor()
    if factor.required(principal.tenant_id, principal.user_id) and not factor.verify(
        principal.tenant_id, principal.user_id, body.code or ""
    ):
        raise HTTPException(401, "Enter the code from your authenticator.")
    lasts = timedelta(hours=settings.session_hours)
    token = issue(
        principal.tenant_id, principal.user_id, settings.session_secret, datetime.now(tz=UTC), lasts
    )
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(lasts.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    return person(principal)


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


@router.get("/me")
def me(principal: SignedIn) -> dict[str, Any]:
    return person(principal)


@router.get("/sso")
def sso(email: str) -> dict[str, str]:
    """Where to go to sign in with single sign-on. Not set up in the first version."""
    try:
        return {"redirect": single_sign_on().start(email)}
    except NotConfigured as error:
        raise HTTPException(404, str(error)) from error
