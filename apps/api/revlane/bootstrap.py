"""Creating a tenant. The one job that runs as the schema owner: an
application session may not add or remove tenants."""

from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from revlane.auth.passwords import hash_password
from revlane.config import Settings
from revlane.db import models as m
from revlane.db.session import admin_session, tenant_session


def ensure_tenant(tenant_id: UUID, name: str) -> None:
    """Create the tenant if it is not there yet."""
    with admin_session() as session:
        session.execute(insert(m.Tenant).values(id=tenant_id, name=name).on_conflict_do_nothing())


def demo_user_id(tenant_id: UUID, email: str) -> UUID:
    """The same id every time the demo tenant is rebuilt, so a session outlives a reset."""
    return uuid5(tenant_id, email)


def ensure_demo_users(tenant_id: UUID, settings: Settings) -> None:
    """The demo tenant's two people, as in the prototype: Priya, an admin, and
    Arjun, a second approver. They can sign in once DEMO_USER_PASSWORD is set."""
    people = (
        (settings.demo_user_email, settings.demo_user_name, "admin"),
        (settings.demo_second_approver_email, settings.demo_second_approver_name, "approver"),
    )
    password = settings.demo_user_password
    with tenant_session(tenant_id) as session:
        for email, name, role in people:
            session.execute(
                insert(m.AppUser)
                .values(
                    tenant_id=tenant_id,
                    id=demo_user_id(tenant_id, email),
                    email=email,
                    name=name,
                    role=role,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "email"])
            )
        if password:
            waiting = session.scalars(
                select(m.AppUser).where(
                    m.AppUser.tenant_id == tenant_id,
                    m.AppUser.email.in_([email for email, _, _ in people]),
                    m.AppUser.password_hash.is_(None),
                )
            )
            for user in waiting:
                user.password_hash = hash_password(password)
