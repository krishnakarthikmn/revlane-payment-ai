"""The unified payments model. See docs/technical-design.md, section 4.

Every table carries `tenant_id` and is covered by row-level security (the
policies live in the migration). Amounts are integer minor units in columns
ending `_minor`. Provider records keep their raw payload.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import UUID as SAUUID
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from revlane.db.base import Base

PROVIDERS = ("razorpay", "payu", "cashfree", "marketplace", "bank", "orders")
ROLES = ("viewer", "approver", "admin")
EVENT_SOURCES = ("api", "webhook", "file")
REFUND_TYPES = ("merchant", "automatic")
LINE_TYPES = ("capture", "refund", "chargeback", "reversal", "fee_adjustment")
FINDING_TYPES = (
    "paid_auto_refunded",
    "settled_short",
    "captured_not_settled",
    "fee_above_contract",
    "tax_on_refunded_fee",
    "fee_not_in_contract",
)
FINDING_STATES = ("lost", "recoverable", "claimed", "recovered", "written_off")
PROPOSAL_STATUSES = ("pending", "approved", "rejected", "expired")
DECISIONS = ("approved", "rejected")
CLAIM_STATUSES = ("drafted", "sent_by_merchant", "paid", "refused")
AUDIT_KINDS = ("connector_call", "model_call", "tool_call", "approval", "action")
LINK_CREATORS = ("engine", "user")
RUN_STATUSES = ("queued", "running", "succeeded", "escalated", "failed", "skipped")
# A run in one of these is still in the queue: one per tenant per playbook.
ACTIVE_RUN_STATUSES = ("queued", "running")
# The canonical status vocabulary. See docs/technical-design.md, section 4.
ATTEMPT_STATUSES = (
    "succeeded",
    "authorized",
    "failed",
    "pending",
    "abandoned",
    "auto_refunded",
    "held_for_review",
    "unknown",
)
ALERT_KINDS = ("unmapped_status",)
ALERT_STATES = ("open", "resolved")

# Tables an application session may only add to, never change or delete from.
APPEND_ONLY_TABLES = ("attempt_event", "finding_event", "audit_log")


def one_of(column: str, values: tuple[str, ...]) -> CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=f"{column}_allowed")


def uuid_pk() -> Mapped[UUID]:
    return mapped_column(SAUUID, primary_key=True, server_default=text("gen_random_uuid()"))


def money(nullable: bool = False) -> Mapped[int]:
    return mapped_column(BigInteger, nullable=nullable)


def raw_payload() -> Mapped[dict[str, Any]]:
    return mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TenantScoped(Timestamps):
    tenant_id: Mapped[UUID] = mapped_column(
        SAUUID, ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )


# --- Setup -------------------------------------------------------------------


class Tenant(Timestamps, Base):
    __tablename__ = "tenant"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False, server_default="in")
    policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class AppUser(TenantScoped, Base):
    """A person who signs in to the console."""

    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email"),
        UniqueConstraint("tenant_id", "id"),
        one_of("role", ROLES),
    )

    id: Mapped[UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="viewer")
    password_hash: Mapped[str | None] = mapped_column(Text)


class AgentSetting(TenantScoped, Base):
    """Autonomy level and kill switch, per playbook per tenant."""

    __tablename__ = "agent_setting"
    __table_args__ = (
        UniqueConstraint("tenant_id", "playbook"),
        CheckConstraint("autonomy_level BETWEEN 0 AND 3", name="autonomy_level_range"),
    )

    id: Mapped[UUID] = uuid_pk()
    playbook: Mapped[str] = mapped_column(Text, nullable=False)
    autonomy_level: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class VaultSecret(TenantScoped, Base):
    """Development-only vault backend: values encrypted before they are stored."""

    __tablename__ = "vault_secret"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class ProviderAccount(TenantScoped, Base):
    __tablename__ = "provider_account"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "account_ref"),
        UniqueConstraint("tenant_id", "id"),
        one_of("provider", PROVIDERS),
    )

    id: Mapped[UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    account_ref: Mapped[str] = mapped_column(Text, nullable=False)
    credential_ref: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="connected")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FeeContract(TenantScoped, Base):
    __tablename__ = "fee_contract"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "provider_account_id"],
            ["provider_account.tenant_id", "provider_account.id"],
            ondelete="CASCADE",
        ),
        # One row per account, method, card and start date.
        UniqueConstraint(
            "tenant_id",
            "provider_account_id",
            "method",
            "card_network",
            "card_type",
            "effective_from",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    provider_account_id: Mapped[UUID] = mapped_column(SAUUID, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    card_network: Mapped[str | None] = mapped_column(Text)
    card_type: Mapped[str | None] = mapped_column(Text)
    rate_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    fixed_fee_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    tax_rate_bps: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1800")
    tax_on_refunded_fee: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    allowed_fee_types: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)


# --- Money movement ----------------------------------------------------------


class Order(TenantScoped, Base):
    __tablename__ = "order"
    __table_args__ = (UniqueConstraint("tenant_id", "merchant_order_id"),)

    id: Mapped[UUID] = uuid_pk()
    merchant_order_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount_minor: Mapped[int] = money()
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="INR")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    fulfilment_status: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text, nullable=False, server_default="web")
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw: Mapped[dict[str, Any]] = raw_payload()


class PaymentAttempt(TenantScoped, Base):
    __tablename__ = "payment_attempt"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "provider", "attempt_id"),
        ForeignKeyConstraint(
            ["tenant_id", "provider_account_id"],
            ["provider_account.tenant_id", "provider_account.id"],
        ),
        Index("ix_payment_attempt_order", "tenant_id", "merchant_order_id"),
        one_of("provider", PROVIDERS),
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider_account_id: Mapped[UUID] = mapped_column(SAUUID, nullable=False)
    merchant_order_id: Mapped[str | None] = mapped_column(Text)
    amount_minor: Mapped[int] = money()
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="INR")
    method: Mapped[str | None] = mapped_column(Text)
    instrument_bank: Mapped[str | None] = mapped_column(Text)
    card_network: Mapped[str | None] = mapped_column(Text)
    card_type: Mapped[str | None] = mapped_column(Text)
    upi_app: Mapped[str | None] = mapped_column(Text)
    # A keyed hash of the shopper's normalised phone or email. Never the contact itself.
    customer_token: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw: Mapped[dict[str, Any]] = raw_payload()


class AttemptEvent(TenantScoped, Base):
    """Append-only status history. An attempt's status is derived from these."""

    __tablename__ = "attempt_event"
    __table_args__ = (
        Index("ix_attempt_event_attempt", "tenant_id", "provider", "attempt_id", "occurred_at"),
        # The same report delivered twice is one event.
        UniqueConstraint("tenant_id", "provider", "attempt_id", "event_key"),
        one_of("provider", PROVIDERS),
        one_of("source", EVENT_SOURCES),
    )

    id: Mapped[UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider_status: Mapped[str] = mapped_column(Text, nullable=False)
    provider_error_code: Mapped[str | None] = mapped_column(Text)
    provider_stage: Mapped[str | None] = mapped_column(Text)
    # Status, stage and time of the report: what makes one event the same as another.
    event_key: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    raw: Mapped[dict[str, Any]] = raw_payload()


class AttemptStatus(TenantScoped, Base):
    """The canonical status of each attempt, derived from its events.

    Never the only truth: `revlane.engine.status` rebuilds every row from
    `attempt_event`, and does so again whenever a rule changes.
    """

    __tablename__ = "attempt_status"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "provider", "attempt_id"),
        ForeignKeyConstraint(
            ["tenant_id", "provider", "attempt_id"],
            ["payment_attempt.tenant_id", "payment_attempt.provider", "payment_attempt.attempt_id"],
            ondelete="CASCADE",
        ),
        Index("ix_attempt_status_status", "tenant_id", "status"),
        Index("ix_attempt_status_changes_at", "tenant_id", "changes_at"),
        one_of("status", ATTEMPT_STATUSES),
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    # The provider's own word in the event that decided the status.
    provider_status: Mapped[str] = mapped_column(Text, nullable=False)
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When a clock rule will change this status with no new event, if one will.
    changes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rule_version: Mapped[str] = mapped_column(Text, nullable=False)
    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Refund(TenantScoped, Base):
    __tablename__ = "refund"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "refund_id"),
        Index("ix_refund_attempt", "tenant_id", "provider", "attempt_id"),
        one_of("provider", PROVIDERS),
        one_of("type", REFUND_TYPES),
    )

    id: Mapped[UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_id: Mapped[str] = mapped_column(Text, nullable=False)
    refund_id: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    amount_minor: Mapped[int] = money()
    status: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw: Mapped[dict[str, Any]] = raw_payload()


class Settlement(TenantScoped, Base):
    __tablename__ = "settlement"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "settlement_id"),
        Index("ix_settlement_utr", "tenant_id", "utr"),
        one_of("provider", PROVIDERS),
    )

    id: Mapped[UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    settlement_id: Mapped[str] = mapped_column(Text, nullable=False)
    gross_minor: Mapped[int] = money()
    fee_minor: Mapped[int] = money()
    tax_minor: Mapped[int] = money()
    net_minor: Mapped[int] = money()
    utr: Mapped[str | None] = mapped_column(Text)
    settled_on: Mapped[date] = mapped_column(Date, nullable=False)
    raw: Mapped[dict[str, Any]] = raw_payload()


class SettlementLine(TenantScoped, Base):
    __tablename__ = "settlement_line"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "settlement_id", "line_ref"),
        Index("ix_settlement_line_attempt", "tenant_id", "provider", "attempt_id"),
        one_of("provider", PROVIDERS),
        one_of("line_type", LINE_TYPES),
    )

    id: Mapped[UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    settlement_id: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(Text)
    # The provider's own reference for this line inside the settlement.
    line_ref: Mapped[str] = mapped_column(Text, nullable=False)
    line_type: Mapped[str] = mapped_column(Text, nullable=False)
    gross_minor: Mapped[int] = money()
    fee_minor: Mapped[int] = money()
    tax_minor: Mapped[int] = money()
    net_minor: Mapped[int] = money()
    fee_type: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = raw_payload()


class BankCredit(TenantScoped, Base):
    __tablename__ = "bank_credit"
    __table_args__ = (
        Index("ix_bank_credit_utr", "tenant_id", "utr"),
        # A statement line has no id of its own; the hash of the line stands in for one.
        UniqueConstraint("tenant_id", "account_ref", "line_hash"),
    )

    id: Mapped[UUID] = uuid_pk()
    account_ref: Mapped[str] = mapped_column(Text, nullable=False)
    line_hash: Mapped[str] = mapped_column(Text, nullable=False)
    utr: Mapped[str | None] = mapped_column(Text)
    amount_minor: Mapped[int] = money()
    credited_on: Mapped[date] = mapped_column(Date, nullable=False)
    narration: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = raw_payload()


class Dispute(TenantScoped, Base):
    """Created now, populated for preview data only."""

    __tablename__ = "dispute"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "dispute_id"),
        one_of("provider", PROVIDERS),
    )

    id: Mapped[UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    dispute_id: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(Text)
    amount_minor: Mapped[int] = money()
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    due_on: Mapped[date | None] = mapped_column(Date)
    raw: Mapped[dict[str, Any]] = raw_payload()


class Mandate(TenantScoped, Base):
    """Created now, populated for preview data only."""

    __tablename__ = "mandate"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "mandate_id"),
        one_of("provider", PROVIDERS),
    )

    id: Mapped[UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    mandate_id: Mapped[str] = mapped_column(Text, nullable=False)
    customer_token: Mapped[str | None] = mapped_column(Text)
    amount_minor: Mapped[int] = money()
    status: Mapped[str] = mapped_column(Text, nullable=False)
    next_debit_on: Mapped[date | None] = mapped_column(Date)
    raw: Mapped[dict[str, Any]] = raw_payload()


class Alert(TenantScoped, Base):
    """Something a person should look at, such as a provider status no mapping knows."""

    __tablename__ = "alert"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "provider", "subject"),
        one_of("kind", ALERT_KINDS),
        one_of("state", ALERT_STATES),
    )

    id: Mapped[UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    # What the alert is about: for an unmapped status, the provider's status value.
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --- Links and output --------------------------------------------------------


class MatchLink(TenantScoped, Base):
    """One explicit pairing between two records, so it can be shown and undone."""

    __tablename__ = "match_link"
    __table_args__ = (
        Index("ix_match_link_left", "tenant_id", "left_type", "left_id"),
        Index("ix_match_link_right", "tenant_id", "right_type", "right_id"),
        # The same pairing found by a second run is the same link.
        UniqueConstraint("tenant_id", "left_type", "left_id", "right_type", "right_id"),
        one_of("created_by", LINK_CREATORS),
    )

    id: Mapped[UUID] = uuid_pk()
    left_type: Mapped[str] = mapped_column(Text, nullable=False)
    left_id: Mapped[str] = mapped_column(Text, nullable=False)
    right_type: Mapped[str] = mapped_column(Text, nullable=False)
    right_id: Mapped[str] = mapped_column(Text, nullable=False)
    rule: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False, server_default="1")
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[str] = mapped_column(Text, nullable=False, server_default="engine")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class Finding(TenantScoped, Base):
    __tablename__ = "finding"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "finding_key", "sequence"),
        Index("ix_finding_state", "tenant_id", "state"),
        one_of("type", FINDING_TYPES),
        one_of("state", FINDING_STATES),
    )

    id: Mapped[UUID] = uuid_pk()
    type: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(Text)
    amount_minor: Mapped[int] = money()
    cause: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    rule_version: Mapped[str] = mapped_column(Text, nullable=False)
    # Type, provider, cause and month: what makes this the same finding from one
    # engine run to the next. The sequence counts follow-on findings under a key.
    finding_key: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("gen_random_uuid()::text")
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class FindingEvent(TenantScoped, Base):
    """Append-only record of every state change of a finding."""

    __tablename__ = "finding_event"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "finding_id"], ["finding.tenant_id", "finding.id"], ondelete="CASCADE"
        ),
        Index("ix_finding_event_finding", "tenant_id", "finding_id", "occurred_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    finding_id: Mapped[UUID] = mapped_column(SAUUID, nullable=False)
    from_state: Mapped[str | None] = mapped_column(Text)
    to_state: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Proposal(TenantScoped, Base):
    __tablename__ = "proposal"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
        one_of("status", PROPOSAL_STATUSES),
    )

    id: Mapped[UUID] = uuid_pk()
    finding_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(SAUUID), nullable=False, server_default=text("'{}'::uuid[]")
    )
    playbook: Mapped[str] = mapped_column(Text, nullable=False)
    playbook_version: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Text with tool-result references; never bare figures.
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    # Tenant, playbook, finding set and period: one proposal per key.
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)


class Approval(TenantScoped, Base):
    __tablename__ = "approval"
    __table_args__ = (
        # One decision per person per proposal.
        UniqueConstraint("tenant_id", "proposal_id", "approver_id"),
        ForeignKeyConstraint(
            ["tenant_id", "proposal_id"], ["proposal.tenant_id", "proposal.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(["tenant_id", "approver_id"], ["app_user.tenant_id", "app_user.id"]),
        one_of("decision", DECISIONS),
    )

    id: Mapped[UUID] = uuid_pk()
    proposal_id: Mapped[UUID] = mapped_column(SAUUID, nullable=False)
    approver_id: Mapped[UUID] = mapped_column(SAUUID, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Action(TenantScoped, Base):
    """Something done because a proposal was approved. In the first version
    that is only ever drafting a document; nothing is sent.

    A database trigger refuses a row here, and in `claim`, unless the proposal
    has the approvals the tenant requires and no rejection.
    """

    __tablename__ = "action"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "proposal_id"], ["proposal.tenant_id", "proposal.id"], ondelete="CASCADE"
        ),
        # Approving twice cannot carry out a proposal twice.
        UniqueConstraint("tenant_id", "proposal_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    proposal_id: Mapped[UUID] = mapped_column(SAUUID, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    # What was produced: claim ids and document references.
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class AgentRun(TenantScoped, Base):
    """One run of a playbook. The table is also the queue: a worker claims a
    queued run, checkpoints after every tool call, and can pick a run up again
    where it stopped."""

    __tablename__ = "agent_run"
    __table_args__ = (
        Index(
            "uq_agent_run_active",
            "tenant_id",
            "playbook",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_agent_run_queue", "status", "created_at"),
        one_of("status", RUN_STATUSES),
    )

    id: Mapped[UUID] = uuid_pk()
    playbook: Mapped[str] = mapped_column(Text, nullable=False)
    playbook_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    trigger: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    # Every tool call made so far, with its result: a run resumes from here.
    checkpoint: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # The validated output: text with references, and the values they resolve to.
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Claim(TenantScoped, Base):
    __tablename__ = "claim"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "proposal_id"], ["proposal.tenant_id", "proposal.id"], ondelete="CASCADE"
        ),
        one_of("status", CLAIM_STATUSES),
    )

    id: Mapped[UUID] = uuid_pk()
    proposal_id: Mapped[UUID] = mapped_column(SAUUID, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    amount_minor: Mapped[int] = money()
    document_ref: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="drafted")
    provider_reply: Mapped[str | None] = mapped_column(Text)


class AuditLog(TenantScoped, Base):
    """Append-only. Holds a hash of each payload, never the payload itself."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_time", "tenant_id", "occurred_at"),
        one_of("kind", AUDIT_KINDS),
    )

    id: Mapped[UUID] = uuid_pk()
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    ref: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
