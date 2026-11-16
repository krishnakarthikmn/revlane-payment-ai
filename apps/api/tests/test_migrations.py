"""Migrations apply and roll back cleanly."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from revlane.db.base import Base
from revlane.db.session import admin_session, get_engine
from tests.conftest import alembic_config

pytestmark = pytest.mark.usefixtures("database")


def public_objects() -> dict[str, int]:
    with admin_session() as session:
        return {
            "tables": session.scalar(
                text(
                    "SELECT count(*) FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
            or 0,
            "policies": session.scalar(text("SELECT count(*) FROM pg_policies")) or 0,
            "extensions": session.scalar(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'pgcrypto'")
            )
            or 0,
        }


def test_downgrade_removes_everything_and_upgrade_restores_it() -> None:
    config = alembic_config()
    tables = len(Base.metadata.tables)
    assert public_objects() == {"tables": tables, "policies": tables, "extensions": 1}

    try:
        command.downgrade(config, "base")
        assert public_objects() == {"tables": 0, "policies": 0, "extensions": 0}
    finally:
        command.upgrade(config, "head")

    assert public_objects() == {"tables": tables, "policies": tables, "extensions": 1}


def test_migrations_match_the_models() -> None:
    """A model change without a migration fails here."""
    with get_engine().connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        difference = compare_metadata(context, Base.metadata)

    assert difference == []
