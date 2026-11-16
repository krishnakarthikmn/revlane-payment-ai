"""Read-only, tenant-scoped views for Ask Revlane, and the role that may read them.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

CHAT_ROLE = "revlane_chat"
# Every view carries this predicate itself: the views run with their owner's
# rights, which row-level security does not restrict.
MINE = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
IST = "AT TIME ZONE 'Asia/Kolkata'"

VIEWS = {
    "chat_attempts": f"""
        SELECT a.tenant_id,
               a.provider,
               COALESCE(a.method, 'unknown') AS method,
               COALESCE(a.instrument_bank, 'unknown') AS instrument_bank,
               COALESCE(a.card_network, 'none') AS card_network,
               COALESCE(a.upi_app, 'none') AS upi_app,
               COALESCE(s.status, 'unknown') AS status,
               a.amount_minor,
               (a.first_seen_at {IST})::date AS paid_on,
               to_char(a.first_seen_at {IST}, 'YYYY-MM-DD') AS day,
               to_char(a.first_seen_at {IST}, 'YYYY-MM') AS month,
               to_char(a.first_seen_at {IST}, 'HH24') AS hour,
               EXISTS (
                   SELECT 1 FROM settlement_line l
                    WHERE l.tenant_id = a.tenant_id AND l.provider = a.provider
                      AND l.attempt_id = a.attempt_id AND l.line_type = 'capture'
               ) AS settled
          FROM payment_attempt a
          LEFT JOIN attempt_status s
            ON s.tenant_id = a.tenant_id AND s.provider = a.provider
           AND s.attempt_id = a.attempt_id
         WHERE a.tenant_id = {MINE}
    """,
    "chat_findings": f"""
        SELECT f.tenant_id,
               f.type AS leak,
               CASE WHEN f.type IN ('fee_above_contract', 'tax_on_refunded_fee',
                                    'fee_not_in_contract')
                    THEN 'fee_audit' ELSE 'reconciliation' END AS family,
               COALESCE(f.provider, 'unknown') AS provider,
               COALESCE(f.cause, 'unknown') AS cause,
               f.state,
               f.amount_minor,
               (f.created_at {IST})::date AS found_on,
               COALESCE(to_char(f.period_start, 'YYYY-MM'), 'unknown') AS month,
               -- A finding the engine closed itself was never a leak.
               NOT (f.state = 'written_off' AND COALESCE((
                   SELECT e.actor FROM finding_event e
                    WHERE e.tenant_id = f.tenant_id AND e.finding_id = f.id
                    ORDER BY e.occurred_at DESC, e.id DESC LIMIT 1), '') = 'engine') AS is_leak
          FROM finding f
         WHERE f.tenant_id = {MINE}
    """,
    "chat_settlement_lines": f"""
        SELECT l.tenant_id,
               l.provider,
               l.line_type,
               COALESCE(l.fee_type, 'none') AS fee_type,
               COALESCE(a.method, l.fee_type, 'unknown') AS method,
               COALESCE(a.card_network, 'none') AS card_network,
               l.gross_minor,
               l.fee_minor + l.tax_minor AS charged_minor,
               s.settled_on,
               to_char(s.settled_on, 'YYYY-MM') AS month
          FROM settlement_line l
          JOIN settlement s
            ON s.tenant_id = l.tenant_id AND s.provider = l.provider
           AND s.settlement_id = l.settlement_id
          LEFT JOIN payment_attempt a
            ON a.tenant_id = l.tenant_id AND a.provider = l.provider
           AND a.attempt_id = l.attempt_id
         WHERE l.tenant_id = {MINE}
    """,
    "chat_settlements": f"""
        SELECT s.tenant_id,
               s.provider,
               s.net_minor,
               s.settled_on,
               to_char(s.settled_on, 'YYYY-MM') AS month,
               EXISTS (
                   SELECT 1 FROM match_link k
                    WHERE k.tenant_id = s.tenant_id AND k.left_type = 'settlement'
                      AND k.left_id = s.provider || ':' || s.settlement_id
                      AND k.right_type = 'bank_credit' AND k.active
               ) AS matched
          FROM settlement s
         WHERE s.tenant_id = {MINE}
    """,
    "chat_refunds": f"""
        SELECT r.tenant_id,
               r.provider,
               r.type AS refund_type,
               r.amount_minor,
               (r.occurred_at {IST})::date AS refunded_on,
               to_char(r.occurred_at {IST}, 'YYYY-MM') AS month
          FROM refund r
         WHERE r.tenant_id = {MINE}
    """,
}


def ensure_chat_role():
    """Ask Revlane's queries run as this role. It can read the chat views and
    nothing else: no table, no write."""
    bind = op.get_bind()
    exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": CHAT_ROLE}
    ).scalar()
    if not exists:
        can_create = bind.execute(
            sa.text("SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user")
        ).scalar()
        if not can_create:
            raise RuntimeError(
                f"The role {CHAT_ROLE} does not exist and this user may not create it. "
                "Run `make db` first."
            )
        op.execute(f"CREATE ROLE {CHAT_ROLE} NOLOGIN")
    member = bind.execute(
        sa.text("SELECT pg_has_role(current_user, :role, 'MEMBER')"), {"role": CHAT_ROLE}
    ).scalar()
    if not member:
        try:
            with bind.begin_nested():
                op.execute(f"GRANT {CHAT_ROLE} TO CURRENT_USER")
        except sa.exc.DBAPIError as error:
            raise RuntimeError(
                f"This user cannot switch to the role {CHAT_ROLE}. Run `make db` first."
            ) from error


def upgrade():
    ensure_chat_role()
    op.execute(f"GRANT USAGE ON SCHEMA public TO {CHAT_ROLE}")
    for name, query in VIEWS.items():
        op.execute(f"CREATE VIEW {name} AS {query}")
        op.execute(f"GRANT SELECT ON {name} TO {CHAT_ROLE}")


def downgrade():
    for name in reversed(VIEWS):
        op.execute(f"DROP VIEW {name}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {CHAT_ROLE}")
