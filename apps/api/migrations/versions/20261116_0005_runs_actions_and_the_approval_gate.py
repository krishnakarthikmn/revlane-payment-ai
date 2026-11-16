"""Agent runs (the queue), actions, and the approval gate enforced in the database.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

APP_ROLE = "revlane_app"
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
RUN_STATUSES = "'queued', 'running', 'succeeded', 'escalated', 'failed', 'skipped'"

# Rule 6: nothing is carried out without an approval. Enforced here as well as
# in code, so no code path can get round it.
REQUIRE_APPROVAL = """
CREATE FUNCTION require_approval() RETURNS trigger AS $$
DECLARE
    needed integer;
    approved integer;
    rejected integer;
BEGIN
    SELECT CASE
               WHEN COALESCE((t.policy -> 'approvals' ->> 'two_person')::boolean, false) THEN 2
               ELSE 1
           END
      INTO needed
      FROM tenant t
     WHERE t.id = NEW.tenant_id;

    SELECT count(DISTINCT a.approver_id) FILTER (WHERE a.decision = 'approved'),
           count(*) FILTER (WHERE a.decision = 'rejected')
      INTO approved, rejected
      FROM approval a
     WHERE a.tenant_id = NEW.tenant_id
       AND a.proposal_id = NEW.proposal_id;

    IF rejected > 0 OR approved < COALESCE(needed, 1) THEN
        RAISE EXCEPTION 'No % without approval: the proposal has % of % approvals',
            TG_TABLE_NAME, approved, COALESCE(needed, 1)
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


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


def empty_json(name):
    return sa.Column(
        name, postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
    )


def upgrade():
    op.create_unique_constraint(
        op.f("uq_approval_tenant_id"), "approval", ["tenant_id", "proposal_id", "approver_id"]
    )

    op.create_table(
        "action",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("proposal_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        empty_json("result"),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "proposal_id"],
            ["proposal.tenant_id", "proposal.id"],
            name=op.f("fk_action_tenant_id_proposal"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_action_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action")),
        sa.UniqueConstraint("tenant_id", "proposal_id", name=op.f("uq_action_tenant_id")),
    )
    op.create_index(op.f("ix_action_tenant_id"), "action", ["tenant_id"])

    op.create_table(
        "agent_run",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("playbook", sa.Text(), nullable=False),
        sa.Column("playbook_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        empty_json("trigger"),
        sa.Column("as_of", sa.Date(), nullable=False),
        empty_json("checkpoint"),
        sa.Column("output", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.CheckConstraint(f"status IN ({RUN_STATUSES})", name="status_allowed"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_agent_run_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_run")),
    )
    op.create_index(op.f("ix_agent_run_tenant_id"), "agent_run", ["tenant_id"])
    op.create_index("ix_agent_run_queue", "agent_run", ["status", "created_at"])
    # One active run per tenant per playbook.
    op.create_index(
        "uq_agent_run_active",
        "agent_run",
        ["tenant_id", "playbook"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    secure("action")
    secure("agent_run")

    op.execute(REQUIRE_APPROVAL)
    for table in ("action", "claim"):
        op.execute(
            f"CREATE TRIGGER {table}_requires_approval BEFORE INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION require_approval()"
        )


def downgrade():
    for table in ("action", "claim"):
        op.execute(f"DROP TRIGGER {table}_requires_approval ON {table}")
    op.execute("DROP FUNCTION require_approval()")
    op.drop_table("agent_run")
    op.drop_table("action")
    op.drop_constraint(op.f("uq_approval_tenant_id"), "approval", type_="unique")
