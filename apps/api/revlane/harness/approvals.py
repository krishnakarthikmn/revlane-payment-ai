"""The approval gate. A proposal does nothing until a person approves it.

With two-person approval switched on for the tenant
(`tenant.policy["approvals"]["two_person"]`), two different people must
approve. One rejection ends the proposal.
"""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from revlane.connectors.base import IST
from revlane.db import models as m
from revlane.harness import actions, audit
from revlane.storage import Storage

MAY_DECIDE = ("approver", "admin")


class ProposalNotFound(LookupError):
    pass


class NotAllowed(PermissionError):
    pass


class AlreadyDecided(ValueError):
    pass


@dataclass
class Decision:
    proposal: m.Proposal
    approvals: int
    needed: int
    # Set once the proposal is fully approved and has been carried out.
    action: m.Action | None = None


def decide(
    session: Session,
    tenant_id: UUID,
    proposal_id: UUID,
    approver_id: UUID,
    decision: str,
    *,
    reason: str | None = None,
    today: date | None = None,
    storage: Storage | None = None,
) -> Decision:
    """Record one person's decision. The last approval needed carries the proposal out."""
    if decision not in m.DECISIONS:
        raise ValueError(f"A decision is one of {', '.join(m.DECISIONS)}.")
    proposal = session.scalar(
        select(m.Proposal)
        .where(m.Proposal.tenant_id == tenant_id, m.Proposal.id == proposal_id)
        .with_for_update()
    )
    if proposal is None:
        raise ProposalNotFound(f"No proposal {proposal_id}.")
    approver = session.scalar(
        select(m.AppUser).where(m.AppUser.tenant_id == tenant_id, m.AppUser.id == approver_id)
    )
    if approver is None or approver.role not in MAY_DECIDE:
        raise NotAllowed("Only an approver or an admin can decide a proposal.")
    if proposal.status != "pending":
        raise AlreadyDecided(f"This proposal is already {proposal.status}.")
    a = m.Approval
    already = session.scalar(
        select(a.id).where(
            a.tenant_id == tenant_id, a.proposal_id == proposal_id, a.approver_id == approver_id
        )
    )
    if already is not None:
        raise AlreadyDecided("You have already decided this proposal. It needs a second person.")

    session.add(
        a(
            tenant_id=tenant_id,
            proposal_id=proposal_id,
            approver_id=approver_id,
            decision=decision,
            reason=reason,
        )
    )
    session.flush()
    audit.record(
        session,
        tenant_id,
        actor=f"user:{approver_id}",
        kind="approval",
        payload={"proposal": str(proposal_id), "decision": decision},
        ref=f"proposal:{proposal_id}",
    )

    needed = actions.approvals_needed(session, tenant_id)
    approved = len(
        set(
            session.scalars(
                select(a.approver_id).where(
                    a.tenant_id == tenant_id,
                    a.proposal_id == proposal_id,
                    a.decision == "approved",
                )
            )
        )
    )
    result = Decision(proposal, approved, needed)
    if decision == "rejected":
        proposal.status = "rejected"
    elif approved >= needed:
        proposal.status = "approved"
        session.flush()
        result.action = actions.execute(
            session,
            tenant_id,
            proposal,
            by=f"user:{approver_id}",
            today=today or datetime.now(tz=IST).date(),
            storage=storage,
        )
    session.flush()
    return result
