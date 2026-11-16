"""Database sessions.

`tenant_session` is the only way application code should reach the database.
It switches to the restricted `revlane_app` role and pins the tenant, so
row-level security applies to every statement. `admin_session` runs as the
schema owner, which row-level security does not restrict: it is for bootstrap
(creating a tenant), migrations and tests, and nothing else.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from functools import lru_cache
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, SessionTransaction

from revlane.config import get_settings

APP_ROLE = "revlane_app"
# Ask Revlane's queries run as this role: it can read the chat views and nothing else.
CHAT_ROLE = "revlane_chat"
CHAT_TIMEOUT_MS = 10_000
TENANT_SETTING = "app.tenant_id"


def exact_json(value: object) -> str:
    """JSON for a stored payload. A decimal is written as the number it is,
    digit for digit, and never passes through a float."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return (
            "{" + ",".join(f"{json.dumps(str(k))}:{exact_json(v)}" for k, v in value.items()) + "}"
        )
    if isinstance(value, list | tuple):
        return "[" + ",".join(exact_json(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False)


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        # Bound values never appear in logs or error messages.
        hide_parameters=True,
        json_serializer=exact_json,
        connect_args={"connect_timeout": 2},
    )


def _scope(connection: Connection, tenant_id: UUID | None) -> None:
    # Both settings are transaction-local, so a pooled connection never
    # carries one tenant's scope into the next request.
    connection.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
    connection.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": TENANT_SETTING, "value": "" if tenant_id is None else str(tenant_id)},
    )


def _scoped_session(tenant_id: UUID | None) -> Session:
    session = Session(get_engine(), expire_on_commit=False)

    # Re-apply the scope at the start of every transaction: after a commit in
    # the middle of a session, the next statement is still tenant-scoped.
    @event.listens_for(session, "after_begin")
    def apply_scope(_: Session, __: SessionTransaction, connection: Connection) -> None:
        _scope(connection, tenant_id)

    return session


def _run(session: Session) -> Iterator[Session]:
    """Commit on success, roll back on any error, always close."""
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def tenant_session(tenant_id: UUID) -> Iterator[Session]:
    """A session that can read and write one tenant's rows and no others."""
    if not isinstance(tenant_id, UUID):
        raise TypeError("tenant_session needs the tenant's UUID")
    yield from _run(_scoped_session(tenant_id))


@contextmanager
def unscoped_app_session() -> Iterator[Session]:
    """The restricted role with no tenant pinned: it sees no rows at all."""
    yield from _run(_scoped_session(None))


@contextmanager
def admin_session() -> Iterator[Session]:
    """Schema-owner session, not restricted by row-level security.

    Only for bootstrap, migrations and tests. Never use it to serve a request.
    """
    with Session(get_engine(), expire_on_commit=False) as session, session.begin():
        yield session


@contextmanager
def chat_session(tenant_id: UUID) -> Iterator[Session]:
    """A read-only session for Ask Revlane: one tenant, the chat views only,
    and a time limit on every statement. Nothing is ever committed."""
    if not isinstance(tenant_id, UUID):
        raise TypeError("chat_session needs the tenant's UUID")
    session = Session(get_engine(), expire_on_commit=False)

    @event.listens_for(session, "after_begin")
    def apply_scope(_: Session, __: SessionTransaction, connection: Connection) -> None:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text(f"SET LOCAL statement_timeout = {CHAT_TIMEOUT_MS}"))
        connection.execute(text(f"SET LOCAL ROLE {CHAT_ROLE}"))
        connection.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": TENANT_SETTING, "value": str(tenant_id)},
        )

    try:
        yield session
    finally:
        session.rollback()
        session.close()
