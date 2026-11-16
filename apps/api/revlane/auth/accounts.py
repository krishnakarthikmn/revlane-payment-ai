"""Finding the account behind an email when someone signs in."""

from uuid import UUID

from sqlalchemy import func, select

from revlane.auth.passwords import verify_password
from revlane.auth.sessions import Principal
from revlane.db import models as m
from revlane.db.session import admin_session, tenant_session


def authenticate(email: str, password: str) -> Principal | None:
    """The person with this email and password, or None.

    An email belongs to a tenant, and nobody is signed in yet, so this one
    lookup reads across tenants. It returns nothing unless the password is
    right, and does the same work whether or not the account exists.
    """
    address = email.strip().lower()
    with admin_session() as session:
        users = list(
            session.scalars(select(m.AppUser).where(func.lower(m.AppUser.email) == address))
        )
    # The same email in two tenants cannot be told apart by a password alone.
    user = users[0] if len(users) == 1 else None
    if not verify_password(password, user.password_hash if user else None) or user is None:
        return None
    return Principal(user.tenant_id, user.id, user.email, user.name, user.role)


def load(tenant_id: UUID, user_id: UUID) -> Principal | None:
    """The signed-in person as they are now: a removed user is signed out, and
    a changed role applies from the next request."""
    with tenant_session(tenant_id) as session:
        user = session.scalar(
            select(m.AppUser).where(m.AppUser.tenant_id == tenant_id, m.AppUser.id == user_id)
        )
    if user is None:
        return None
    return Principal(user.tenant_id, user.id, user.email, user.name, user.role)
