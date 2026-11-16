"""Proposals: what an agent suggests, waiting for a person."""

import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from revlane.db import models as m
from revlane.harness import grounding
from revlane.harness.playbook import Playbook
from revlane.harness.schema import ProposalOut


def idempotency_key(
    tenant_id: UUID, playbook: str, action_type: str, finding_ids: list[UUID], period: str
) -> str:
    """Tenant, playbook, action, finding set and period: one proposal per key."""
    parts = [str(tenant_id), playbook, action_type, *sorted(str(i) for i in finding_ids), period]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def create(
    session: Session,
    tenant_id: UUID,
    playbook: Playbook,
    run_id: UUID,
    proposed: ProposalOut,
    results: dict[str, Any],
) -> tuple[UUID, bool]:
    """Store a proposal. Returns its id and whether it is new: proposing the
    same thing for the same findings and period a second time changes nothing."""
    finding_ids = sorted({UUID(finding_id) for finding_id in proposed.finding_ids})
    findings = list(
        session.scalars(
            select(m.Finding).where(m.Finding.tenant_id == tenant_id, m.Finding.id.in_(finding_ids))
        )
    )
    starts = [f.period_start for f in findings if f.period_start]
    ends = [f.period_end for f in findings if f.period_end]
    period = f"{min(starts).isoformat() if starts else ''}/{max(ends).isoformat() if ends else ''}"
    key = idempotency_key(tenant_id, playbook.name, proposed.action_type, finding_ids, period)
    texts = [proposed.title, proposed.rationale]
    summary = {
        # The model's text, references and all, and what code made of it.
        "title": proposed.title,
        "rationale": proposed.rationale,
        "rendered": {
            "title": grounding.render(proposed.title, results),
            "rationale": grounding.render(proposed.rationale, results),
        },
        "values": grounding.values(texts, results),
        # Summed by code from the findings, never taken from the model.
        "amount_minor": sum(f.amount_minor for f in findings),
        "providers": sorted({f.provider for f in findings if f.provider}),
        "period": period,
        "run_id": str(run_id),
    }
    created = session.scalar(
        insert(m.Proposal)
        .values(
            tenant_id=tenant_id,
            finding_ids=finding_ids,
            playbook=playbook.name,
            playbook_version=playbook.version,
            action_type=proposed.action_type,
            summary=summary,
            idempotency_key=key,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
        .returning(m.Proposal.id)
    )
    if created is not None:
        return created, True
    existing = session.scalar(
        select(m.Proposal.id).where(
            m.Proposal.tenant_id == tenant_id, m.Proposal.idempotency_key == key
        )
    )
    assert existing is not None
    return existing, False
