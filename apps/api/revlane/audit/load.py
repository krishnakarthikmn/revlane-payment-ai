"""Reads a tenant's records into the plain values the engines work on."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from revlane.connectors.base import IST
from revlane.db import models as m
from revlane.engine import records as r
from revlane.engine.fees import FeeRules
from revlane.engine.matching import MatchRules


@dataclass
class Inputs:
    orders: list[r.Order]
    attempts: list[r.Attempt]
    refunds: list[r.Refund]
    settlements: list[r.Settlement]
    lines: list[r.Line]
    credits: list[r.BankCredit]
    contracts: list[r.Contract]
    # Settlement-to-credit pairs a person has undone.
    rejected: frozenset[tuple[str, str]]
    match_rules: MatchRules
    fee_rules: FeeRules


def _day(moment: datetime) -> date:
    """The calendar day in India, where the merchant and its providers keep their books."""
    return moment.astimezone(IST).date()


def load_contracts(session: Session, tenant_id: UUID) -> list[r.Contract]:
    c, account = m.FeeContract, m.ProviderAccount
    rows = session.execute(
        select(c, account.provider)
        .join(account, (account.tenant_id == c.tenant_id) & (account.id == c.provider_account_id))
        .where(c.tenant_id == tenant_id)
        .order_by(account.provider, c.method, c.effective_from, c.id)
    )
    return [
        r.Contract(
            provider,
            row.method,
            row.card_network,
            row.card_type,
            row.rate_bps,
            row.fixed_fee_minor,
            row.tax_rate_bps,
            row.tax_on_refunded_fee,
            frozenset(row.allowed_fee_types),
            row.effective_from,
            row.effective_to,
        )
        for row, provider in rows
    ]


def load_inputs(session: Session, tenant_id: UUID) -> Inputs:
    o, a, s = m.Order, m.PaymentAttempt, m.AttemptStatus
    orders = [
        r.Order(order_id, amount, status, _day(placed_at))
        for order_id, amount, status, placed_at in session.execute(
            select(o.merchant_order_id, o.amount_minor, o.status, o.placed_at).where(
                o.tenant_id == tenant_id
            )
        )
    ]
    attempts = [
        r.Attempt(
            provider, attempt_id, order_id, amount, status or "unknown", _day(seen), *instrument
        )
        for provider, attempt_id, order_id, amount, status, seen, *instrument in session.execute(
            select(
                a.provider,
                a.attempt_id,
                a.merchant_order_id,
                a.amount_minor,
                s.status,
                a.first_seen_at,
                a.method,
                a.card_network,
                a.card_type,
            )
            .outerjoin(
                s,
                (s.tenant_id == a.tenant_id)
                & (s.provider == a.provider)
                & (s.attempt_id == a.attempt_id),
            )
            .where(a.tenant_id == tenant_id)
        )
    ]
    f = m.Refund
    refunds = [
        r.Refund(provider, refund_id, attempt_id, kind, amount, _day(occurred_at))
        for provider, refund_id, attempt_id, kind, amount, occurred_at in session.execute(
            select(
                f.provider, f.refund_id, f.attempt_id, f.type, f.amount_minor, f.occurred_at
            ).where(f.tenant_id == tenant_id)
        )
    ]
    t = m.Settlement
    settlements = [
        r.Settlement(*row)
        for row in session.execute(
            select(t.provider, t.settlement_id, t.net_minor, t.utr, t.settled_on).where(
                t.tenant_id == tenant_id
            )
        )
    ]
    line = m.SettlementLine
    lines = [
        r.Line(
            provider, settlement_id, line_ref, attempt_id, line_type, gross, fee, tax, net, fee_type
        )
        for (
            provider,
            settlement_id,
            line_ref,
            attempt_id,
            line_type,
            fee_type,
            gross,
            fee,
            tax,
            net,
        ) in (
            session.execute(
                select(
                    line.provider,
                    line.settlement_id,
                    line.line_ref,
                    line.attempt_id,
                    line.line_type,
                    line.fee_type,
                    line.gross_minor,
                    line.fee_minor,
                    line.tax_minor,
                    line.net_minor,
                ).where(line.tenant_id == tenant_id)
            )
        )
    ]
    b = m.BankCredit
    credits = [
        r.BankCredit(*row)
        for row in session.execute(
            select(b.line_hash, b.utr, b.amount_minor, b.credited_on).where(
                b.tenant_id == tenant_id
            )
        )
    ]
    link = m.MatchLink
    rejected = frozenset(
        (left_id, right_id)
        for left_id, right_id in session.execute(
            select(link.left_id, link.right_id).where(
                link.tenant_id == tenant_id,
                link.left_type == "settlement",
                link.right_type == "bank_credit",
                link.active.is_(False),
            )
        )
    )
    policy = session.scalar(select(m.Tenant.policy).where(m.Tenant.id == tenant_id)) or {}
    return Inputs(
        orders=orders,
        attempts=attempts,
        refunds=refunds,
        settlements=settlements,
        lines=lines,
        credits=credits,
        contracts=load_contracts(session, tenant_id),
        rejected=rejected,
        match_rules=MatchRules.from_policy(policy.get("matching") or {}),
        fee_rules=FeeRules.from_policy(policy.get("fees") or {}),
    )
