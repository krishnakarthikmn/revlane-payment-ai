"""Writes what the engines found: links, findings, and every change of state."""

from collections.abc import Iterable, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from revlane.db import models as m
from revlane.engine import findings as fd
from revlane.engine.records import Link

BATCH = 5_000
ENGINE = "engine"


class FindingNotFound(LookupError):
    pass


def save_links(session: Session, tenant_id: UUID, links: Iterable[Link]) -> int:
    """Add the links that are not there yet. A link is never changed or removed
    here: a person may have undone it, and that must survive the next run."""
    link = m.MatchLink
    known = {
        tuple(row)
        for row in session.execute(
            select(link.left_type, link.left_id, link.right_type, link.right_id).where(
                link.tenant_id == tenant_id
            )
        )
    }
    new = [
        {
            "tenant_id": tenant_id,
            "left_type": found.left_type,
            "left_id": found.left_id,
            "right_type": found.right_type,
            "right_id": found.right_id,
            "rule": found.rule,
            "confidence": found.confidence,
            "amount_minor": found.amount_minor,
            "created_by": ENGINE,
        }
        for found in links
        if (found.left_type, found.left_id, found.right_type, found.right_id) not in known
    ]
    for start in range(0, len(new), BATCH):
        session.execute(insert(link).on_conflict_do_nothing(), new[start : start + BATCH])
    return len(new)


def existing_findings(session: Session, tenant_id: UUID) -> list[fd.Existing]:
    event = m.FindingEvent
    # Who made the latest move of each finding: the engine's own write-offs do
    # not stop a leak from being reported again.
    last_actor: dict[UUID, str] = {}
    for finding_id, actor in session.execute(
        select(event.finding_id, event.actor)
        .where(event.tenant_id == tenant_id)
        .order_by(event.occurred_at, event.id)
    ):
        last_actor[finding_id] = actor
    return [
        fd.Existing(
            id=str(finding.id),
            key=finding.finding_key,
            sequence=finding.sequence,
            state=finding.state,
            amount_minor=finding.amount_minor,
            item_ids=frozenset(item["id"] for item in finding.evidence.get("items", [])),
            covers=not (finding.state == fd.WRITTEN_OFF and last_actor.get(finding.id) == ENGINE),
        )
        for finding in session.scalars(select(m.Finding).where(m.Finding.tenant_id == tenant_id))
    ]


def apply(session: Session, tenant_id: UUID, actions: Sequence[fd.Action]) -> dict[str, int]:
    """Carry out what `findings.reconcile` decided. Returns how many of each."""
    done = {"created": 0, "updated": 0, "moved": 0}
    for action in actions:
        if isinstance(action, fd.Create):
            found = action.detected
            state = fd.INITIAL_STATE[found.type]
            finding = m.Finding(
                tenant_id=tenant_id,
                type=found.type,
                provider=found.provider,
                amount_minor=found.amount_minor,
                cause=found.cause,
                state=state,
                evidence=found.evidence(),
                period_start=found.period_start,
                period_end=found.period_end,
                rule_version=found.rule_version,
                finding_key=found.key,
                sequence=action.sequence,
            )
            session.add(finding)
            session.flush()
            session.add(
                m.FindingEvent(
                    tenant_id=tenant_id,
                    finding_id=finding.id,
                    from_state=None,
                    to_state=state,
                    reason="Found by the engine.",
                    actor=ENGINE,
                )
            )
            done["created"] += 1
        elif isinstance(action, fd.Update):
            finding = _finding(session, tenant_id, UUID(action.finding_id))
            finding.amount_minor = action.detected.amount_minor
            finding.evidence = action.detected.evidence()
            finding.rule_version = action.detected.rule_version
            done["updated"] += 1
        else:
            move_finding(
                session,
                tenant_id,
                UUID(action.finding_id),
                action.to_state,
                actor=ENGINE,
                reason=action.reason,
            )
            done["moved"] += 1
    session.flush()
    return done


def _finding(session: Session, tenant_id: UUID, finding_id: UUID) -> m.Finding:
    finding = session.scalar(
        select(m.Finding)
        .where(m.Finding.tenant_id == tenant_id, m.Finding.id == finding_id)
        .with_for_update()
    )
    if finding is None:
        raise FindingNotFound(f"No finding {finding_id}.")
    return finding


def move_finding(
    session: Session,
    tenant_id: UUID,
    finding_id: UUID,
    to_state: str,
    *,
    actor: str,
    reason: str | None = None,
) -> m.Finding:
    """Move a finding to a new state and record the move. Refuses a move the
    lifecycle does not allow."""
    finding = _finding(session, tenant_id, finding_id)
    fd.check_transition(finding.state, to_state)
    session.add(
        m.FindingEvent(
            tenant_id=tenant_id,
            finding_id=finding.id,
            from_state=finding.state,
            to_state=to_state,
            reason=reason,
            actor=actor,
        )
    )
    finding.state = to_state
    session.flush()
    return finding
