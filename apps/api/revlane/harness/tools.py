"""The tools an agent may call. Every one of them only reads.

A tool takes validated arguments and a tenant-scoped session and returns plain
data. Amounts are paise in keys ending `_minor`; counts end `_count`; rates end
`_bps`. Free text that came from outside (a bank narration, an order status
from a file) is wrapped as `{"untrusted_text": ...}`: the model may read it as
data, and output can never quote it.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from revlane.audit import store
from revlane.audit.load import load_contracts
from revlane.audit.run import FAMILIES, audit
from revlane.connectors.base import IST
from revlane.db import models as m
from revlane.engine import fees
from revlane.engine import findings as fd
from revlane.engine import records as r
from revlane.harness.grounding import UNTRUSTED
from revlane.llm.client import ToolSpec

MAX_TEXT = 200


class ToolError(Exception):
    """The call could not be answered. The message goes back to the model as data."""


@dataclass(frozen=True)
class ToolContext:
    session: Session
    tenant_id: UUID
    as_of: date


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(Args):
    pass


class FindingArgs(Args):
    finding_id: UUID


class LimitedFindingArgs(FindingArgs):
    limit: int = Field(default=5, ge=1, le=20)


class FindingsForArgs(Args):
    family: Literal["reconciliation", "fee_audit"] | None = None
    type: str | None = None
    state: Literal["lost", "recoverable", "claimed", "recovered", "written_off"] | None = None


class RupeeImpactArgs(Args):
    group_by: Literal["leak", "provider"] = "leak"


class ContractForArgs(Args):
    provider: str
    method: str
    card_network: str | None = None
    card_type: str | None = None
    on: date | None = None


def untrusted(text: str | None) -> dict[str, str] | None:
    """Wrap text that came from outside, cut to a sane length."""
    return None if text is None else {UNTRUSTED: text[:MAX_TEXT]}


def _finding(ctx: ToolContext, finding_id: UUID) -> m.Finding:
    finding = ctx.session.scalar(
        select(m.Finding).where(m.Finding.tenant_id == ctx.tenant_id, m.Finding.id == finding_id)
    )
    if finding is None:
        raise ToolError(f"No finding {finding_id}.")
    return finding


def _month(day: date | None) -> str | None:
    return f"{day:%Y-%m}" if day else None


def _summary(finding: m.Finding) -> dict[str, Any]:
    return {
        "id": str(finding.id),
        "type": finding.type,
        "provider": finding.provider,
        "cause": finding.cause,
        "state": finding.state,
        "amount_minor": finding.amount_minor,
        "record_count": len(finding.evidence.get("items", [])),
        "month": _month(finding.period_start),
        "period_start": finding.period_start.isoformat() if finding.period_start else None,
        "period_end": finding.period_end.isoformat() if finding.period_end else None,
    }


# --- The tools -------------------------------------------------------------------


def three_way_match(ctx: ToolContext, _: NoArgs) -> dict[str, Any]:
    """Run the match and the fee check now, and say what they did."""
    report = audit(ctx.session, ctx.tenant_id, ctx.as_of)
    missing = report.fee_lines_unchecked.get(fees.CONTRACT_MISSING, 0)
    return {
        "as_of": report.as_of.isoformat(),
        "link_count": sum(report.links.values()),
        "links_by_rule": report.links,
        "review_link_count": report.review_links,
        "unmatched_settlement_count": len(report.unmatched_settlements),
        "unexplained_credit_count": len(report.unmatched_credits),
        "new_finding_count": report.findings_created,
        "fee_line_count": report.fee_lines_checked,
        "contract_missing_count": missing,
        "contract_missing": missing > 0,
    }


def findings_for(ctx: ToolContext, args: FindingsForArgs) -> dict[str, Any]:
    """Stored findings, largest first."""
    f = m.Finding
    query = select(f).where(f.tenant_id == ctx.tenant_id)
    if args.family:
        query = query.where(f.type.in_(FAMILIES[args.family]))
    if args.type:
        query = query.where(f.type == args.type)
    if args.state:
        query = query.where(f.state == args.state)
    found = [
        _summary(row) for row in ctx.session.scalars(query.order_by(f.amount_minor.desc(), f.id))
    ]
    return {
        "finding_count": len(found),
        "total_minor": sum(row["amount_minor"] for row in found),
        "findings": found,
    }


def rupee_impact(ctx: ToolContext, args: RupeeImpactArgs) -> dict[str, Any]:
    """Lost, recoverable and recovered, never mixed.

    Lost is everything that leaked. Recoverable is the part someone can still
    act on. Recovered is money that reached the bank. A finding the engine
    closed because the records came to match was never a leak and counts nowhere.
    """
    real = {row.id for row in store.existing_findings(ctx.session, ctx.tenant_id) if row.covers}
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    totals = {"lost_minor": 0, "recoverable_minor": 0, "recovered_minor": 0}
    count = 0
    for finding in ctx.session.scalars(
        select(m.Finding).where(m.Finding.tenant_id == ctx.tenant_id)
    ):
        if str(finding.id) not in real:
            continue
        count += 1
        provider = finding.provider or "unknown"
        key = (provider,) if args.group_by == "provider" else (finding.type, provider)
        group = groups.setdefault(
            key,
            {"key": ":".join(key), "provider": provider}
            | ({} if args.group_by == "provider" else {"type": finding.type})
            | {"lost_minor": 0, "recoverable_minor": 0, "recovered_minor": 0, "record_count": 0},
        )
        amounts = {
            "lost_minor": finding.amount_minor,
            "recoverable_minor": (
                finding.amount_minor if finding.state in (fd.RECOVERABLE, fd.CLAIMED) else 0
            ),
            "recovered_minor": finding.amount_minor if finding.state == fd.RECOVERED else 0,
        }
        for name, amount in amounts.items():
            group[name] += amount
            totals[name] += amount
        group["record_count"] += len(finding.evidence.get("items", []))
    ordered = sorted(groups.values(), key=lambda g: (-g["lost_minor"], g["key"]))
    return totals | {
        "finding_count": count,
        "group_count": len(ordered),
        "top_key": ordered[0]["key"] if ordered else None,
        "groups": ordered,
    }


def _lines(ctx: ToolContext, finding: m.Finding) -> list[m.SettlementLine]:
    """The settlement lines a finding's evidence names."""
    wanted = [
        str(line_id).split("/", 1) for line_id in finding.evidence.get("settlement_lines", [])
    ]
    line = m.SettlementLine
    if not wanted or finding.provider is None:
        return []
    return list(
        ctx.session.scalars(
            select(line)
            .where(
                line.tenant_id == ctx.tenant_id,
                line.provider == finding.provider,
                tuple_(line.settlement_id, line.line_ref).in_([tuple(pair) for pair in wanted]),
            )
            .order_by(line.settlement_id, line.line_ref)
        )
    )


def settlement_lines(ctx: ToolContext, args: LimitedFindingArgs) -> dict[str, Any]:
    """The settlement lines behind a finding, largest fee first."""
    lines = _lines(ctx, _finding(ctx, args.finding_id))
    lines.sort(key=lambda line: (-(line.fee_minor + line.tax_minor), line.line_ref))
    return {
        "line_count": len(lines),
        "gross_minor": sum(line.gross_minor for line in lines),
        "fee_minor": sum(line.fee_minor for line in lines),
        "tax_minor": sum(line.tax_minor for line in lines),
        "lines": [
            {
                "line_id": f"{line.settlement_id}/{line.line_ref}",
                "line_type": line.line_type,
                "fee_type": line.fee_type,
                "gross_minor": line.gross_minor,
                "fee_minor": line.fee_minor,
                "tax_minor": line.tax_minor,
                "net_minor": line.net_minor,
            }
            for line in lines[: args.limit]
        ],
    }


def bank_credits(ctx: ToolContext, args: FindingArgs) -> dict[str, Any]:
    """The bank credits linked to the settlements a finding names."""
    finding = _finding(ctx, args.finding_id)
    settlements = [f"{finding.provider}:{sid}" for sid in finding.evidence.get("settlements", [])]
    link, credit = m.MatchLink, m.BankCredit
    rows = (
        ctx.session.execute(
            select(link.left_id, link.rule, credit)
            .join(
                credit, (credit.tenant_id == link.tenant_id) & (credit.line_hash == link.right_id)
            )
            .where(
                link.tenant_id == ctx.tenant_id,
                link.left_type == "settlement",
                link.right_type == "bank_credit",
                link.active.is_(True),
                link.left_id.in_(settlements),
            )
            .order_by(credit.credited_on, link.left_id)
        ).all()
        if settlements
        else []
    )
    return {
        "settlement_count": len(settlements),
        "credit_count": len(rows),
        "credited_minor": sum(row[2].amount_minor for row in rows),
        "credits": [
            {
                "settlement_id": left_id.split(":", 1)[1],
                "matched_by": rule,
                "credited_on": row.credited_on.isoformat(),
                "amount_minor": row.amount_minor,
                "has_utr": row.utr is not None,
                "narration": untrusted(row.narration),
            }
            for left_id, rule, row in rows
        ],
    }


def fee_differences(ctx: ToolContext, args: LimitedFindingArgs) -> dict[str, Any]:
    """What a fee finding's lines carry against what the contracts give."""
    finding = _finding(ctx, args.finding_id)
    rows = _lines(ctx, finding)
    lines = [
        r.Line(
            row.provider,
            row.settlement_id,
            row.line_ref,
            row.attempt_id,
            row.line_type,
            row.gross_minor,
            row.fee_minor,
            row.tax_minor,
            row.net_minor,
            row.fee_type,
        )
        for row in rows
    ]
    attempt_ids = sorted({line.attempt_id for line in lines if line.attempt_id})
    a, s = m.PaymentAttempt, m.Settlement
    attempts = [
        r.Attempt(
            row.provider,
            row.attempt_id,
            row.merchant_order_id,
            row.amount_minor,
            "succeeded",
            row.first_seen_at.astimezone(IST).date(),
            row.method,
            row.card_network,
            row.card_type,
        )
        for row in ctx.session.scalars(
            select(a).where(
                a.tenant_id == ctx.tenant_id,
                a.provider == finding.provider,
                a.attempt_id.in_(attempt_ids),
            )
        )
    ]
    settlements = [
        r.Settlement(row.provider, row.settlement_id, row.net_minor, row.utr, row.settled_on)
        for row in ctx.session.scalars(
            select(s).where(
                s.tenant_id == ctx.tenant_id,
                s.provider == finding.provider,
                s.settlement_id.in_(sorted({line.settlement_id for line in lines})),
            )
        )
    ]
    checked = fees.check_fees(
        lines=lines,
        settlements=settlements,
        attempts=attempts,
        refunds=[],
        contracts=load_contracts(ctx.session, ctx.tenant_id),
    )
    own = [d for d in checked.differences if d.type == finding.type]
    own.sort(key=lambda d: (-d.amount_minor, d.line_id))
    missing = checked.unchecked.get(fees.CONTRACT_MISSING, 0)
    return {
        "line_count": len(own),
        "difference_minor": sum(d.amount_minor for d in own),
        "charged_minor": sum(d.charged_minor for d in own),
        "expected_minor": sum(d.expected_minor for d in own),
        "contract_missing_count": missing,
        "contract_missing": missing > 0,
        "largest": [
            {
                "line_id": d.line_id,
                "difference_minor": d.amount_minor,
                "charged_minor": d.charged_minor,
                "expected_minor": d.expected_minor,
            }
            for d in own[: args.limit]
        ],
    }


def contract_for(ctx: ToolContext, args: ContractForArgs) -> dict[str, Any]:
    """The contract row in force for a method on a date (default: today)."""
    contract = fees.contract_for(
        load_contracts(ctx.session, ctx.tenant_id),
        args.provider.lower(),
        args.method.lower(),
        args.card_network.lower() if args.card_network else None,
        args.card_type.lower() if args.card_type else None,
        args.on or ctx.as_of,
    )
    if contract is None:
        return {"found": False, "contract_missing": True}
    return {
        "found": True,
        "contract_missing": False,
        "provider": contract.provider,
        "method": contract.method,
        "card_network": contract.card_network,
        "card_type": contract.card_type,
        "rate_bps": contract.rate_bps,
        "fixed_fee_minor": contract.fixed_fee_minor,
        "tax_rate_bps": contract.tax_rate_bps,
        "provider_keeps_tax_on_refunded_fee": contract.tax_on_refunded_fee,
        "allowed_fee_types": sorted(contract.allowed_fee_types),
        "effective_from": contract.effective_from.isoformat(),
        "effective_to": contract.effective_to.isoformat() if contract.effective_to else None,
    }


def evidence_for(ctx: ToolContext, args: LimitedFindingArgs) -> dict[str, Any]:
    """The records a finding rests on: how many of each kind, and the largest."""
    finding = _finding(ctx, args.finding_id)
    items = sorted(
        finding.evidence.get("items", []), key=lambda item: (-item["amount_minor"], item["id"])
    )
    order_ids = [str(order_id) for order_id in finding.evidence.get("orders", [])][: args.limit]
    o = m.Order
    orders: list[m.Order] = (
        list(
            ctx.session.scalars(
                select(o)
                .where(o.tenant_id == ctx.tenant_id, o.merchant_order_id.in_(order_ids))
                .order_by(o.merchant_order_id)
            )
        )
        if order_ids
        else []
    )
    counts: dict[str, int] = defaultdict(int)
    for kind, records in finding.evidence.items():
        if kind != "items":
            counts[f"{kind}_count"] = len(records)
    return _summary(finding) | {
        "record_counts": dict(counts),
        "item_count": len(items),
        "largest": [
            {"id": item["id"], "amount_minor": item["amount_minor"]} for item in items[: args.limit]
        ],
        # What the merchant's own export says about the orders: text from a file.
        "orders": [
            {
                "order_id": order.merchant_order_id,
                "amount_minor": order.amount_minor,
                "status": untrusted(order.status),
                "fulfilment": untrusted(order.fulfilment_status),
            }
            for order in orders
        ],
    }


# --- The registry -----------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    args: type[Args]
    run: Callable[[ToolContext, Any], dict[str, Any]]

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, (self.run.__doc__ or "").strip(), self.args.model_json_schema())


REGISTRY: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool("three_way_match", NoArgs, three_way_match),
        Tool("findings_for", FindingsForArgs, findings_for),
        Tool("rupee_impact", RupeeImpactArgs, rupee_impact),
        Tool("settlement_lines", LimitedFindingArgs, settlement_lines),
        Tool("bank_credits", FindingArgs, bank_credits),
        Tool("fee_differences", LimitedFindingArgs, fee_differences),
        Tool("contract_for", ContractForArgs, contract_for),
        Tool("evidence_for", LimitedFindingArgs, evidence_for),
    )
}


def call(
    ctx: ToolContext, name: str, arguments: dict[str, Any], allowed: frozenset[str]
) -> dict[str, Any]:
    """Check the allow-list and the arguments, then run the tool.

    Raises `ToolError` for anything refused; the harness records the refusal
    and hands it back to the model as a result.
    """
    if name not in allowed or name not in REGISTRY:
        raise ToolError(f"The tool {name} is not available to this playbook.")
    tool = REGISTRY[name]
    try:
        args = tool.args.model_validate(arguments)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise ToolError(f"{name}: {where}: {first['msg']}") from error
    return tool.run(ctx, args)
