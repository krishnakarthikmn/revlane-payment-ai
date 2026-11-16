"""One audit run: derive statuses that have come due, match, check fees, store."""

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from revlane.audit import store
from revlane.audit.load import load_inputs
from revlane.connectors.base import IST
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.engine import findings as fd
from revlane.engine.fees import check_fees
from revlane.engine.matching import EXACT, Claimed, match_recoveries, three_way_match
from revlane.engine.records import BankCredit
from revlane.ingest import statuses

FAMILIES = {
    "reconciliation": ("paid_auto_refunded", "settled_short", "captured_not_settled"),
    "fee_audit": ("fee_above_contract", "tax_on_refunded_fee", "fee_not_in_contract"),
}


@dataclass
class AuditReport:
    as_of: date
    # Links the engine made this run, by rule, and how many of them were new.
    links: dict[str, int] = field(default_factory=dict)
    links_added: int = 0
    # Links made without an exact reference: sound, but worth a person's look.
    review_links: int = 0
    findings_created: int = 0
    findings_updated: int = 0
    findings_moved: int = 0
    # Claimed findings whose money was found in the bank this run.
    findings_recovered: int = 0
    unmatched_settlements: list[str] = field(default_factory=list)
    unmatched_credits: list[str] = field(default_factory=list)
    fee_lines_checked: int = 0
    fee_lines_unchecked: dict[str, int] = field(default_factory=dict)
    # Money in findings nobody has acted on yet, by family.
    totals_minor: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"as_of": self.as_of.isoformat()}


def today() -> date:
    return datetime.now(tz=IST).date()


def run_audit(tenant_id: UUID, *, as_of: date | None = None) -> AuditReport:
    """Run both engines for a tenant as of a date (default: today in India).

    Safe to repeat: a second run over the same data adds no link and changes
    no finding.
    """
    with tenant_session(tenant_id) as session:
        return audit(session, tenant_id, as_of or today())


def audit(session: Session, tenant_id: UUID, as_of: date) -> AuditReport:
    """The same run inside a session the caller already holds."""
    report = AuditReport(as_of=as_of)
    statuses.refresh_due(session, tenant_id, datetime.combine(as_of, time.max, tzinfo=IST))
    inputs = load_inputs(session, tenant_id)

    matched = three_way_match(
        orders=inputs.orders,
        attempts=inputs.attempts,
        refunds=inputs.refunds,
        settlements=inputs.settlements,
        lines=inputs.lines,
        credits=inputs.credits,
        as_of=as_of,
        rules=inputs.match_rules,
        rejected=inputs.rejected,
    )
    fee_check = check_fees(
        lines=inputs.lines,
        settlements=inputs.settlements,
        attempts=inputs.attempts,
        refunds=inputs.refunds,
        contracts=inputs.contracts,
        rules=inputs.fee_rules,
    )

    report.links = dict(sorted(Counter(link.rule for link in matched.links).items()))
    report.review_links = sum(1 for link in matched.links if link.confidence < EXACT)
    report.links_added = store.save_links(session, tenant_id, matched.links)
    report.unmatched_settlements = matched.unmatched_settlements
    report.unmatched_credits = matched.unmatched_credits
    report.fee_lines_checked = fee_check.checked
    report.fee_lines_unchecked = fee_check.unchecked

    report.findings_recovered = _record_recoveries(
        session, tenant_id, inputs.credits, matched.unmatched_credits
    )

    actions = fd.reconcile(
        store.existing_findings(session, tenant_id),
        [*matched.findings, *fee_check.findings],
    )
    done = store.apply(session, tenant_id, actions)
    report.findings_created = done["created"]
    report.findings_updated = done["updated"]
    report.findings_moved = done["moved"]

    open_by_type = dict(
        session.execute(
            select(m.Finding.type, func.sum(m.Finding.amount_minor))
            .where(m.Finding.tenant_id == tenant_id, m.Finding.state.in_(fd.OPEN))
            .group_by(m.Finding.type)
        ).all()
    )
    report.totals_minor = {
        family: sum(int(open_by_type.get(kind, 0)) for kind in types)
        for family, types in FAMILIES.items()
    }
    return report


def _record_recoveries(
    session: Session, tenant_id: UUID, credits: list[BankCredit], unexplained: list[str]
) -> int:
    """Mark claimed findings recovered when a bank credit for the claimed amount
    has arrived, and their claims paid."""
    f, event, link = m.Finding, m.FindingEvent, m.MatchLink
    claimed_at = dict(
        session.execute(
            select(event.finding_id, func.max(event.occurred_at))
            .where(event.tenant_id == tenant_id, event.to_state == fd.CLAIMED)
            .group_by(event.finding_id)
        ).all()
    )
    claimed = [
        Claimed(str(row.id), row.amount_minor, claimed_at[row.id].astimezone(IST).date())
        for row in session.scalars(select(f).where(f.tenant_id == tenant_id, f.state == fd.CLAIMED))
        if row.id in claimed_at
    ]
    if not claimed:
        return 0
    taken = frozenset(
        session.scalars(
            select(link.right_id).where(
                link.tenant_id == tenant_id,
                link.left_type == "finding",
                link.right_type == "bank_credit",
            )
        )
    )
    free = set(unexplained)
    links = match_recoveries(
        claimed, [credit for credit in credits if credit.credit_id in free], taken
    )
    store.save_links(session, tenant_id, links)
    for found in links:
        finding_id = UUID(found.left_id)
        store.move_finding(
            session, tenant_id, finding_id, fd.RECOVERED, actor=store.ENGINE, reason=fd.CLAIM_PAID
        )
        proposals = select(m.Proposal.id).where(
            m.Proposal.tenant_id == tenant_id, m.Proposal.finding_ids.contains([finding_id])
        )
        for claim in session.scalars(
            select(m.Claim).where(
                m.Claim.tenant_id == tenant_id,
                m.Claim.proposal_id.in_(proposals),
                m.Claim.status == "drafted",
            )
        ):
            claim.status = "paid"
    return len(links)
