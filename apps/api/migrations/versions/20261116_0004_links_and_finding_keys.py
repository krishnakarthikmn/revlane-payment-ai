"""Keys that make an engine run repeatable: one row per link, one finding per key.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    # The same pairing found by a second run is the same link.
    op.create_unique_constraint(
        op.f("uq_match_link_tenant_id"),
        "match_link",
        ["tenant_id", "left_type", "left_id", "right_type", "right_id"],
    )

    # Type, provider, cause and month: what makes a finding the same from run
    # to run. The sequence counts follow-on findings under one key.
    op.add_column(
        "finding",
        sa.Column(
            "finding_key",
            sa.Text(),
            server_default=sa.text("gen_random_uuid()::text"),
            nullable=False,
        ),
    )
    op.add_column(
        "finding", sa.Column("sequence", sa.Integer(), server_default="1", nullable=False)
    )
    op.create_unique_constraint(
        op.f("uq_finding_tenant_id_finding_key"),
        "finding",
        ["tenant_id", "finding_key", "sequence"],
    )

    # One contract row per account, method, card and start date, so loading
    # the same contract file twice adds nothing.
    op.create_unique_constraint(
        op.f("uq_fee_contract_tenant_id"),
        "fee_contract",
        [
            "tenant_id",
            "provider_account_id",
            "method",
            "card_network",
            "card_type",
            "effective_from",
        ],
        postgresql_nulls_not_distinct=True,
    )


def downgrade():
    op.drop_constraint(op.f("uq_fee_contract_tenant_id"), "fee_contract", type_="unique")
    op.drop_constraint(op.f("uq_finding_tenant_id_finding_key"), "finding", type_="unique")
    op.drop_column("finding", "sequence")
    op.drop_column("finding", "finding_key")
    op.drop_constraint(op.f("uq_match_link_tenant_id"), "match_link", type_="unique")
