"""What the console's real screens show, in one request, and the actions
behind its buttons: run the audit, edit an agent's rules, simulate them."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, ConfigDict, StrictInt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from revlane.api.deps import ACT, ADMINISTER, ExistingTenant
from revlane.audit import store
from revlane.audit.load import load_inputs
from revlane.audit.run import FAMILIES
from revlane.config import get_settings
from revlane.connectors.base import IST
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.engine import findings as fd
from revlane.engine.fees import FeeRules, check_fees
from revlane.engine.findings import Detected
from revlane.engine.matching import MatchRules, three_way_match
from revlane.harness import queue, run
from revlane.llm.client import get_client
from revlane.llm.stub import LEAKS, PROVIDERS
from revlane.money import format_inr, group_indian

router = APIRouter()

# The two real agents, by the key the console knows them by.
AGENTS = {"recon": "reconciliation", "fees": "fee_audit"}
AUDIT_ORDER = ("leakage_audit", "reconciliation", "fee_audit")
RECENT = timedelta(days=7)
MAX_NOTIFICATIONS = 12
ACCENT, QUIET = "#D97757", "#C9C5B7"


class Rule(BaseModel):
    key: str
    label: str
    low: int
    high: int


# What a person may tune, and where it lives in the tenant's policy.
RULES: dict[str, tuple[str, tuple[Rule, ...]]] = {
    "reconciliation": (
        "matching",
        (
            Rule(
                key="grace_working_days",
                label="Flag unsettled after (working days past the cycle)",
                low=0,
                high=30,
            ),
            Rule(
                key="bank_window_days",
                label="Days a bank credit may trail its settlement",
                low=0,
                high=30,
            ),
            Rule(
                key="group_tolerance_minor",
                label="Tolerance per 1,000 lines (paise)",
                low=0,
                high=1_000_000,
            ),
        ),
    ),
    "fee_audit": (
        "fees",
        (
            Rule(
                key="tolerance_minor",
                label="Overcharge threshold per line (paise)",
                low=0,
                high=10_000_000,
            ),
            Rule(
                key="tolerance_bps",
                label="Or this share of the line (basis points)",
                low=0,
                high=1_000,
            ),
        ),
    ),
}


def _playbook(name: str) -> str:
    if name not in RULES:
        raise HTTPException(404, f"No real agent called {name}.")
    return name


def _current_rules(policy: dict[str, Any], playbook: str) -> dict[str, int]:
    section, rules = RULES[playbook]
    own = policy.get(section) or {}
    defaults: Any = MatchRules() if playbook == "reconciliation" else FeeRules()
    return {rule.key: int(own.get(rule.key, getattr(defaults, rule.key))) for rule in rules}


def _where(provider: str | None) -> str:
    return PROVIDERS.get(provider or "", "the provider")


def _agent(
    session: Session, tenant_id: UUID, playbook: str, policy: dict[str, Any]
) -> dict[str, Any]:
    real = {row.id for row in store.existing_findings(session, tenant_id) if row.covers}
    f = m.Finding
    findings = [
        row
        for row in session.scalars(
            select(f)
            .where(f.tenant_id == tenant_id, f.type.in_(FAMILIES[playbook]))
            .order_by(f.amount_minor.desc(), f.id)
        )
        if str(row.id) in real
    ]
    since = datetime.now(tz=UTC) - RECENT
    lost = sum(row.amount_minor for row in findings if row.created_at >= since)
    total = sum(row.amount_minor for row in findings)
    causes = [
        {
            "label": f"{LEAKS.get(row.type, row.type)} at {_where(row.provider)}",
            # A whole-number share of the total, worked out here and not by a model.
            "pct": (row.amount_minor * 100 + total // 2) // total if total else 0,
        }
        for row in findings[:3]
    ]
    last = session.scalar(
        select(m.AgentRun)
        .where(m.AgentRun.tenant_id == tenant_id, m.AgentRun.playbook == playbook)
        .order_by(m.AgentRun.created_at.desc(), m.AgentRun.id.desc())
        .limit(1)
    )
    summarised = session.scalar(
        select(m.AgentRun)
        .where(
            m.AgentRun.tenant_id == tenant_id,
            m.AgentRun.playbook == playbook,
            m.AgentRun.output.is_not(None),
        )
        .order_by(m.AgentRun.created_at.desc(), m.AgentRun.id.desc())
        .limit(1)
    )
    p = m.Proposal
    proposals: list[dict[str, Any]] = []
    for proposal in session.scalars(
        select(p)
        .where(p.tenant_id == tenant_id, p.playbook == playbook)
        .order_by(p.created_at, p.id)
    ):
        action = session.scalar(
            select(m.Action).where(
                m.Action.tenant_id == tenant_id, m.Action.proposal_id == proposal.id
            )
        )
        summary = proposal.summary or {}
        proposals.append(
            {
                "id": str(proposal.id),
                "action_type": proposal.action_type,
                "status": proposal.status,
                "title": (summary.get("rendered") or {}).get("title") or proposal.action_type,
                "rationale": (summary.get("rendered") or {}).get("rationale") or "",
                "amount_minor": int(summary.get("amount_minor") or 0),
                "claim_ids": (action.result.get("claims") or []) if action else [],
                "documents": (action.result.get("documents") or []) if action else [],
            }
        )
    # Waiting first: money owed by a provider before lists for the team, largest first.
    proposals.sort(
        key=lambda item: (
            item["status"] != "pending",
            item["action_type"] == "draft_reoffer_list",
            -item["amount_minor"],
        )
    )
    setting = session.scalar(
        select(m.AgentSetting).where(
            m.AgentSetting.tenant_id == tenant_id, m.AgentSetting.playbook == playbook
        )
    )
    records = sum(len(row.evidence.get("items", [])) for row in findings)
    waiting = [item for item in proposals if item["status"] == "pending"]
    tools: list[str] = []
    for item in summarised.checkpoint.get("exchanges", []) if summarised else []:
        if item["name"] not in tools:
            tools.append(item["name"])
    _, rules = RULES[playbook]
    current = _current_rules(policy, playbook)
    return {
        "playbook": playbook,
        "autonomy_level": setting.autonomy_level if setting else 1,
        "enabled": setting.enabled if setting else True,
        "lost_minor": lost,
        "headline": ((summarised.output or {}).get("rendered") or {}).get("headline")
        if summarised
        else None,
        "causes": causes,
        "tools": " → ".join(tools),
        "proposals": proposals,
        "findings": [
            {
                "id": str(row.id),
                "label": f"{LEAKS.get(row.type, row.type)} at {_where(row.provider)}",
                "state": row.state,
                "amount_minor": row.amount_minor,
            }
            for row in findings
        ],
        "meta": [
            {"k": "Findings", "v": group_indian(len(findings))},
            {"k": "Records", "v": group_indian(records)},
            {"k": "Waiting for you", "v": group_indian(len(waiting))},
            {"k": "At stake", "v": format_inr(sum(item["amount_minor"] for item in waiting))},
        ],
        "rules": [
            {"key": rule.key, "label": rule.label, "value": current[rule.key]} for rule in rules
        ],
        "last_run": {"status": last.status, "error": last.error} if last else None,
    }


def _notifications(session: Session, tenant_id: UUID) -> list[dict[str, Any]]:
    """Built from what happened to findings and proposals, newest first."""
    notes: list[tuple[datetime, dict[str, Any]]] = []
    family_key = {kind: key for key, playbook in AGENTS.items() for kind in FAMILIES[playbook]}
    event, f = m.FindingEvent, m.Finding
    rows = session.execute(
        select(event, f)
        .join(f, (f.tenant_id == event.tenant_id) & (f.id == event.finding_id))
        .where(event.tenant_id == tenant_id)
        .order_by(event.occurred_at.desc(), event.id.desc())
        .limit(MAX_NOTIFICATIONS)
    )
    for happened, finding in rows:
        what = f"{LEAKS.get(finding.type, finding.type)} at {_where(finding.provider)}"
        amount = format_inr(finding.amount_minor)
        if happened.from_state is None:
            title, dot = f"New finding: {amount}", ACCENT
        elif happened.to_state == fd.RECOVERED:
            title, dot = f"Recovered: {amount}", ACCENT
        elif happened.to_state == fd.CLAIMED:
            title, dot = f"Claim drafted: {amount}", QUIET
        else:
            title, dot = f"Finding {happened.to_state.replace('_', ' ')}: {amount}", QUIET
        notes.append(
            (
                happened.occurred_at,
                {
                    "id": str(happened.id),
                    "title": title,
                    "body": what + ".",
                    "dot": dot,
                    "link": f"/agents/{family_key.get(finding.type, 'recon')}",
                },
            )
        )
    p = m.Proposal
    playbook_key = {playbook: key for key, playbook in AGENTS.items()}
    for proposal in session.scalars(
        select(p)
        .where(p.tenant_id == tenant_id, p.status == "pending", p.playbook.in_(playbook_key))
        .order_by(p.created_at.desc())
        .limit(MAX_NOTIFICATIONS)
    ):
        notes.append(
            (
                proposal.created_at,
                {
                    "id": str(proposal.id),
                    "title": "A fix is waiting for you",
                    "body": ((proposal.summary or {}).get("rendered") or {}).get("title") or "",
                    "dot": ACCENT,
                    "link": "/approvals",
                },
            )
        )
    notes.sort(key=lambda note: note[0], reverse=True)
    return [note | {"time": moment.isoformat()} for moment, note in notes[:MAX_NOTIFICATIONS]]


@router.get("/console")
def console(tenant_id: ExistingTenant) -> dict[str, Any]:
    settings = get_settings()
    with tenant_session(tenant_id) as session:
        tenant = session.scalar(select(m.Tenant).where(m.Tenant.id == tenant_id))
        assert tenant is not None
        policy = tenant.policy or {}

        def count(model: type[Any]) -> int:
            return (
                session.scalar(
                    select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
                )
                or 0
            )

        r = m.AgentRun
        running = session.scalar(
            select(func.count())
            .select_from(r)
            .where(r.tenant_id == tenant_id, r.status.in_(m.ACTIVE_RUN_STATUSES))
        )
        finished = session.scalar(
            select(func.max(r.finished_at)).where(
                r.tenant_id == tenant_id, r.playbook == "leakage_audit", r.status == "succeeded"
            )
        )
        return {
            "merchant": {"name": tenant.name},
            # Shown as a pill while the tenant is the synthetic merchant.
            "sample_data": tenant_id == settings.demo_tenant_id,
            "setup": {
                "has_data": count(m.PaymentAttempt) + count(m.Settlement) + count(m.BankCredit) > 0,
                "has_contracts": count(m.FeeContract) > 0,
                "has_audit": finished is not None,
            },
            "audit": {
                "running": bool(running),
                "last_finished_at": finished.isoformat() if finished else None,
            },
            "agents": {
                key: _agent(session, tenant_id, playbook, policy)
                for key, playbook in AGENTS.items()
            },
            "notifications": _notifications(session, tenant_id),
        }


# --- Run the audit ---------------------------------------------------------------------------


class AuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date | None = None


def _work(tenant_id: UUID, run_ids: list[UUID]) -> None:
    """Carry the runs out here, in order, when there is no worker to do it."""
    model = get_client()
    for run_id in run_ids:
        if queue.claim(tenant_id, run_id, "api"):
            run.execute(tenant_id, run_id, model)


@router.post("/audit", status_code=202, dependencies=ACT)
def run_audit(
    tenant_id: ExistingTenant, background: BackgroundTasks, body: AuditRequest | None = None
) -> dict[str, Any]:
    """Queue the leakage audit, then reconciliation and the fee audit.

    The first matches everything and checks every fee; the other two read
    what it found and make their proposals.
    """
    as_of = (body.as_of if body else None) or datetime.now(tz=IST).date()
    run_ids = [
        queue.enqueue(tenant_id, playbook, trigger={"kind": "manual"}, as_of=as_of)[0]
        for playbook in AUDIT_ORDER
    ]
    if get_settings().agent_runs_inline:
        background.add_task(_work, tenant_id, run_ids)
    return {"run_ids": [str(run_id) for run_id in run_ids], "as_of": as_of.isoformat()}


# --- Rules, and trying them out ----------------------------------------------------------------


class RulesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: dict[str, StrictInt]


def _checked(playbook: str, body: RulesBody) -> dict[str, int]:
    _, rules = RULES[playbook]
    known = {rule.key: rule for rule in rules}
    for key, value in body.rules.items():
        if key not in known:
            raise HTTPException(422, f"rules.{key}: this agent has no such rule.")
        if not known[key].low <= value <= known[key].high:
            raise HTTPException(
                422, f"rules.{key}: must be between {known[key].low} and {known[key].high}."
            )
    return dict(body.rules)


@router.get("/agents/{name}/rules")
def get_rules(name: str, tenant_id: ExistingTenant) -> dict[str, Any]:
    playbook = _playbook(name)
    with tenant_session(tenant_id) as session:
        policy = session.scalar(select(m.Tenant.policy).where(m.Tenant.id == tenant_id)) or {}
    return {"playbook": playbook, "rules": _current_rules(policy, playbook)}


@router.put("/agents/{name}/rules", dependencies=ADMINISTER)
def put_rules(name: str, body: RulesBody, tenant_id: ExistingTenant) -> dict[str, Any]:
    """Save the rules. They apply the next time the audit runs."""
    playbook = _playbook(name)
    changed = _checked(playbook, body)
    section, _ = RULES[playbook]
    with tenant_session(tenant_id) as session:
        tenant = session.scalar(select(m.Tenant).where(m.Tenant.id == tenant_id).with_for_update())
        assert tenant is not None
        policy = dict(tenant.policy or {})
        policy[section] = dict(policy.get(section) or {}) | changed
        tenant.policy = policy
        return {"playbook": playbook, "rules": _current_rules(policy, playbook)}


def _measure(findings: list[Detected]) -> dict[str, int]:
    return {
        "finding_count": len(findings),
        "amount_minor": sum(finding.amount_minor for finding in findings),
        "record_count": sum(len(finding.items) for finding in findings),
    }


@router.post("/agents/{name}/simulate", dependencies=ACT)
def simulate(name: str, body: RulesBody, tenant_id: ExistingTenant) -> dict[str, Any]:
    """Replay the engine over the records on file with the rules given, and
    with the rules in force. Nothing is stored."""
    playbook = _playbook(name)
    changed = _checked(playbook, body)
    with tenant_session(tenant_id) as session:
        inputs = load_inputs(session, tenant_id)
        policy = session.scalar(select(m.Tenant.policy).where(m.Tenant.id == tenant_id)) or {}
    as_of = datetime.now(tz=IST).date()
    section, _ = RULES[playbook]
    edited = dict(policy.get(section) or {}) | changed

    def replay(rules: dict[str, Any]) -> list[Detected]:
        if playbook == "reconciliation":
            return three_way_match(
                orders=inputs.orders,
                attempts=inputs.attempts,
                refunds=inputs.refunds,
                settlements=inputs.settlements,
                lines=inputs.lines,
                credits=inputs.credits,
                as_of=as_of,
                rules=MatchRules.from_policy(rules),
                rejected=inputs.rejected,
            ).findings
        return check_fees(
            lines=inputs.lines,
            settlements=inputs.settlements,
            attempts=inputs.attempts,
            refunds=inputs.refunds,
            contracts=inputs.contracts,
            rules=FeeRules.from_policy(rules),
        ).findings

    return {
        "playbook": playbook,
        "with_these_rules": _measure(replay(edited)),
        "with_current_rules": _measure(replay(dict(policy.get(section) or {}))),
    }
