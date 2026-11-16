"""Ingestion: raw provider records in, canonical rows out.

Safe to repeat. Every table is written with the provider's own id as the
key, so the same record delivered twice adds nothing. The raw payload is
kept, already stripped of card numbers and contact details by the mapper.
Each status report becomes an `attempt_event`; the status itself is derived.
"""

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from revlane.config import Settings, get_settings
from revlane.connectors import base as c
from revlane.connectors.registry import PROVIDERS, connector_for, read_records
from revlane.connectors.sanitize import shopper_token, tenant_token_key
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.ingest import statuses

BATCH = 2_000
MAX_REJECTIONS_SHOWN = 5
# Tables ingestion writes, in the order they are reported.
TABLES: dict[str, type[Any]] = {
    "order": m.Order,
    "payment_attempt": m.PaymentAttempt,
    "attempt_event": m.AttemptEvent,
    "refund": m.Refund,
    "settlement": m.Settlement,
    "settlement_line": m.SettlementLine,
    "bank_credit": m.BankCredit,
}


class TenantNotFound(LookupError):
    pass


class Digest(Protocol):
    def hexdigest(self) -> str: ...


@dataclass
class IngestReport:
    provider: str
    source: str
    read: int = 0
    rejected: int = 0
    rejections: list[str] = field(default_factory=list)
    # New rows per table. A repeat of the same file adds none.
    added: dict[str, int] = field(default_factory=dict)
    statuses_derived: int = 0
    # The provider's status word -> attempts stuck on it, for this provider.
    unmapped_statuses: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.rejected += 1
        if reason not in self.rejections and len(self.rejections) < MAX_REJECTIONS_SHOWN:
            self.rejections.append(reason)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)  # noqa: UP017


def ensure_account(
    session: Session, tenant_id: UUID, provider: str, account_ref: str
) -> m.ProviderAccount:
    account = session.scalar(
        select(m.ProviderAccount).where(
            m.ProviderAccount.tenant_id == tenant_id,
            m.ProviderAccount.provider == provider,
            m.ProviderAccount.account_ref == account_ref,
        )
    )
    if account is None:
        account = m.ProviderAccount(tenant_id=tenant_id, provider=provider, account_ref=account_ref)
        session.add(account)
        session.flush()
    return account


class _Writer:
    """Collects canonical records and writes them a batch at a time."""

    def __init__(
        self, session: Session, tenant_id: UUID, account: m.ProviderAccount, settings: Settings
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.account = account
        self.settings = settings
        self._token_key: bytes | None = None
        self.rows: dict[str, list[dict[str, Any]]] = {name: [] for name in TABLES}
        self.shoppers: list[c.ShopperRecord] = []
        self.touched: set[statuses.AttemptKey] = set()
        self.unsummed: set[tuple[str, str]] = set()  # settlements whose totals come from lines
        self.settlements: set[tuple[str, str]] = set()

    def token(self, contact: str | None) -> str | None:
        if contact is None:
            return None
        if self._token_key is None:
            self._token_key = tenant_token_key(self.settings.token_hash_root_key, self.tenant_id)
        return shopper_token(self._token_key, contact)

    def add(self, record: c.CanonicalRecord) -> None:
        tenant = {"tenant_id": self.tenant_id}
        if isinstance(record, c.AttemptRecord):
            self.rows["payment_attempt"].append(
                tenant
                | {
                    "provider": record.provider,
                    "attempt_id": record.attempt_id,
                    "provider_account_id": self.account.id,
                    "merchant_order_id": record.merchant_order_id,
                    "amount_minor": record.amount_minor,
                    "currency": record.currency,
                    "method": record.method,
                    "instrument_bank": record.instrument_bank,
                    "card_network": record.card_network,
                    "card_type": record.card_type,
                    "upi_app": record.upi_app,
                    "customer_token": self.token(record.shopper_contact),
                    "first_seen_at": record.first_seen_at,
                    "raw": record.raw,
                }
            )
        elif isinstance(record, c.EventRecord):
            self.touched.add((record.provider, record.attempt_id))
            self.rows["attempt_event"].append(
                tenant
                | {
                    "provider": record.provider,
                    "attempt_id": record.attempt_id,
                    "provider_status": record.provider_status,
                    "provider_error_code": record.provider_error_code,
                    "provider_stage": record.provider_stage,
                    "event_key": record.event_key,
                    "occurred_at": record.occurred_at,
                    "source": record.source,
                    "raw": record.raw,
                }
            )
        elif isinstance(record, c.RefundRecord):
            self.rows["refund"].append(
                tenant
                | {
                    "provider": record.provider,
                    "attempt_id": record.attempt_id,
                    "refund_id": record.refund_id,
                    "type": record.type,
                    "amount_minor": record.amount_minor,
                    "status": record.status,
                    "occurred_at": record.occurred_at,
                    "raw": record.raw,
                }
            )
        elif isinstance(record, c.SettlementRecord):
            key = (record.provider, record.settlement_id)
            if key in self.settlements:
                return  # a line-per-row file repeats its settlement on every row
            self.settlements.add(key)
            if record.gross_minor is None:
                self.unsummed.add((record.provider, record.settlement_id))
            self.rows["settlement"].append(
                tenant
                | {
                    "provider": record.provider,
                    "settlement_id": record.settlement_id,
                    "gross_minor": record.gross_minor or 0,
                    "fee_minor": record.fee_minor or 0,
                    "tax_minor": record.tax_minor or 0,
                    "net_minor": record.net_minor,
                    "utr": record.utr,
                    "settled_on": record.settled_on,
                    "raw": record.raw,
                }
            )
        elif isinstance(record, c.SettlementLineRecord):
            self.rows["settlement_line"].append(
                tenant
                | {
                    "provider": record.provider,
                    "settlement_id": record.settlement_id,
                    "attempt_id": record.attempt_id,
                    "line_ref": record.line_ref,
                    "line_type": record.line_type,
                    "gross_minor": record.gross_minor,
                    "fee_minor": record.fee_minor,
                    "tax_minor": record.tax_minor,
                    "net_minor": record.net_minor,
                    "fee_type": record.fee_type,
                    "raw": record.raw,
                }
            )
        elif isinstance(record, c.BankCreditRecord):
            self.rows["bank_credit"].append(
                tenant
                | {
                    "account_ref": self.account.account_ref,
                    "line_hash": record.line_hash,
                    "utr": record.utr,
                    "amount_minor": record.amount_minor,
                    "credited_on": record.credited_on,
                    "narration": record.narration,
                    "raw": record.raw,
                }
            )
        elif isinstance(record, c.OrderRecord):
            self.rows["order"].append(
                tenant
                | {
                    "merchant_order_id": record.merchant_order_id,
                    "amount_minor": record.amount_minor,
                    "currency": record.currency,
                    "status": record.status,
                    "fulfilment_status": record.fulfilment_status,
                    "channel": record.channel,
                    "placed_at": record.placed_at,
                    "raw": record.raw,
                }
            )
        else:
            self.shoppers.append(record)

    def flush(self) -> None:
        for name, rows in self.rows.items():
            if not rows:
                continue
            statement = insert(TABLES[name])
            if name == "order":
                # An order's state moves on (confirmed, then cancelled): the latest export wins.
                latest = {row["merchant_order_id"]: row for row in rows}
                self.session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["tenant_id", "merchant_order_id"],
                        set_={
                            "status": statement.excluded.status,
                            "fulfilment_status": statement.excluded.fulfilment_status,
                            "raw": statement.excluded.raw,
                            "updated_at": func.now(),
                        },
                    ),
                    list(latest.values()),
                )
            else:
                self.session.execute(statement.on_conflict_do_nothing(), rows)
            rows.clear()
        if self.shoppers:
            self._link_shoppers()

    def _link_shoppers(self) -> None:
        by_provider: dict[str, dict[str, str]] = {}
        for shopper in self.shoppers:
            token = self.token(shopper.shopper_contact)
            if token is not None:
                by_provider.setdefault(shopper.provider, {})[shopper.merchant_order_id] = token
        for provider, tokens in by_provider.items():
            self.session.execute(
                text(
                    "UPDATE payment_attempt AS a SET customer_token = v.token "
                    "FROM unnest(CAST(:orders AS text[]), CAST(:tokens AS text[])) "
                    "AS v(order_id, token) "
                    "WHERE a.tenant_id = :tenant AND a.provider = :provider "
                    "AND a.merchant_order_id = v.order_id AND a.customer_token IS NULL"
                ),
                {
                    "orders": list(tokens),
                    "tokens": list(tokens.values()),
                    "tenant": self.tenant_id,
                    "provider": provider,
                },
            )
        self.shoppers.clear()

    def sum_settlements(self) -> None:
        """Totals for settlements whose file gives only lines."""
        by_provider: dict[str, list[str]] = {}
        for provider, settlement_id in self.unsummed:
            by_provider.setdefault(provider, []).append(settlement_id)
        for provider, ids in by_provider.items():
            for chunk in statuses.chunks(sorted(ids)):
                self.session.execute(
                    text(
                        "UPDATE settlement AS s SET gross_minor = t.gross, fee_minor = t.fee, "
                        "tax_minor = t.tax, updated_at = now() "
                        "FROM (SELECT settlement_id, sum(gross_minor) AS gross, "
                        "sum(fee_minor) AS fee, sum(tax_minor) AS tax FROM settlement_line "
                        "WHERE tenant_id = :tenant AND provider = :provider "
                        "AND settlement_id = ANY(CAST(:ids AS text[])) "
                        "GROUP BY settlement_id) AS t "
                        "WHERE s.tenant_id = :tenant AND s.provider = :provider "
                        "AND s.settlement_id = t.settlement_id "
                        "AND (s.gross_minor, s.fee_minor, s.tax_minor) "
                        "IS DISTINCT FROM (t.gross, t.fee, t.tax)"
                    ),
                    {"tenant": self.tenant_id, "provider": provider, "ids": chunk},
                )


def table_counts(session: Session, tenant_id: UUID) -> dict[str, int]:
    return {
        name: session.scalar(
            select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
        )
        or 0
        for name, model in TABLES.items()
    }


def ingest(
    tenant_id: UUID,
    provider: str,
    source: str,
    records: Iterable[c.RawRecord | c.MappingError],
    *,
    account_ref: str = "default",
    now: datetime | None = None,
    actor: str = "system",
    payload_hash: Digest | None = None,
    settings: Settings | None = None,
) -> IngestReport:
    """Write a stream of raw records for one provider account, in one transaction.

    `source` names where they came from (a file name, `api`, `webhook`).
    `payload_hash` is a hashlib object the caller has fed with the bytes read;
    its digest goes in the audit log.
    """
    settings = settings or get_settings()
    now = now or utc_now()
    connector = connector_for(provider)
    report = IngestReport(provider=provider, source=source)

    with tenant_session(tenant_id) as session:
        if session.get(m.Tenant, tenant_id) is None:
            raise TenantNotFound(f"No tenant {tenant_id}")
        account = ensure_account(session, tenant_id, provider, account_ref)
        before = table_counts(session, tenant_id)
        writer = _Writer(session, tenant_id, account, settings)

        for record in records:
            report.read += 1
            if isinstance(record, c.MappingError):
                report.reject(str(record))
                continue
            try:
                mapped = connector.map(record)
            except c.MappingError as error:
                report.reject(str(error))
                continue
            for canonical in mapped:
                writer.add(canonical)
            if report.read % BATCH == 0:
                writer.flush()
        writer.flush()
        writer.sum_settlements()

        report.statuses_derived = statuses.rebuild(session, tenant_id, now, writer.touched)
        unknown = statuses.raise_alerts(session, tenant_id, now)
        report.unmapped_statuses = {
            word: count for (owner, word), count in sorted(unknown.items()) if owner == provider
        }
        after = table_counts(session, tenant_id)
        report.added = {name: after[name] - before[name] for name in TABLES}

        session.execute(
            update(m.ProviderAccount)
            .where(m.ProviderAccount.tenant_id == tenant_id, m.ProviderAccount.id == account.id)
            .values(last_sync_at=now, status="connected")
        )
        digest = payload_hash.hexdigest() if payload_hash is not None else "0" * 64
        session.add(
            m.AuditLog(
                tenant_id=tenant_id,
                actor=actor,
                kind="connector_call",
                payload_hash=digest,
                ref=f"{provider}:{source}",
                occurred_at=now,
            )
        )
    return report


def ingest_text(
    tenant_id: UUID,
    provider: str,
    filename: str,
    lines: Iterable[str],
    *,
    account_ref: str = "default",
    now: datetime | None = None,
    actor: str = "system",
    settings: Settings | None = None,
) -> IngestReport:
    """Ingest a JSON-lines or CSV file given as its lines."""
    digest = hashlib.sha256()

    def hashed() -> Iterator[str]:
        for line in lines:
            digest.update(line.encode("utf-8"))
            yield line

    records = read_records(connector_for(provider), filename, hashed())
    return ingest(
        tenant_id,
        provider,
        filename,
        records,
        account_ref=account_ref,
        now=now,
        actor=actor,
        payload_hash=digest,
        settings=settings,
    )


def file_order(path: Path) -> tuple[int, str]:
    # Payments before the records that refer to them.
    first = ("payment", "transaction")
    return (0 if any(word in path.name for word in first) else 1, path.name)


def ingest_directory(
    tenant_id: UUID,
    root: Path,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> list[IngestReport]:
    """Ingest a folder laid out as `<provider>/<file>`, as `make synth` writes it."""
    reports = []
    for provider in ("orders", *[p for p in PROVIDERS if p != "orders"]):
        folder = root / provider
        if not folder.is_dir():
            continue
        files = (p for p in folder.iterdir() if p.suffix in (".csv", ".jsonl"))
        for path in sorted(files, key=file_order):
            with path.open(encoding="utf-8", newline="") as handle:
                reports.append(
                    ingest_text(tenant_id, provider, path.name, handle, now=now, settings=settings)
                )
    return reports
