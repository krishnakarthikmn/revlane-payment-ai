"""Data lifecycle: export the audit log, apply retention, erase a shopper,
offboard a tenant. See docs/technical-design.md, section 10.

`make retention` and `make offboard ARGS=<tenant id>` run the two jobs.
"""

import argparse
import csv
import io
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from revlane.config import Settings, get_settings
from revlane.connectors.base import IST
from revlane.connectors.sanitize import normalise_contact, shopper_token, tenant_token_key
from revlane.db import models as m
from revlane.db.session import admin_session, tenant_session
from revlane.harness import audit
from revlane.vault import Vault, get_vault

EXPORT_COLUMNS = ("occurred_at", "kind", "actor", "ref", "payload_hash", "id")
EXPORT_BATCH = 2_000
# A retention period shorter than this is taken for a mistake.
MIN_RETENTION_DAYS = 30
OFFBOARDED = "The tenant was offboarded."


# --- Audit-log export ---------------------------------------------------------------


def export_audit_log(session: Session, tenant_id: UUID) -> Iterator[str]:
    """The whole audit log as CSV, oldest first. It holds hashes, never payloads."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush() -> str:
        text = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate()
        return text

    writer.writerow(EXPORT_COLUMNS)
    yield flush()
    log = m.AuditLog
    rows = session.execute(
        select(log.occurred_at, log.kind, log.actor, log.ref, log.payload_hash, log.id)
        .where(log.tenant_id == tenant_id)
        .order_by(log.occurred_at, log.id)
        .execution_options(yield_per=EXPORT_BATCH)
    )
    for occurred_at, *rest in rows:
        writer.writerow([occurred_at.isoformat(), *("" if v is None else v for v in rest)])
        yield flush()


# --- Retention --------------------------------------------------------------------------


def retention_days(policy: dict[str, Any]) -> int | None:
    """The tenant's retention period, from `tenant.policy["retention"]["days"]`."""
    days = (policy.get("retention") or {}).get("days")
    if not isinstance(days, int) or isinstance(days, bool):
        return None
    if days < MIN_RETENTION_DAYS:
        raise ValueError(f"A retention period must be at least {MIN_RETENTION_DAYS} days.")
    return days


def apply_retention(tenant_id: UUID, *, today: date | None = None) -> dict[str, int]:
    """Delete a tenant's provider records older than its retention period.

    Findings, proposals, approvals, claims and the audit log are kept: they are
    the record of what was found and decided. Runs as the schema owner, because
    an application session may not delete from the append-only event history.
    """
    today = today or datetime.now(tz=IST).date()
    with admin_session() as session:
        policy = session.scalar(select(m.Tenant.policy).where(m.Tenant.id == tenant_id))
        if policy is None:
            raise LookupError(f"No tenant {tenant_id}.")
        days = retention_days(policy)
        if days is None:
            return {}
        cutoff = today - timedelta(days=days)
        moment = datetime.combine(cutoff, time.min, tzinfo=IST)
        old_settlements = select(m.Settlement.provider, m.Settlement.settlement_id).where(
            m.Settlement.tenant_id == tenant_id, m.Settlement.settled_on < cutoff
        )
        removed: dict[str, int] = {}

        def remove(name: str, model: type[Any], *conditions: Any) -> None:  # noqa: ANN401
            result = session.execute(delete(model).where(model.tenant_id == tenant_id, *conditions))
            removed[name] = result.rowcount or 0  # type: ignore[attr-defined]

        line = m.SettlementLine
        lines = 0
        for provider, settlement_id in session.execute(old_settlements).all():
            lines += (
                session.execute(
                    delete(line).where(
                        line.tenant_id == tenant_id,
                        line.provider == provider,
                        line.settlement_id == settlement_id,
                    )
                ).rowcount  # type: ignore[attr-defined]
                or 0
            )
        removed["settlement_line"] = lines
        remove("settlement", m.Settlement, m.Settlement.settled_on < cutoff)
        remove("attempt_event", m.AttemptEvent, m.AttemptEvent.occurred_at < moment)
        # The derived status goes with its attempt.
        remove("payment_attempt", m.PaymentAttempt, m.PaymentAttempt.first_seen_at < moment)
        remove("refund", m.Refund, m.Refund.occurred_at < moment)
        remove("bank_credit", m.BankCredit, m.BankCredit.credited_on < cutoff)
        remove("order", m.Order, m.Order.placed_at < moment)
        remove("match_link", m.MatchLink, m.MatchLink.created_at < moment)
        remove("agent_run", m.AgentRun, m.AgentRun.finished_at < moment)
        audit.record(
            session,
            tenant_id,
            actor="retention",
            kind="action",
            payload={"cutoff": cutoff.isoformat(), "removed": removed},
            ref=f"retention:{cutoff.isoformat()}",
        )
        return removed


def apply_retention_everywhere(today: date | None = None) -> dict[UUID, dict[str, int]]:
    """Every tenant that has a retention period."""
    with admin_session() as session:
        tenants = list(session.scalars(select(m.Tenant.id).order_by(m.Tenant.created_at)))
    done = {}
    for tenant_id in tenants:
        removed = apply_retention(tenant_id, today=today)
        if removed:
            done[tenant_id] = removed
    return done


# --- Erasure -------------------------------------------------------------------------------


def erase_shopper(
    session: Session,
    tenant_id: UUID,
    *,
    customer_token: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Remove a shopper's token wherever it links records together.

    Revlane keeps no contact details, only a keyed hash. Given the phone or
    email on an erasure request, the hash is worked out and the contact is
    thrown away again; nothing about it is stored or logged.
    """
    token = customer_token
    if token is None:
        contact = normalise_contact(phone=phone, email=email)
        if contact is None:
            raise ValueError("Give the shopper's token, phone or email.")
        settings = settings or get_settings()
        token = shopper_token(tenant_token_key(settings.token_hash_root_key, tenant_id), contact)
    erased = {}
    for name, model in (("payment_attempt", m.PaymentAttempt), ("mandate", m.Mandate)):
        result = session.execute(
            update(model)
            .where(model.tenant_id == tenant_id, model.customer_token == token)
            .values(customer_token=None)
        )
        erased[name] = result.rowcount or 0  # type: ignore[attr-defined]
    # The log says an erasure happened and how much it touched, not whose.
    audit.record(session, tenant_id, actor="erasure", kind="action", payload=erased, ref="erasure")
    return erased


# --- Offboarding -----------------------------------------------------------------------------


def offboard(tenant_id: UUID, vault: Vault | None = None) -> dict[str, int]:
    """Revoke and delete a tenant's credentials at once, and stop its agents.

    The tenant's records stay until they are deleted on instruction or by
    retention; what goes immediately is everything that could reach a provider.
    """
    vault = vault or get_vault()
    with tenant_session(tenant_id) as session:
        tenant = session.scalar(select(m.Tenant).where(m.Tenant.id == tenant_id).with_for_update())
        if tenant is None:
            raise LookupError(f"No tenant {tenant_id}.")
        secrets = vault.delete_all(tenant_id)
        accounts = session.execute(
            update(m.ProviderAccount)
            .where(m.ProviderAccount.tenant_id == tenant_id)
            .values(credential_ref=None, status="revoked")
        )
        runs = session.execute(
            update(m.AgentRun)
            .where(m.AgentRun.tenant_id == tenant_id, m.AgentRun.status.in_(m.ACTIVE_RUN_STATUSES))
            .values(status="skipped", error=OFFBOARDED, locked_by=None, locked_at=None)
        )
        policy = dict(tenant.policy or {})
        policy["agents"] = dict(policy.get("agents") or {}) | {"enabled": False}
        tenant.policy = policy
        done = {
            "secrets_deleted": secrets,
            "accounts_revoked": accounts.rowcount or 0,  # type: ignore[attr-defined]
            "runs_stopped": runs.rowcount or 0,  # type: ignore[attr-defined]
        }
        audit.record(
            session, tenant_id, actor="offboarding", kind="action", payload=done, ref="offboarding"
        )
        return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m revlane.lifecycle", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("retention", help="delete records older than each tenant's period")
    commands.add_parser("offboard", help="delete a tenant's credentials").add_argument(
        "tenant_id", type=UUID
    )
    args = parser.parse_args(argv)
    if args.command == "retention":
        done = apply_retention_everywhere()
        for tenant_id, removed in done.items():
            total = sum(removed.values())
            print(f"{tenant_id}: removed {total:,} records past retention")
        if not done:
            print("No tenant has a retention period set, or nothing was old enough.")
        return 0
    result = offboard(args.tenant_id)
    print(
        f"Offboarded {args.tenant_id}: {result['secrets_deleted']} credentials deleted, "
        f"{result['accounts_revoked']} accounts revoked, {result['runs_stopped']} runs stopped."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
