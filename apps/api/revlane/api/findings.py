"""Findings: what the engines found, and where each one stands."""

import base64
import binascii
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, tuple_

from revlane.api.deps import Acting, ExistingTenant
from revlane.audit import store
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.engine.findings import InvalidTransition

router = APIRouter(prefix="/findings")

State = Literal["lost", "recoverable", "claimed", "recovered", "written_off"]
FindingType = Literal[
    "paid_auto_refunded",
    "settled_short",
    "captured_not_settled",
    "fee_above_contract",
    "tax_on_refunded_fee",
    "fee_not_in_contract",
]


def summary(finding: m.Finding) -> dict[str, Any]:
    return {
        "id": str(finding.id),
        "type": finding.type,
        "provider": finding.provider,
        "amount_minor": finding.amount_minor,
        "cause": finding.cause,
        "state": finding.state,
        "period_start": finding.period_start.isoformat() if finding.period_start else None,
        "period_end": finding.period_end.isoformat() if finding.period_end else None,
        "rule_version": finding.rule_version,
        # How many records of each kind the finding rests on; the ids are in the detail.
        "record_counts": {
            kind: len(records) for kind, records in finding.evidence.items() if kind != "items"
        },
        "created_at": finding.created_at.isoformat(),
        "updated_at": finding.updated_at.isoformat(),
    }


def detail(finding: m.Finding, events: list[m.FindingEvent]) -> dict[str, Any]:
    return summary(finding) | {
        "evidence": finding.evidence,
        "events": [
            {
                "from_state": event.from_state,
                "to_state": event.to_state,
                "reason": event.reason,
                "actor": event.actor,
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in events
        ],
    }


def encode_cursor(finding: m.Finding) -> str:
    raw = f"{finding.created_at.isoformat()}|{finding.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        created_at, finding_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(created_at), UUID(finding_id)
    except (ValueError, binascii.Error) as error:
        raise HTTPException(422, "cursor: not a cursor this API gave out") from error


@router.get("")
def list_findings(
    tenant_id: ExistingTenant,
    state: State | None = None,
    type: FindingType | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    f = m.Finding
    query = select(f).where(f.tenant_id == tenant_id)
    if state:
        query = query.where(f.state == state)
    if type:
        query = query.where(f.type == type)
    if cursor:
        query = query.where(tuple_(f.created_at, f.id) < decode_cursor(cursor))
    with tenant_session(tenant_id) as session:
        rows = list(
            session.scalars(query.order_by(f.created_at.desc(), f.id.desc()).limit(limit + 1))
        )
    page = rows[:limit]
    return {
        "items": [summary(finding) for finding in page],
        "next_cursor": encode_cursor(page[-1]) if len(rows) > limit else None,
    }


def _detail(tenant_id: UUID, finding_id: UUID) -> dict[str, Any]:
    with tenant_session(tenant_id) as session:
        finding = session.scalar(
            select(m.Finding).where(m.Finding.tenant_id == tenant_id, m.Finding.id == finding_id)
        )
        if finding is None:
            raise HTTPException(404, f"No finding {finding_id}.")
        event = m.FindingEvent
        events = list(
            session.scalars(
                select(event)
                .where(event.tenant_id == tenant_id, event.finding_id == finding_id)
                .order_by(event.occurred_at, event.id)
            )
        )
        return detail(finding, events)


@router.get("/{finding_id}")
def get_finding(finding_id: UUID, tenant_id: ExistingTenant) -> dict[str, Any]:
    return _detail(tenant_id, finding_id)


class Transition(BaseModel):
    to_state: State
    reason: str | None = Field(default=None, max_length=500)


@router.post("/{finding_id}/transitions")
def move(finding_id: UUID, body: Transition, who: Acting) -> dict[str, Any]:
    """Record that a person moved a finding on: wrote it off, say, or sent the claim.

    Nothing is sent to anyone: this only records where the finding stands.
    """
    try:
        with tenant_session(who.tenant_id) as session:
            store.move_finding(
                session,
                who.tenant_id,
                finding_id,
                body.to_state,
                actor=f"user:{who.user_id}",
                reason=body.reason,
            )
    except store.FindingNotFound as error:
        raise HTTPException(404, str(error)) from error
    except InvalidTransition as error:
        raise HTTPException(409, str(error)) from error
    return _detail(who.tenant_id, finding_id)
