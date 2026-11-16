"""Rule 4: every tenant table has `tenant_id` and row-level security.

These tests are written against the catalogue and the model metadata, so a
table added later is covered without touching this file - and fails here if it
is missing a policy, a grant, or a row in `seed_tenant`.
"""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Table, func, select, text
from sqlalchemy.exc import DBAPIError

from revlane.db import models as m
from revlane.db.base import Base
from revlane.db.session import admin_session, tenant_session, unscoped_app_session
from tests.seed import TODAY, seed_tenant

TABLES: list[Table] = list(Base.metadata.sorted_tables)
TABLE_NAMES = [table.name for table in TABLES]

pytestmark = pytest.mark.usefixtures("database")


def tenant_column(table: Table) -> str:
    return "id" if table.name == "tenant" else "tenant_id"


@pytest.fixture(scope="module")
def tenants(database: None) -> Iterator[tuple[UUID, UUID]]:
    with admin_session() as session:
        a = seed_tenant(session, f"a-{uuid4().hex[:8]}")
        b = seed_tenant(session, f"b-{uuid4().hex[:8]}")
    yield a, b


def test_every_table_in_the_database_is_a_model() -> None:
    with admin_session() as session:
        in_database = set(
            session.scalars(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
        )

    assert in_database == set(TABLE_NAMES)


@pytest.mark.parametrize("table", TABLES, ids=TABLE_NAMES)
def test_table_has_a_tenant_column_and_row_level_security(table: Table) -> None:
    assert tenant_column(table) in table.columns

    with admin_session() as session:
        enabled = session.scalar(
            text("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(:name)"),
            {"name": f'public."{table.name}"'},
        )
        policies = session.execute(
            text("SELECT qual, with_check FROM pg_policies WHERE tablename = :name"),
            {"name": table.name},
        ).all()

    assert enabled is True
    assert len(policies) == 1
    qual, with_check = policies[0]
    assert "app.tenant_id" in qual
    assert "app.tenant_id" in with_check


@pytest.mark.parametrize("table", TABLES, ids=TABLE_NAMES)
def test_tenant_a_cannot_read_tenant_b(table: Table, tenants: tuple[UUID, UUID]) -> None:
    a, b = tenants
    column = table.columns[tenant_column(table)]

    with admin_session() as session:
        seeded: dict[UUID, int] = dict(
            session.execute(
                select(column, func.count()).where(column.in_([a, b])).group_by(column)
            ).all()
        )
    # The check below means nothing unless both tenants have rows in this table.
    assert seeded.get(a, 0) >= 1, f"seed_tenant puts no row in {table.name}"
    assert seeded.get(b, 0) >= 1

    with tenant_session(a) as session:
        visible = set(session.scalars(select(column)))
        asked_for_b = session.scalar(select(func.count()).select_from(table).where(column == b))

    assert visible == {a}
    assert asked_for_b == 0


@pytest.mark.parametrize("table", TABLES, ids=TABLE_NAMES)
def test_a_session_with_no_tenant_sees_nothing(table: Table, tenants: tuple[UUID, UUID]) -> None:
    with unscoped_app_session() as session:
        assert session.scalar(select(func.count()).select_from(table)) == 0


def test_tenant_a_cannot_change_or_delete_tenant_b_rows(tenants: tuple[UUID, UUID]) -> None:
    a, b = tenants

    with tenant_session(a) as session:
        updated = session.execute(
            text("UPDATE bank_credit SET narration = 'taken' WHERE tenant_id = :b"), {"b": b}
        )
        deleted = session.execute(text("DELETE FROM finding WHERE tenant_id = :b"), {"b": b})
        assert updated.rowcount == 0  # type: ignore[attr-defined]
        assert deleted.rowcount == 0  # type: ignore[attr-defined]

    with tenant_session(b) as session:
        assert session.scalar(select(m.BankCredit.narration)) is None
        assert session.scalar(select(func.count()).select_from(m.Finding)) == 1


def test_tenant_a_cannot_write_a_row_for_tenant_b(tenants: tuple[UUID, UUID]) -> None:
    a, b = tenants

    with pytest.raises(DBAPIError, match="row-level security"), tenant_session(a) as session:
        session.add(
            m.BankCredit(
                tenant_id=b,
                account_ref="hdfc-1",
                line_hash="h2",
                amount_minor=1,
                credited_on=TODAY,
            )
        )
        session.flush()


def test_tenant_a_cannot_move_its_row_to_tenant_b(tenants: tuple[UUID, UUID]) -> None:
    a, b = tenants

    with pytest.raises(DBAPIError, match="row-level security"), tenant_session(a) as session:
        session.execute(text("UPDATE bank_credit SET tenant_id = :b"), {"b": b})


def test_a_child_row_cannot_point_at_another_tenants_parent(tenants: tuple[UUID, UUID]) -> None:
    a, b = tenants
    with tenant_session(b) as session:
        finding_of_b = session.scalar(select(m.Finding.id))

    with pytest.raises(DBAPIError, match="foreign key"), tenant_session(a) as session:
        session.add(
            m.FindingEvent(tenant_id=a, finding_id=finding_of_b, to_state="claimed", actor="x")
        )
        session.flush()


def test_scope_survives_a_commit_inside_the_session(tenants: tuple[UUID, UUID]) -> None:
    a, _ = tenants

    with tenant_session(a) as session:
        session.commit()
        visible = set(session.scalars(select(m.Tenant.id)))
        role = session.scalar(text("SELECT current_user"))

    assert visible == {a}
    assert role == "revlane_app"


def test_scope_does_not_leak_into_the_next_session(tenants: tuple[UUID, UUID]) -> None:
    a, b = tenants

    with tenant_session(a) as session:
        session.scalar(select(func.count()).select_from(m.Tenant))
    with tenant_session(b) as session:
        assert set(session.scalars(select(m.Tenant.id))) == {b}
    with admin_session() as session:
        assert session.scalar(text("SELECT current_setting('app.tenant_id', true)")) in ("", None)


def test_the_application_role_is_not_privileged() -> None:
    with admin_session() as session:
        row = session.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles "
                "WHERE rolname = 'revlane_app'"
            )
        ).one()

    assert tuple(row) == (False, False, False)


@pytest.mark.parametrize("table", m.APPEND_ONLY_TABLES)
def test_append_only_tables_refuse_update_and_delete(
    table: str, tenants: tuple[UUID, UUID]
) -> None:
    a, _ = tenants

    for statement in (f"UPDATE {table} SET updated_at = now()", f"DELETE FROM {table}"):
        with pytest.raises(DBAPIError, match="permission denied"), tenant_session(a) as session:
            session.execute(text(statement))


def test_application_sessions_cannot_create_or_delete_tenants(tenants: tuple[UUID, UUID]) -> None:
    a, _ = tenants

    with pytest.raises(DBAPIError, match="permission denied"), tenant_session(a) as session:
        session.execute(text("DELETE FROM tenant"))
    with pytest.raises(DBAPIError, match="permission denied"), tenant_session(a) as session:
        session.add(m.Tenant(name="another"))
        session.flush()


def test_tenant_session_refuses_anything_but_a_uuid() -> None:
    with pytest.raises(TypeError), tenant_session("not-a-uuid"):  # type: ignore[arg-type]
        pass
