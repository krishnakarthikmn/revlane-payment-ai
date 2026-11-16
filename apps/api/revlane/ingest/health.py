"""Connector health: last sync, record counts and unmapped statuses."""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.ingest import statuses

# Record counts shown per provider account, and the table each comes from.
BY_PROVIDER: dict[str, type[Any]] = {
    "attempts": m.PaymentAttempt,
    "events": m.AttemptEvent,
    "refunds": m.Refund,
    "settlements": m.Settlement,
    "settlement_lines": m.SettlementLine,
}


def connector_health(tenant_id: UUID) -> dict[str, Any]:
    with tenant_session(tenant_id) as session:
        counts: dict[str, dict[str, int]] = {}
        for label, model in BY_PROVIDER.items():
            rows = session.execute(
                select(model.provider, func.count())
                .where(model.tenant_id == tenant_id)
                .group_by(model.provider)
            )
            for provider, count in rows:
                counts.setdefault(provider, {})[label] = count
        credits = dict(
            session.execute(
                select(m.BankCredit.account_ref, func.count())
                .where(m.BankCredit.tenant_id == tenant_id)
                .group_by(m.BankCredit.account_ref)
            ).all()
        )
        orders = session.scalar(
            select(func.count()).select_from(m.Order).where(m.Order.tenant_id == tenant_id)
        )
        unknown = statuses.unknown_statuses(session, tenant_id)
        accounts = session.scalars(
            select(m.ProviderAccount)
            .where(m.ProviderAccount.tenant_id == tenant_id)
            .order_by(m.ProviderAccount.provider, m.ProviderAccount.account_ref)
        ).all()
        alerts = session.scalars(
            select(m.Alert)
            .where(m.Alert.tenant_id == tenant_id, m.Alert.state == "open")
            .order_by(m.Alert.provider, m.Alert.subject)
        ).all()

        connectors = []
        for account in accounts:
            if account.provider == "bank":
                records = {"bank_credits": credits.get(account.account_ref, 0)}
            elif account.provider == "orders":
                records = {"orders": orders or 0}
            else:
                records = {label: 0 for label in BY_PROVIDER} | counts.get(account.provider, {})
            connectors.append(
                {
                    "provider": account.provider,
                    "account_ref": account.account_ref,
                    "status": account.status,
                    "has_credentials": account.credential_ref is not None,
                    "last_sync_at": account.last_sync_at.isoformat()
                    if account.last_sync_at
                    else None,
                    "records": records,
                    "unmapped_statuses": [
                        {"provider_status": word, "attempts": count}
                        for (provider, word), count in sorted(unknown.items())
                        if provider == account.provider
                    ],
                }
            )
        return {
            "connectors": connectors,
            "alerts": [
                {
                    "kind": alert.kind,
                    "provider": alert.provider,
                    "subject": alert.subject,
                    "record_count": alert.record_count,
                    "first_seen_at": alert.first_seen_at.isoformat(),
                    "last_seen_at": alert.last_seen_at.isoformat(),
                }
                for alert in alerts
            ],
        }
