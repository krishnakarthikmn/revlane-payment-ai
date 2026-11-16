"""The queue: runs waiting in Postgres, claimed one at a time.

`agent_run` is the queue. A unique index allows one active run per tenant per
playbook, so queueing a playbook that is already waiting or running returns
the run that is there. A worker claims the oldest waiting run with
`FOR UPDATE SKIP LOCKED`; a run whose worker died is claimed again once its
lock is stale, and resumes from its checkpoint.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert

from revlane.connectors.base import IST
from revlane.db import models as m
from revlane.db.session import admin_session, tenant_session
from revlane.harness import run as run_loop
from revlane.harness.playbook import get as get_playbook
from revlane.llm.client import ModelClient

STALE_AFTER = timedelta(minutes=15)
ACTIVE = text("status IN ('queued', 'running')")


def enqueue(
    tenant_id: UUID,
    playbook: str,
    *,
    trigger: dict[str, Any] | None = None,
    as_of: date | None = None,
) -> tuple[UUID, bool]:
    """Queue a run. Returns the run's id and whether it is new: with a run of
    this playbook already waiting or running, that run is returned instead."""
    book = get_playbook(playbook)
    r = m.AgentRun
    with tenant_session(tenant_id) as session:
        created = session.scalar(
            insert(r)
            .values(
                tenant_id=tenant_id,
                playbook=book.name,
                playbook_version=book.version,
                trigger=trigger or {"kind": "manual"},
                as_of=as_of or datetime.now(tz=IST).date(),
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "playbook"], index_where=ACTIVE)
            .returning(r.id)
        )
        if created is not None:
            return created, True
        active = session.scalar(
            select(r.id).where(
                r.tenant_id == tenant_id,
                r.playbook == book.name,
                r.status.in_(m.ACTIVE_RUN_STATUSES),
            )
        )
        assert active is not None
        return active, False


def claim_next(worker: str, *, stale_after: timedelta = STALE_AFTER) -> tuple[UUID, UUID] | None:
    """Claim the oldest run that is waiting, or whose worker has gone quiet.

    The one query in the harness that looks across tenants: a worker serves
    them all. It reads and writes queue columns only; the run itself is then
    carried out inside that tenant's own session.
    """
    r = m.AgentRun
    now = datetime.now(tz=UTC)
    with admin_session() as session:
        run = session.scalar(
            select(r)
            .where(
                or_(
                    r.status == "queued",
                    (r.status == "running") & (r.locked_at < now - stale_after),
                )
            )
            .order_by(r.created_at, r.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if run is None:
            return None
        _mark_claimed(run, worker, now)
        return run.tenant_id, run.id


def _mark_claimed(run: m.AgentRun, worker: str, now: datetime) -> None:
    run.status = "running"
    run.locked_by = worker
    run.locked_at = now
    run.attempts += 1
    run.started_at = run.started_at or now


def claim(tenant_id: UUID, run_id: UUID, worker: str) -> bool:
    """Claim one particular waiting run."""
    r = m.AgentRun
    with tenant_session(tenant_id) as session:
        run = session.scalar(
            select(r)
            .where(r.tenant_id == tenant_id, r.id == run_id, r.status == "queued")
            .with_for_update(skip_locked=True)
        )
        if run is None:
            return False
        _mark_claimed(run, worker, datetime.now(tz=UTC))
        return True


def work_once(worker: str, model: ModelClient) -> m.AgentRun | None:
    """Claim and carry out one run. None when nothing is waiting."""
    claimed = claim_next(worker)
    if claimed is None:
        return None
    return run_loop.execute(*claimed, model)


def run_now(
    tenant_id: UUID,
    playbook: str,
    model: ModelClient,
    *,
    trigger: dict[str, Any] | None = None,
    as_of: date | None = None,
    worker: str = "inline",
) -> m.AgentRun:
    """Queue a run and carry it out here, for the command line and for tests."""
    run_id, _ = enqueue(tenant_id, playbook, trigger=trigger, as_of=as_of)
    claim(tenant_id, run_id, worker)
    return run_loop.execute(tenant_id, run_id, model)
