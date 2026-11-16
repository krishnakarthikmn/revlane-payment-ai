"""Agents: playbooks and their settings, runs, proposals, decisions, claims
and the audit log."""

import base64
import binascii
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert

from revlane.api.deps import ACT, ADMINISTER, Acting, ExistingTenant
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.harness import approvals, playbook, queue
from revlane.storage import get_storage
from revlane.storage.base import ObjectNotFound

router = APIRouter()

Cursor = Annotated[str | None, Query()]
Limit = Annotated[int, Query(ge=1, le=100)]


def _playbook(name: str) -> playbook.Playbook:
    try:
        return playbook.get(name)
    except playbook.PlaybookError as error:
        raise HTTPException(404, str(error)) from error


def _encode(moment: datetime, row_id: UUID) -> str:
    return base64.urlsafe_b64encode(f"{moment.isoformat()}|{row_id}".encode()).decode()


def _decode(cursor: str) -> tuple[datetime, UUID]:
    try:
        moment, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(moment), UUID(row_id)
    except (ValueError, binascii.Error) as error:
        raise HTTPException(422, "cursor: not a cursor this API gave out") from error


# --- Playbooks and settings ---------------------------------------------------------


def _run_summary(run: m.AgentRun) -> dict[str, Any]:
    output = run.output or {}
    return {
        "id": str(run.id),
        "playbook": run.playbook,
        "playbook_version": run.playbook_version,
        "status": run.status,
        "as_of": run.as_of.isoformat(),
        "error": run.error,
        "model": run.model,
        "attempts": run.attempts,
        # The summary as a person reads it: every figure put in by code.
        "headline": (output.get("rendered") or {}).get("headline"),
        "points": (output.get("rendered") or {}).get("points", []),
        "top_cause": output.get("top_cause"),
        "confidence": output.get("confidence"),
        "proposal_ids": output.get("proposal_ids", []),
        "tool_calls": [item["name"] for item in run.checkpoint.get("exchanges", [])],
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.get("/agents")
def list_agents(tenant_id: ExistingTenant) -> dict[str, Any]:
    with tenant_session(tenant_id) as session:
        settings = {
            row.playbook: row
            for row in session.scalars(
                select(m.AgentSetting).where(m.AgentSetting.tenant_id == tenant_id)
            )
        }
        items = []
        for name, book in playbook.load_all().items():
            last = session.scalar(
                select(m.AgentRun)
                .where(m.AgentRun.tenant_id == tenant_id, m.AgentRun.playbook == name)
                .order_by(m.AgentRun.created_at.desc(), m.AgentRun.id.desc())
                .limit(1)
            )
            own = settings.get(name)
            items.append(
                {
                    "playbook": name,
                    "version": book.version,
                    "read_tools": book.read_tools,
                    "actions": [rule.type for rule in book.actions],
                    "autonomy_level": own.autonomy_level if own else 1,
                    "enabled": own.enabled if own else True,
                    "last_run": _run_summary(last) if last else None,
                }
            )
    return {"items": items, "next_cursor": None}


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 0 Observe, 1 Suggest. Batch and Auto are not built in the first version.
    autonomy_level: Literal[0, 1]
    enabled: bool


@router.put("/agents/{name}/settings", dependencies=ADMINISTER)
def set_settings(name: str, body: Settings, tenant_id: ExistingTenant) -> dict[str, Any]:
    _playbook(name)
    with tenant_session(tenant_id) as session:
        statement = insert(m.AgentSetting).values(
            tenant_id=tenant_id,
            playbook=name,
            autonomy_level=body.autonomy_level,
            enabled=body.enabled,
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["tenant_id", "playbook"],
                set_={"autonomy_level": body.autonomy_level, "enabled": body.enabled},
            )
        )
    return {"playbook": name, "autonomy_level": body.autonomy_level, "enabled": body.enabled}


# --- Runs -----------------------------------------------------------------------------


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date | None = None


@router.post("/agents/{name}/runs", status_code=202, dependencies=ACT)
def start_run(
    name: str, tenant_id: ExistingTenant, body: RunRequest | None = None
) -> dict[str, Any]:
    """Queue a run. A worker carries it out; ask for the run to see how it went.

    With a run of this playbook already waiting or running, that run is returned.
    """
    _playbook(name)
    run_id, created = queue.enqueue(
        tenant_id, name, trigger={"kind": "manual"}, as_of=body.as_of if body else None
    )
    return _get_run(tenant_id, run_id) | {"created": created}


def _get_run(tenant_id: UUID, run_id: UUID) -> dict[str, Any]:
    with tenant_session(tenant_id) as session:
        run = session.scalar(
            select(m.AgentRun).where(m.AgentRun.tenant_id == tenant_id, m.AgentRun.id == run_id)
        )
        if run is None:
            raise HTTPException(404, f"No run {run_id}.")
        return _run_summary(run)


@router.get("/agents/runs/{run_id}")
def get_run(run_id: UUID, tenant_id: ExistingTenant) -> dict[str, Any]:
    return _get_run(tenant_id, run_id)


@router.get("/agents/runs/{run_id}/trace")
def get_trace(run_id: UUID, tenant_id: ExistingTenant) -> dict[str, Any]:
    """Every model turn and tool call of a run, in order, with how long each took."""
    with tenant_session(tenant_id) as session:
        run = session.scalar(
            select(m.AgentRun).where(m.AgentRun.tenant_id == tenant_id, m.AgentRun.id == run_id)
        )
        if run is None:
            raise HTTPException(404, f"No run {run_id}.")
        return {
            "trace_id": str(run.id),
            "playbook": run.playbook,
            "status": run.status,
            "spans": run.checkpoint.get("trace", []),
            "usage": run.checkpoint.get("usage", {}),
        }


# --- Proposals and decisions -------------------------------------------------------------


def _proposal(proposal: m.Proposal, decisions: list[m.Approval] | None = None) -> dict[str, Any]:
    summary = proposal.summary or {}
    body = {
        "id": str(proposal.id),
        "playbook": proposal.playbook,
        "action_type": proposal.action_type,
        "status": proposal.status,
        "finding_ids": [str(finding_id) for finding_id in proposal.finding_ids],
        "title": (summary.get("rendered") or {}).get("title"),
        "rationale": (summary.get("rendered") or {}).get("rationale"),
        "amount_minor": summary.get("amount_minor"),
        "providers": summary.get("providers", []),
        "created_at": proposal.created_at.isoformat(),
    }
    if decisions is not None:
        body["decisions"] = [
            {
                "approver_id": str(decision.approver_id),
                "decision": decision.decision,
                "reason": decision.reason,
                "decided_at": decision.decided_at.isoformat(),
            }
            for decision in decisions
        ]
    return body


@router.get("/proposals")
def list_proposals(
    tenant_id: ExistingTenant,
    status: Literal["pending", "approved", "rejected", "expired"] | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> dict[str, Any]:
    p = m.Proposal
    query = select(p).where(p.tenant_id == tenant_id)
    if status:
        query = query.where(p.status == status)
    if cursor:
        query = query.where(tuple_(p.created_at, p.id) < _decode(cursor))
    with tenant_session(tenant_id) as session:
        rows = list(
            session.scalars(query.order_by(p.created_at.desc(), p.id.desc()).limit(limit + 1))
        )
    page = rows[:limit]
    return {
        "items": [_proposal(row) for row in page],
        "next_cursor": _encode(page[-1].created_at, page[-1].id) if len(rows) > limit else None,
    }


def _get_proposal(tenant_id: UUID, proposal_id: UUID) -> dict[str, Any]:
    with tenant_session(tenant_id) as session:
        proposal = session.scalar(
            select(m.Proposal).where(
                m.Proposal.tenant_id == tenant_id, m.Proposal.id == proposal_id
            )
        )
        if proposal is None:
            raise HTTPException(404, f"No proposal {proposal_id}.")
        a = m.Approval
        decisions = list(
            session.scalars(
                select(a)
                .where(a.tenant_id == tenant_id, a.proposal_id == proposal_id)
                .order_by(a.decided_at, a.id)
            )
        )
        claims = session.scalars(
            select(m.Claim.id).where(
                m.Claim.tenant_id == tenant_id, m.Claim.proposal_id == proposal_id
            )
        )
        action = session.scalar(
            select(m.Action).where(
                m.Action.tenant_id == tenant_id, m.Action.proposal_id == proposal_id
            )
        )
        return _proposal(proposal, decisions) | {
            "claim_ids": [str(claim_id) for claim_id in claims],
            "documents": action.result.get("documents", []) if action else [],
        }


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: UUID, tenant_id: ExistingTenant) -> dict[str, Any]:
    return _get_proposal(tenant_id, proposal_id)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=500)


@router.post("/proposals/{proposal_id}/decisions")
def decide(proposal_id: UUID, body: DecisionRequest, who: Acting) -> dict[str, Any]:
    """Approve or reject, as the person signed in. The last approval needed
    drafts the documents; nothing is sent."""
    try:
        with tenant_session(who.tenant_id) as session:
            approvals.decide(
                session, who.tenant_id, proposal_id, who.user_id, body.decision, reason=body.reason
            )
    except approvals.ProposalNotFound as error:
        raise HTTPException(404, str(error)) from error
    except approvals.NotAllowed as error:
        raise HTTPException(403, str(error)) from error
    except approvals.AlreadyDecided as error:
        raise HTTPException(409, str(error)) from error
    return _get_proposal(who.tenant_id, proposal_id)


# --- Claims -------------------------------------------------------------------------------


def _claim(claim: m.Claim) -> dict[str, Any]:
    return {
        "id": str(claim.id),
        "proposal_id": str(claim.proposal_id),
        "provider": claim.provider,
        "amount_minor": claim.amount_minor,
        "status": claim.status,
        "created_at": claim.created_at.isoformat(),
    }


@router.get("/claims")
def list_claims(
    tenant_id: ExistingTenant, limit: Limit = 50, cursor: Cursor = None
) -> dict[str, Any]:
    c = m.Claim
    query = select(c).where(c.tenant_id == tenant_id)
    if cursor:
        query = query.where(tuple_(c.created_at, c.id) < _decode(cursor))
    with tenant_session(tenant_id) as session:
        rows = list(
            session.scalars(query.order_by(c.created_at.desc(), c.id.desc()).limit(limit + 1))
        )
    page = rows[:limit]
    return {
        "items": [_claim(row) for row in page],
        "next_cursor": _encode(page[-1].created_at, page[-1].id) if len(rows) > limit else None,
    }


FORMATS = {"pdf": "application/pdf", "txt": "text/plain; charset=utf-8"}


@router.get("/claims/{claim_id}/document.{extension}")
def claim_document(claim_id: UUID, extension: str, tenant_id: ExistingTenant) -> Response:
    """The drafted claim: `document.pdf`, or `document.txt` to paste into an email."""
    if extension not in FORMATS:
        raise HTTPException(404, "A claim comes as document.pdf or document.txt.")
    with tenant_session(tenant_id) as session:
        ref = session.scalar(
            select(m.Claim.document_ref).where(
                m.Claim.tenant_id == tenant_id, m.Claim.id == claim_id
            )
        )
    if ref is None:
        raise HTTPException(404, f"No claim {claim_id}.")
    try:
        content = get_storage().get(tenant_id, f"{ref}.{extension}")
    except ObjectNotFound as error:
        raise HTTPException(404, "The claim's document is no longer stored.") from error
    return Response(
        content=content,
        media_type=FORMATS[extension],
        headers={"Content-Disposition": f'attachment; filename="claim-{claim_id}.{extension}"'},
    )


# --- The audit log ---------------------------------------------------------------------------


@router.get("/audit-log")
def audit_log(
    tenant_id: ExistingTenant,
    kind: Literal["connector_call", "model_call", "tool_call", "approval", "action"] | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """The append-only log, newest first. It holds hashes of payloads, never the payloads."""
    log = m.AuditLog
    query = select(log).where(log.tenant_id == tenant_id)
    if kind:
        query = query.where(log.kind == kind)
    if cursor:
        query = query.where(tuple_(log.occurred_at, log.id) < _decode(cursor))
    with tenant_session(tenant_id) as session:
        rows = list(
            session.scalars(query.order_by(log.occurred_at.desc(), log.id.desc()).limit(limit + 1))
        )
    page = rows[:limit]
    return {
        "items": [
            {
                "id": str(row.id),
                "actor": row.actor,
                "kind": row.kind,
                "ref": row.ref,
                "payload_hash": row.payload_hash,
                "occurred_at": row.occurred_at.isoformat(),
            }
            for row in page
        ],
        "next_cursor": _encode(page[-1].occurred_at, page[-1].id) if len(rows) > limit else None,
    }
