"""Keeps `attempt_status` in step with `attempt_event`, and raises an alert for
every provider status no mapping knows.

The rules themselves are in `revlane.engine.status`; this module only reads
events, calls them, and stores the answers.
"""

from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime
from itertools import islice
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from revlane.connectors.statuses import UNKNOWN
from revlane.db import models as m
from revlane.engine.status import RULES_VERSION, Event, StatusRules, derive

CHUNK = 5_000
AttemptKey = tuple[str, str]  # provider, attempt id


def rules_for(session: Session, tenant_id: UUID) -> StatusRules:
    policy = session.scalar(select(m.Tenant.policy).where(m.Tenant.id == tenant_id))
    return StatusRules.from_policy(policy or {})


def chunks(items: Iterable[str], size: int = CHUNK) -> Iterator[list[str]]:
    iterator = iter(items)
    while chunk := list(islice(iterator, size)):
        yield chunk


def rebuild(
    session: Session,
    tenant_id: UUID,
    now: datetime,
    attempts: Iterable[AttemptKey] | None = None,
) -> int:
    """Derive the status of the given attempts again, or of every attempt.

    Returns how many were derived. Run it for everything after a rule changes.
    """
    rules = rules_for(session, tenant_id)
    by_provider: dict[str, set[str]] = defaultdict(set)
    if attempts is None:
        keys = session.execute(
            select(m.PaymentAttempt.provider, m.PaymentAttempt.attempt_id).where(
                m.PaymentAttempt.tenant_id == tenant_id
            )
        )
        for provider, attempt_id in keys:
            by_provider[provider].add(attempt_id)
    else:
        for provider, attempt_id in attempts:
            by_provider[provider].add(attempt_id)

    derived = 0
    for provider, ids in sorted(by_provider.items()):
        for chunk in chunks(sorted(ids)):
            derived += _rebuild_chunk(session, tenant_id, provider, chunk, now, rules)
    return derived


def _rebuild_chunk(
    session: Session,
    tenant_id: UUID,
    provider: str,
    attempt_ids: list[str],
    now: datetime,
    rules: StatusRules,
) -> int:
    event = m.AttemptEvent
    rows = session.execute(
        select(
            event.attempt_id,
            event.provider_status,
            event.occurred_at,
            event.provider_stage,
            event.provider_error_code,
        )
        .where(
            event.tenant_id == tenant_id,
            event.provider == provider,
            event.attempt_id.in_(attempt_ids),
        )
        .order_by(event.attempt_id, event.occurred_at, event.ingested_at, event.id)
    )
    history: dict[str, list[Event]] = defaultdict(list)
    for row in rows:
        history[row.attempt_id].append(
            Event(
                row.provider_status,
                row.occurred_at,
                row.provider_stage,
                row.provider_error_code,
            )
        )
    if not history:
        return 0

    values = []
    for attempt_id, events in history.items():
        result = derive(provider, events, now, rules)
        values.append(
            {
                "tenant_id": tenant_id,
                "provider": provider,
                "attempt_id": attempt_id,
                "status": result.status,
                "failure_reason": result.failure_reason,
                "provider_status": result.provider_status,
                "last_event_at": result.last_event_at,
                "changes_at": result.changes_at,
                "rule_version": RULES_VERSION,
                "derived_at": now,
            }
        )
    statement = insert(m.AttemptStatus)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["tenant_id", "provider", "attempt_id"],
            set_={
                name: statement.excluded[name]
                for name in (
                    "status",
                    "failure_reason",
                    "provider_status",
                    "last_event_at",
                    "changes_at",
                    "rule_version",
                    "derived_at",
                )
            }
            | {"updated_at": func.now()},
        ),
        values,
    )
    return len(values)


def refresh_due(session: Session, tenant_id: UUID, now: datetime) -> int:
    """Derive again every attempt whose clock rule has come due."""
    status = m.AttemptStatus
    due = session.execute(
        select(status.provider, status.attempt_id).where(
            status.tenant_id == tenant_id, status.changes_at <= now
        )
    ).all()
    return rebuild(session, tenant_id, now, [(row[0], row[1]) for row in due])


def unknown_statuses(session: Session, tenant_id: UUID) -> dict[AttemptKey, int]:
    """(provider, the provider's status word) -> how many attempts are stuck on it."""
    status = m.AttemptStatus
    rows = session.execute(
        select(status.provider, status.provider_status, func.count())
        .where(status.tenant_id == tenant_id, status.status == UNKNOWN)
        .group_by(status.provider, status.provider_status)
    )
    return {(provider, word): count for provider, word, count in rows}


def raise_alerts(session: Session, tenant_id: UUID, now: datetime) -> dict[AttemptKey, int]:
    """One open alert per unmapped status; resolve those that no longer apply."""
    unknown = unknown_statuses(session, tenant_id)
    alert = m.Alert
    if unknown:
        statement = insert(alert)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["tenant_id", "kind", "provider", "subject"],
                set_={
                    "record_count": statement.excluded.record_count,
                    "last_seen_at": statement.excluded.last_seen_at,
                    "state": "open",
                    "updated_at": func.now(),
                },
            ),
            [
                {
                    "tenant_id": tenant_id,
                    "kind": "unmapped_status",
                    "provider": provider,
                    "subject": word,
                    "record_count": count,
                    "state": "open",
                    "first_seen_at": now,
                    "last_seen_at": now,
                }
                for (provider, word), count in sorted(unknown.items())
            ],
        )
    open_alerts = session.execute(
        select(alert.id, alert.provider, alert.subject).where(
            alert.tenant_id == tenant_id, alert.kind == "unmapped_status", alert.state == "open"
        )
    ).all()
    cleared = [row.id for row in open_alerts if (row.provider, row.subject) not in unknown]
    if cleared:
        session.execute(
            update(alert)
            .where(alert.tenant_id == tenant_id, alert.id.in_(cleared))
            .values(state="resolved", record_count=0)
        )
    return unknown
