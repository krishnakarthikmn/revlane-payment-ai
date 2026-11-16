"""What every route shares: who is asking, for which tenant, and what they may do."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request

from revlane.auth import Principal, SessionError, read
from revlane.auth.accounts import load
from revlane.auth.sessions import COOKIE
from revlane.config import get_settings

NO_TENANT = "The demo tenant does not exist yet. Run `make ingest`."
SIGN_IN = "Sign in to continue."
ACTS = ("approver", "admin")
ADMINISTERS = ("admin",)


def current_principal(request: Request) -> Principal:
    """The signed-in person, from the session cookie. 401 if there is none."""
    try:
        tenant_id, user_id = read(
            request.cookies.get(COOKIE), get_settings().session_secret, datetime.now(tz=UTC)
        )
    except SessionError as error:
        raise HTTPException(401, SIGN_IN) from error
    principal = load(tenant_id, user_id)
    if principal is None:
        raise HTTPException(401, SIGN_IN)
    return principal


SignedIn = Annotated[Principal, Depends(current_principal)]


def current_tenant(principal: SignedIn) -> UUID:
    """The tenant of the signed-in person. Nobody chooses a tenant in a request."""
    return principal.tenant_id


Tenant = Annotated[UUID, Depends(current_tenant)]
# A signed-in person's tenant exists: they have a row in it.
ExistingTenant = Tenant


def can_act(principal: SignedIn) -> Principal:
    """Approvers and admins: decide proposals, upload files, run the audit."""
    if principal.role not in ACTS:
        raise HTTPException(403, "Your role can view this, but not change it.")
    return principal


def can_administer(principal: SignedIn) -> Principal:
    """Admins: contracts, rules, agent settings, credentials, erasure, export."""
    if principal.role not in ADMINISTERS:
        raise HTTPException(403, "Only an admin can do this.")
    return principal


Acting = Annotated[Principal, Depends(can_act)]
Administering = Annotated[Principal, Depends(can_administer)]
# For routes that need the role and not the person.
ACT = [Depends(can_act)]
ADMINISTER = [Depends(can_administer)]
