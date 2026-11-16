"""Derived attempt status, alerts, and the keys that make ingestion repeatable.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_ROLE = "revlane_app"
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
STATUSES = (
    "'succeeded', 'authorized', 'failed', 'pending', 'abandoned', "
    "'auto_refunded', 'held_for_review', 'unknown'"
)


def timestamps():
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
    ]


def secure(table):
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{table}" '
        f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO {APP_ROLE}')


def upgrade():
    # The same provider report delivered twice is one event.
    op.add_column("attempt_event", sa.Column("event_key", sa.Text(), nullable=True))
    op.execute("UPDATE attempt_event SET event_key = id::text")
    op.alter_column("attempt_event", "event_key", nullable=False)
    op.create_unique_constraint(
        op.f("uq_attempt_event_tenant_id"),
        "attempt_event",
        ["tenant_id", "provider", "attempt_id", "event_key"],
    )

    # A statement line has no id of its own.
    op.add_column("bank_credit", sa.Column("line_hash", sa.Text(), nullable=True))
    op.execute("UPDATE bank_credit SET line_hash = id::text")
    op.alter_column("bank_credit", "line_hash", nullable=False)
    op.create_unique_constraint(
        op.f("uq_bank_credit_tenant_id"), "bank_credit", ["tenant_id", "account_ref", "line_hash"]
    )

    op.create_table(
        "attempt_status",
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("attempt_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("provider_status", sa.Text(), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changes_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("derived_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="status_allowed"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "provider", "attempt_id"],
            ["payment_attempt.tenant_id", "payment_attempt.provider", "payment_attempt.attempt_id"],
            name=op.f("fk_attempt_status_tenant_id_payment_attempt"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_attempt_status_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "provider", "attempt_id", name=op.f("pk_attempt_status")
        ),
    )
    op.create_index("ix_attempt_status_status", "attempt_status", ["tenant_id", "status"])
    op.create_index("ix_attempt_status_changes_at", "attempt_status", ["tenant_id", "changes_at"])
    op.create_index(op.f("ix_attempt_status_tenant_id"), "attempt_status", ["tenant_id"])

    op.create_table(
        "alert",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("record_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("state", sa.Text(), server_default="open", nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
        sa.CheckConstraint("kind IN ('unmapped_status')", name="kind_allowed"),
        sa.CheckConstraint("state IN ('open', 'resolved')", name="state_allowed"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_alert_tenant_id_tenant"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert")),
        sa.UniqueConstraint(
            "tenant_id", "kind", "provider", "subject", name=op.f("uq_alert_tenant_id")
        ),
    )
    op.create_index(op.f("ix_alert_tenant_id"), "alert", ["tenant_id"])

    secure("attempt_status")
    secure("alert")


def downgrade():
    op.drop_table("alert")
    op.drop_table("attempt_status")
    op.drop_constraint(op.f("uq_bank_credit_tenant_id"), "bank_credit", type_="unique")
    op.drop_column("bank_credit", "line_hash")
    op.drop_constraint(op.f("uq_attempt_event_tenant_id"), "attempt_event", type_="unique")
    op.drop_column("attempt_event", "event_key")
