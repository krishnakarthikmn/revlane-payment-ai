"""Operational metrics in the Prometheus text format: sync lag, unmapped
statuses, findings, agent runs and model use.

Counts and totals across all tenants, with no tenant named. This is the one
route that reads across tenants, so it reads aggregates only.
"""

import hmac
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import BigInteger, cast, func, select

from revlane.config import get_settings
from revlane.db import models as m
from revlane.db.session import admin_session

router = APIRouter()

Labels = dict[str, str]
Sample = tuple[Labels, int]


def render(metrics: list[tuple[str, str, str, list[Sample]]]) -> str:
    lines = []
    for name, kind, help_text, samples in metrics:
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
        for labels, value in samples:
            inner = ",".join(f'{key}="{labels[key]}"' for key in sorted(labels))
            lines.append(f"{name}{{{inner}}} {value}" if inner else f"{name} {value}")
    return "\n".join(lines) + "\n"


def collect(now: datetime | None = None) -> str:
    now = now or datetime.now(tz=UTC)
    account, alert, finding, run, log = (
        m.ProviderAccount,
        m.Alert,
        m.Finding,
        m.AgentRun,
        m.AuditLog,
    )
    usage = run.checkpoint["usage"]
    with admin_session() as session:
        synced = session.execute(
            select(account.provider, func.min(account.last_sync_at))
            .where(account.last_sync_at.is_not(None))
            .group_by(account.provider)
        ).all()
        lag = [(provider, last) for provider, last in synced if last is not None]
        unmapped = session.execute(
            select(alert.provider, func.sum(alert.record_count))
            .where(alert.kind == "unmapped_status", alert.state == "open")
            .group_by(alert.provider)
        ).all()
        findings = session.execute(
            select(
                finding.type, finding.state, func.count(), func.sum(finding.amount_minor)
            ).group_by(finding.type, finding.state)
        ).all()
        runs = session.execute(
            select(run.playbook, run.status, func.count()).group_by(run.playbook, run.status)
        ).all()
        by_actor = session.execute(
            select(log.actor, func.count()).where(log.kind == "model_call").group_by(log.actor)
        ).all()
        tokens = session.execute(
            select(
                run.model,
                func.coalesce(func.sum(cast(usage["input_tokens"].astext, BigInteger)), 0),
                func.coalesce(func.sum(cast(usage["output_tokens"].astext, BigInteger)), 0),
            ).group_by(run.model)
        ).all()
    # "agent:fee_audit" and "chat" become the surfaces "agent" and "chat".
    calls: dict[str, int] = {}
    for made_by, number in by_actor:
        surface = made_by.split(":", 1)[0]
        calls[surface] = calls.get(surface, 0) + int(number)
    return render(
        [
            (
                "revlane_sync_lag_seconds",
                "gauge",
                "Seconds since the least recently synced account of each provider last synced.",
                [
                    ({"provider": provider}, max(0, int((now - last).total_seconds())))
                    for provider, last in sorted(lag)
                ],
            ),
            (
                "revlane_unmapped_status_attempts",
                "gauge",
                "Payment attempts whose provider status no mapping knows.",
                [({"provider": provider}, int(count)) for provider, count in sorted(unmapped)],
            ),
            (
                "revlane_findings",
                "gauge",
                "Findings by type and state.",
                [({"type": t, "state": s}, int(n)) for t, s, n, _ in sorted(findings)],
            ),
            (
                "revlane_findings_amount_minor",
                "gauge",
                "Money in findings by type and state, in paise.",
                [({"type": t, "state": s}, int(a or 0)) for t, s, _, a in sorted(findings)],
            ),
            (
                "revlane_agent_runs",
                "gauge",
                "Agent runs by playbook and status.",
                [({"playbook": p, "status": s}, int(n)) for p, s, n in sorted(runs)],
            ),
            (
                "revlane_model_calls_total",
                "counter",
                "Model calls recorded in the audit log, by what made them.",
                [({"surface": surface}, n) for surface, n in sorted(calls.items())],
            ),
            (
                "revlane_model_tokens_total",
                "counter",
                "Tokens used by agent runs, by model and direction. Spend is tokens times price.",
                [
                    sample
                    for model, sent, received in sorted(
                        (name or "unknown", sent, received) for name, sent, received in tokens
                    )
                    for sample in (
                        ({"model": model, "direction": "input"}, int(sent)),
                        ({"model": model, "direction": "output"}, int(received)),
                    )
                ],
            ),
        ]
    )


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request) -> str:
    """Needs `Authorization: Bearer <METRICS_TOKEN>`. Switched off while no token is set."""
    token = get_settings().metrics_token
    if not token:
        raise HTTPException(404, "Metrics are switched off: METRICS_TOKEN is not set.")
    given = request.headers.get("authorization", "")
    if not hmac.compare_digest(given, f"Bearer {token}"):
        raise HTTPException(401, "A metrics token is needed.")
    return collect()
