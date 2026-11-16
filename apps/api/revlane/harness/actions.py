"""Carrying out an approved proposal. Rule 6: nothing here runs without approval.

In the first version an action only ever drafts a document: a claim, a
settlement query, a list of bookings to offer again, or a report. Rule 5:
nothing is sent to a provider or a customer.

The approval is checked twice: here, before anything is written, and by a
database trigger on `action` and `claim`, which no code path can get round.
"""

from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from revlane.audit import store
from revlane.db import models as m
from revlane.engine import findings as fd
from revlane.harness import audit, claims
from revlane.storage import Storage, get_storage


class ApprovalRequired(PermissionError):
    pass


def approvals_needed(session: Session, tenant_id: UUID) -> int:
    policy = session.scalar(select(m.Tenant.policy).where(m.Tenant.id == tenant_id)) or {}
    return 2 if (policy.get("approvals") or {}).get("two_person") else 1


def is_approved(session: Session, tenant_id: UUID, proposal_id: UUID) -> bool:
    a = m.Approval
    by_decision = dict(
        session.execute(
            select(a.decision, func.count(a.approver_id.distinct()))
            .where(a.tenant_id == tenant_id, a.proposal_id == proposal_id)
            .group_by(a.decision)
        ).all()
    )
    return by_decision.get("rejected", 0) == 0 and by_decision.get(
        "approved", 0
    ) >= approvals_needed(session, tenant_id)


def _open_findings(session: Session, tenant_id: UUID, proposal: m.Proposal) -> list[m.Finding]:
    f = m.Finding
    return list(
        session.scalars(
            select(f)
            .where(
                f.tenant_id == tenant_id,
                f.id.in_(proposal.finding_ids),
                f.state == fd.RECOVERABLE,
            )
            .order_by(f.amount_minor.desc(), f.id)
        )
    )


def _draft_claims(
    session: Session,
    tenant_id: UUID,
    proposal: m.Proposal,
    findings: list[m.Finding],
    by: str,
    storage: Storage,
    today: date,
) -> dict[str, Any]:
    merchant = session.scalar(select(m.Tenant.name).where(m.Tenant.id == tenant_id)) or ""
    claim_ids, documents = [], []
    for finding in findings:
        draft = claims.build(finding, merchant, today)
        claim_id = uuid4()
        ref = f"claims/{claim_id}"
        storage.put(tenant_id, f"{ref}.pdf", claims.render_pdf(draft))
        storage.put(tenant_id, f"{ref}.txt", claims.render_email(draft).encode("utf-8"))
        session.add(
            m.Claim(
                tenant_id=tenant_id,
                id=claim_id,
                proposal_id=proposal.id,
                provider=finding.provider or "",
                amount_minor=finding.amount_minor,
                document_ref=ref,
            )
        )
        session.flush()
        store.move_finding(
            session,
            tenant_id,
            finding.id,
            fd.CLAIMED,
            actor=by,
            reason="A claim was drafted after approval.",
        )
        claim_ids.append(str(claim_id))
        documents += [f"{ref}.pdf", f"{ref}.txt"]
    return {"claims": claim_ids, "documents": documents}


def _draft_reoffer_list(
    session: Session,
    tenant_id: UUID,
    proposal: m.Proposal,
    findings: list[m.Finding],
    by: str,
    storage: Storage,
    today: date,
) -> dict[str, Any]:
    key = f"reoffers/{proposal.id}.csv"
    storage.put(tenant_id, key, claims.render_reoffer_csv(findings).encode("utf-8"))
    return {"claims": [], "documents": [key]}


def _draft_leakage_report(
    session: Session,
    tenant_id: UUID,
    proposal: m.Proposal,
    findings: list[m.Finding],
    by: str,
    storage: Storage,
    today: date,
) -> dict[str, Any]:
    merchant = session.scalar(select(m.Tenant.name).where(m.Tenant.id == tenant_id)) or ""
    key = f"reports/{proposal.id}.pdf"
    storage.put(tenant_id, key, claims.render_report_pdf(merchant, today, findings))
    return {"claims": [], "documents": [key]}


Drafter = Callable[[Session, UUID, m.Proposal, list[m.Finding], str, Storage, date], dict[str, Any]]
DRAFTERS: dict[str, Drafter] = {
    "draft_fee_claim": _draft_claims,
    "draft_settlement_query": _draft_claims,
    "draft_reoffer_list": _draft_reoffer_list,
    "draft_leakage_report": _draft_leakage_report,
}


def execute(
    session: Session,
    tenant_id: UUID,
    proposal: m.Proposal,
    *,
    by: str,
    today: date,
    storage: Storage | None = None,
) -> m.Action:
    """Carry out an approved proposal, once. Raises `ApprovalRequired` otherwise."""
    if proposal.status != "approved" or not is_approved(session, tenant_id, proposal.id):
        raise ApprovalRequired("This proposal has not been approved.")
    existing = session.scalar(
        select(m.Action).where(m.Action.tenant_id == tenant_id, m.Action.proposal_id == proposal.id)
    )
    if existing is not None:
        return existing
    drafter = DRAFTERS.get(proposal.action_type)
    if drafter is None:
        raise ValueError(f"No action called {proposal.action_type}.")
    findings = _open_findings(session, tenant_id, proposal)
    skipped = sorted({str(i) for i in proposal.finding_ids} - {str(f.id) for f in findings})
    result = drafter(session, tenant_id, proposal, findings, by, storage or get_storage(), today)
    # Findings that moved on between the proposal and the approval are left alone.
    action = m.Action(
        tenant_id=tenant_id,
        proposal_id=proposal.id,
        type=proposal.action_type,
        result=result | {"skipped_findings": skipped},
    )
    session.add(action)
    session.flush()
    audit.record(
        session,
        tenant_id,
        actor=by,
        kind="action",
        payload={"type": action.type, "result": action.result},
        ref=f"proposal:{proposal.id}",
    )
    return action
