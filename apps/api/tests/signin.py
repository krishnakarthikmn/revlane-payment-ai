"""Signing a test client in, without going through the login form."""

from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from revlane.api.deps import current_principal
from revlane.auth import Principal
from revlane.db import models as m
from revlane.db.session import tenant_session


def sign_in(app: FastAPI, tenant_id: UUID, role: str = "admin") -> Principal:
    """Make every request to `app` come from a real user of the tenant with this role."""
    email = f"{role}@signed-in.example"
    with tenant_session(tenant_id) as session:
        session.execute(
            insert(m.AppUser)
            .values(tenant_id=tenant_id, email=email, name=f"Test {role}", role=role)
            .on_conflict_do_nothing(index_elements=["tenant_id", "email"])
        )
        user_id = session.scalar(
            select(m.AppUser.id).where(m.AppUser.tenant_id == tenant_id, m.AppUser.email == email)
        )
    assert user_id is not None
    principal = Principal(tenant_id, user_id, email, f"Test {role}", role)
    app.dependency_overrides[current_principal] = lambda: principal
    return principal
