"""Milestone 3 acceptance: the synthetic merchant, ingested into Postgres.

- zero unknown statuses except the five planted in trap T7, which raise an alert;
- ingesting everything twice changes no row counts;
- trap T4 (a late reversal) changes the derived status without losing history;
- raw payloads are kept, without card numbers or contact details.

The status of every attempt is also checked against what the generator knows
really happened to it.
"""

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text

from revlane.bootstrap import ensure_tenant
from revlane.connectors.base import IST
from revlane.db import models as m
from revlane.db.base import Base
from revlane.db.session import tenant_session
from revlane.engine.status import RULES_VERSION
from revlane.ingest import statuses
from revlane.ingest.health import connector_health
from revlane.ingest.pipeline import IngestReport, ingest_directory, ingest_text
from revlane.synth import config as synth
from revlane.synth import render
from revlane.synth.generate import generate
from revlane.synth.world import World, build_world

AS_OF = date(2026, 11, 16)
SCALE = 0.02
# Noon the day after generation: every checkout on the last day has gone stale.
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=IST)

pytestmark = pytest.mark.usefixtures("database")


@dataclass
class Merchant:
    tenant_id: UUID
    path: Path
    key: dict[str, Any]
    world: World
    first: list[IngestReport]
    counts_after_first: dict[str, int]

    def trap(self, trap_id: str) -> dict[str, list[str]]:
        found: dict[str, list[str]] = next(t for t in self.key["traps"] if t["id"] == trap_id)[
            "record_ids"
        ]
        return found


def new_tenant(name: str) -> UUID:
    tenant_id = uuid4()
    ensure_tenant(tenant_id, name)
    return tenant_id


def all_counts(tenant_id: UUID) -> dict[str, int]:
    """Rows in every table the tenant has."""
    with tenant_session(tenant_id) as session:
        return {
            table.name: session.scalar(select(func.count()).select_from(table)) or 0
            for table in Base.metadata.sorted_tables
        }


@pytest.fixture(scope="module")
def merchant(database: None, tmp_path_factory: pytest.TempPathFactory) -> Merchant:
    result = generate(seed=42, as_of=AS_OF, scale=SCALE, out_root=tmp_path_factory.mktemp("synth"))
    tenant_id = new_tenant("ingest-synth")
    first = ingest_directory(tenant_id, result.path, now=NOW)
    return Merchant(
        tenant_id=tenant_id,
        path=result.path,
        key=result.answer_key,
        world=build_world(42, synth.calendar_for(AS_OF), SCALE),
        first=first,
        counts_after_first=all_counts(tenant_id),
    )


def derived(tenant_id: UUID) -> dict[tuple[str, str], m.AttemptStatus]:
    with tenant_session(tenant_id) as session:
        return {
            (row.provider, row.attempt_id): row for row in session.scalars(select(m.AttemptStatus))
        }


# --- Everything arrives ----------------------------------------------------------


def test_every_file_is_read_and_nothing_is_rejected(merchant: Merchant) -> None:
    sources = {f"{report.provider}/{report.source}" for report in merchant.first}
    on_disk = {
        f"{path.parent.name}/{path.name}"
        for path in merchant.path.glob("*/*")
        if path.parent.name != "contracts"
    }

    assert sources == on_disk
    assert [report for report in merchant.first if report.rejected] == []
    assert all(report.read > 0 for report in merchant.first)


def test_row_counts_match_the_generated_world(merchant: Merchant) -> None:
    world, counts = merchant.world, merchant.counts_after_first
    gateway = [a for a in world.attempts if a.provider in ("razorpay", "payu", "cashfree")]

    assert counts["order"] == len(world.orders)
    assert counts["payment_attempt"] == len(gateway) == len(world.attempts)
    assert counts["attempt_status"] == len(gateway)
    assert counts["refund"] == len(world.refunds)
    assert counts["settlement"] == len(world.settlements)
    assert counts["settlement_line"] == sum(len(s.lines) for s in world.settlements)
    assert counts["bank_credit"] == len(world.credits)
    assert counts["provider_account"] == 6


def test_money_arrives_exact_to_the_paisa(merchant: Merchant) -> None:
    world = merchant.world
    with tenant_session(merchant.tenant_id) as session:
        attempts = session.scalar(select(func.sum(m.PaymentAttempt.amount_minor)))
        credits = session.scalar(select(func.sum(m.BankCredit.amount_minor)))
        settlements = {
            (row.provider, row.settlement_id): row for row in session.scalars(select(m.Settlement))
        }
        lines = dict(
            session.execute(
                select(m.SettlementLine.provider, func.sum(m.SettlementLine.net_minor)).group_by(
                    m.SettlementLine.provider
                )
            ).all()
        )

    assert attempts == sum(a.amount for a in world.attempts)
    assert credits == sum(credit.amount for credit in world.credits)
    for expected in world.settlements:
        row = settlements[(expected.provider, expected.settlement_id)]
        assert (row.gross_minor, row.fee_minor, row.tax_minor, row.net_minor, row.utr) == (
            expected.gross,
            expected.fee,
            expected.tax,
            expected.paid,
            expected.utr,
        ), expected.settlement_id
        assert row.settled_on == world.cal.days[expected.day]
    for provider in ("razorpay", "payu", "cashfree", "marketplace"):
        assert lines[provider] == sum(
            line.net for s in world.settlements if s.provider == provider for line in s.lines
        )


# --- Status ----------------------------------------------------------------------

TRUTH = {
    "succeeded": "succeeded",
    "failed": "failed",
    "abandoned": "abandoned",
    "auto_refunded": "auto_refunded",
    "pending_then_failed": "failed",
    "unknown": "unknown",
}


def expected_reason(provider: str, reason: str | None) -> str:
    known: dict[str, Any] = {
        "razorpay": render.RAZORPAY_ERRORS,
        "payu": render.PAYU_ERRORS,
    }.get(provider, {})
    if provider == "cashfree":
        return reason or "unknown"
    return reason if reason in known else "unknown"


def test_every_attempt_has_the_status_the_generator_knows_it_had(merchant: Merchant) -> None:
    rows = derived(merchant.tenant_id)
    wrong: list[tuple[str, str | None, str | None]] = []
    for attempt in merchant.world.attempts:
        row = rows[(attempt.provider, attempt.attempt_id)]
        expected = TRUTH[attempt.truth]
        if row.status != expected:
            wrong.append((attempt.attempt_id, attempt.truth, row.status))
        elif expected == "failed" and row.failure_reason != expected_reason(
            attempt.provider, attempt.reason
        ):
            wrong.append((attempt.attempt_id, attempt.reason, row.failure_reason))
        elif expected != "failed" and row.failure_reason is not None:
            wrong.append((attempt.attempt_id, "no reason", row.failure_reason))

    assert wrong == []
    assert {row.rule_version for row in rows.values()} == {RULES_VERSION}
    assert Counter(row.status for row in rows.values()).keys() >= {
        "succeeded",
        "failed",
        "abandoned",
        "auto_refunded",
        "unknown",
    }


def test_the_only_unknown_statuses_are_the_five_planted_in_trap_t7(merchant: Merchant) -> None:
    planted = set(merchant.trap("T7")["attempts"])
    unknown = {
        attempt_id
        for (_, attempt_id), row in derived(merchant.tenant_id).items()
        if row.status == "unknown"
    }

    assert len(planted) == 5
    assert unknown == planted


def test_trap_t7_raises_an_alert_for_each_unmapped_status(merchant: Merchant) -> None:
    with tenant_session(merchant.tenant_id) as session:
        alerts = {
            (alert.provider, alert.subject): alert for alert in session.scalars(select(m.Alert))
        }

    assert {key: alert.record_count for key, alert in alerts.items()} == dict(
        Counter(synth.T7_STATUSES)
    )
    assert sum(alert.record_count for alert in alerts.values()) == 5
    assert {alert.state for alert in alerts.values()} == {"open"}
    assert {alert.kind for alert in alerts.values()} == {"unmapped_status"}


def test_connector_health_reports_counts_last_sync_and_unmapped_statuses(
    merchant: Merchant,
) -> None:
    health = connector_health(merchant.tenant_id)
    by_provider = {connector["provider"]: connector for connector in health["connectors"]}
    world = merchant.world

    assert set(by_provider) == {"razorpay", "payu", "cashfree", "marketplace", "bank", "orders"}
    for provider in ("razorpay", "payu", "cashfree"):
        connector = by_provider[provider]
        assert connector["records"]["attempts"] == sum(
            1 for a in world.attempts if a.provider == provider
        )
        assert connector["records"]["events"] >= connector["records"]["attempts"]
        assert connector["last_sync_at"] == NOW.isoformat()
        assert connector["status"] == "connected"
        assert {
            item["provider_status"]: item["attempts"] for item in connector["unmapped_statuses"]
        } == {word: n for (p, word), n in Counter(synth.T7_STATUSES).items() if p == provider}
    assert by_provider["bank"]["records"] == {"bank_credits": len(world.credits)}
    assert by_provider["orders"]["records"] == {"orders": len(world.orders)}
    assert by_provider["marketplace"]["unmapped_statuses"] == []
    assert len(health["alerts"]) == len(set(synth.T7_STATUSES))


def test_trap_t3_keeps_both_attempts_of_the_order(merchant: Merchant) -> None:
    trap = merchant.trap("T3")
    rows = derived(merchant.tenant_id)
    with tenant_session(merchant.tenant_id) as session:
        by_order = Counter(
            session.scalars(
                select(m.PaymentAttempt.merchant_order_id).where(
                    m.PaymentAttempt.merchant_order_id.in_(trap["orders"])
                )
            )
        )
        webhooks = session.scalar(
            select(func.count())
            .select_from(m.AttemptEvent)
            .where(
                m.AttemptEvent.attempt_id.in_(trap["attempts"]), m.AttemptEvent.source == "webhook"
            )
        )

    assert set(by_order.values()) == {2}
    assert Counter(rows[("razorpay", attempt)].status for attempt in trap["attempts"]) == {
        "failed": len(trap["orders"]),
        "succeeded": len(trap["orders"]),
    }
    # Each was reported by the payments file and again by a webhook.
    assert webhooks == len(trap["attempts"])


# --- Idempotency -----------------------------------------------------------------


def test_ingesting_everything_twice_changes_no_row_counts(merchant: Merchant) -> None:
    second = ingest_directory(merchant.tenant_id, merchant.path, now=NOW)
    after = all_counts(merchant.tenant_id)

    # The audit log is the one table meant to grow: it records that the second run happened.
    files = len(merchant.first)
    assert after.pop("audit_log") == merchant.counts_after_first["audit_log"] + files
    before = dict(merchant.counts_after_first)
    before.pop("audit_log")
    assert after == before
    assert all(set(report.added.values()) == {0} for report in second)
    assert [report.read for report in second] == [report.read for report in merchant.first]


def test_trap_t2_a_redelivered_settlement_is_ingested_once(merchant: Merchant) -> None:
    (settlement_id,) = merchant.trap("T2")["settlements"]
    redelivery = next(r for r in merchant.first if r.source == "settlements_redelivery.csv")
    expected = next(s for s in merchant.world.settlements if s.settlement_id == settlement_id)
    with tenant_session(merchant.tenant_id) as session:
        settlements = session.scalar(
            select(func.count())
            .select_from(m.Settlement)
            .where(m.Settlement.settlement_id == settlement_id)
        )
        lines = session.scalar(
            select(func.count())
            .select_from(m.SettlementLine)
            .where(m.SettlementLine.settlement_id == settlement_id)
        )

    assert redelivery.read == len(expected.lines)
    assert set(redelivery.added.values()) == {0}
    assert (settlements, lines) == (1, len(expected.lines))


# --- Trap T4: a late reversal ------------------------------------------------------


def test_trap_t4_changes_the_status_and_keeps_the_history(merchant: Merchant) -> None:
    planted = set(merchant.trap("T4")["attempts"])
    assert len(planted) == synth.T4_COUNT
    first_reports, seen = [], set()
    with (merchant.path / "payu" / "transactions.jsonl").open(encoding="utf-8") as handle:
        everything = handle.readlines()
    for line in everything:
        attempt_id = json.loads(line)["mihpayid"]
        if attempt_id in planted and attempt_id not in seen:
            seen.add(attempt_id)
            first_reports.append(line)
    tenant_id = new_tenant("ingest-t4")

    def state() -> tuple[set[str], list[tuple[str, str, datetime]]]:
        with tenant_session(tenant_id) as session:
            status = set(session.scalars(select(m.AttemptStatus.status)))
            events = session.execute(
                select(
                    m.AttemptEvent.attempt_id,
                    m.AttemptEvent.provider_status,
                    m.AttemptEvent.occurred_at,
                ).order_by(m.AttemptEvent.attempt_id, m.AttemptEvent.occurred_at)
            ).all()
        return status, [(row[0], row[1], row[2]) for row in events]

    # Day one: PayU says pending, and it stays pending however long we wait.
    ingest_text(tenant_id, "payu", "transactions.jsonl", first_reports, now=NOW)
    status, early = state()
    assert status == {"pending"}
    assert [word for _, word, _ in early] == ["pending"] * len(planted)

    # Two days later the same transactions are reported failed.
    late = [line for line in everything if json.loads(line)["mihpayid"] in planted]
    report = ingest_text(tenant_id, "payu", "transactions.jsonl", late, now=NOW)
    status, history = state()

    assert status == {"failed"}
    assert report.added["attempt_event"] == len(planted)
    assert report.added["payment_attempt"] == 0
    assert len(history) == 2 * len(planted)
    # Nothing that was recorded on day one has gone or changed.
    assert [event for event in history if event[1] == "pending"] == early
    for attempt_id in planted:
        (pending,) = [e for e in history if e[0] == attempt_id and e[1] == "pending"]
        (failed,) = [e for e in history if e[0] == attempt_id and e[1] == "failed"]
        assert failed[2] - pending[2] >= timedelta(days=2)
    with tenant_session(tenant_id) as session:
        reasons = set(session.scalars(select(m.AttemptStatus.failure_reason)))
        payloads = session.scalars(
            select(m.AttemptEvent.raw["unmappedstatus"].astext).where(
                m.AttemptEvent.provider_status == "pending"
            )
        ).all()
    assert reasons == {"bank_declined"}
    assert set(payloads) == {"pending"}  # the first report's own payload is still there


# --- The clock, and rebuilding ---------------------------------------------------

CHECKOUT = (
    '{{"id":"pay_clock","entity":"payment","amount":50000,"currency":"INR","status":"created",'
    '"method":"upi","captured":false,"notes":{{"booking_id":"BK-C"}},"created_at":{at}}}\n'
)


def test_a_checkout_goes_stale_with_no_new_event() -> None:
    tenant_id = new_tenant("ingest-clock")
    created = datetime(2026, 11, 16, 9, 0, tzinfo=IST)
    line = CHECKOUT.format(at=int(created.timestamp()))
    ingest_text(tenant_id, "razorpay", "payments.jsonl", [line], now=created + timedelta(minutes=5))

    def status() -> tuple[str, datetime | None]:
        with tenant_session(tenant_id) as session:
            row = session.scalars(select(m.AttemptStatus)).one()
            return row.status, row.changes_at

    assert status() == ("pending", created + timedelta(minutes=30))

    with tenant_session(tenant_id) as session:
        assert statuses.refresh_due(session, tenant_id, created + timedelta(minutes=29)) == 0
    assert status()[0] == "pending"

    with tenant_session(tenant_id) as session:
        assert statuses.refresh_due(session, tenant_id, created + timedelta(minutes=31)) == 1
        events = session.scalar(select(func.count()).select_from(m.AttemptEvent))
    assert status() == ("abandoned", None)
    assert events == 1


def test_status_can_be_rebuilt_from_events_alone(merchant: Merchant) -> None:
    before = {key: row.status for key, row in derived(merchant.tenant_id).items()}
    tenant_id = merchant.tenant_id

    with tenant_session(tenant_id) as session:
        session.execute(text("DELETE FROM attempt_status"))
        assert session.scalar(select(func.count()).select_from(m.AttemptStatus)) == 0
        rebuilt = statuses.rebuild(session, tenant_id, NOW)

    assert rebuilt == len(before)
    assert {key: row.status for key, row in derived(tenant_id).items()} == before


def test_a_tenants_own_stale_period_is_used() -> None:
    tenant_id = new_tenant("ingest-policy")
    with tenant_session(tenant_id) as session:
        session.execute(
            text("UPDATE tenant SET policy = CAST(:policy AS jsonb)"),
            {"policy": '{"status": {"stale_after_minutes": 120}}'},
        )
    created = datetime(2026, 11, 16, 9, 0, tzinfo=IST)
    line = CHECKOUT.format(at=int(created.timestamp()))
    ingest_text(tenant_id, "razorpay", "payments.jsonl", [line], now=created + timedelta(hours=1))

    with tenant_session(tenant_id) as session:
        assert session.scalars(select(m.AttemptStatus.status)).one() == "pending"


def test_an_alert_is_resolved_once_the_status_is_known() -> None:
    tenant_id = new_tenant("ingest-alert")
    created = datetime(2026, 11, 16, 9, 0, tzinfo=IST)
    odd = CHECKOUT.format(at=int(created.timestamp())).replace('"created"', '"under_review"')
    report = ingest_text(tenant_id, "razorpay", "payments.jsonl", [odd], now=NOW)
    assert report.unmapped_statuses == {"under_review": 1}

    captured = odd.replace('"under_review"', '"captured"')
    report = ingest_text(tenant_id, "razorpay", "payments.jsonl", [captured], now=NOW)

    with tenant_session(tenant_id) as session:
        alert = session.scalars(select(m.Alert)).one()
        events = session.scalar(select(func.count()).select_from(m.AttemptEvent))
    assert report.unmapped_statuses == {}
    assert (alert.state, alert.record_count) == ("resolved", 0)
    assert events == 2


# --- Rule 7 ----------------------------------------------------------------------

RAW_TABLES = (
    "payment_attempt",
    "attempt_event",
    "refund",
    "settlement",
    "settlement_line",
    "bank_credit",
    '"order"',
)
# Emails, phones, shopper names, UPI ids and masked card numbers, as the generator writes them.
SENSITIVE = (
    r"example\.com|\+9198\d{8}|shopper\d+@|Shopper\d|SHOPPER \d|X{6,}\d{4}"
    r'|"(phone|contact|email|firstname|last4|cardnum|card_number|upi_id|upi_va|vpa)"'
)


@pytest.mark.parametrize("table", RAW_TABLES)
def test_no_stored_payload_keeps_a_card_number_or_contact(merchant: Merchant, table: str) -> None:
    with tenant_session(merchant.tenant_id) as session:
        kept = session.scalar(text(f"SELECT count(*) FROM {table} WHERE raw::text <> '{{}}'"))
        leaked = session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE raw::text ~ :pattern"), {"pattern": SENSITIVE}
        )

    assert kept == merchant.counts_after_first[table.strip('"')]  # raw payloads are kept
    assert leaked == 0


def test_the_generated_files_do_hold_what_ingestion_drops(merchant: Merchant) -> None:
    text_ = (merchant.path / "payu" / "transactions.jsonl").read_text(encoding="utf-8")

    assert "@example.com" in text_
    assert "XXXXXX" in text_


def test_shoppers_are_keyed_hash_tokens_only(merchant: Merchant) -> None:
    world = merchant.world
    with tenant_session(merchant.tenant_id) as session:
        tokens = dict(
            session.execute(
                select(m.PaymentAttempt.attempt_id, m.PaymentAttempt.customer_token)
            ).all()
        )

    assert all(token and len(token) == 64 for token in tokens.values())
    by_shopper: dict[int, set[str | None]] = {}
    for attempt in world.attempts:
        by_shopper.setdefault(attempt.shopper, set()).add(tokens[attempt.attempt_id])
    # One shopper, one token, whichever gateway they paid through; no two shoppers share one.
    assert all(len(found) == 1 for found in by_shopper.values())
    assert len({next(iter(found)) for found in by_shopper.values()}) == len(by_shopper)


def test_stored_decimals_are_exact(merchant: Merchant) -> None:
    with tenant_session(merchant.tenant_id) as session:
        mismatched = session.scalar(
            text(
                "SELECT count(*) FROM payment_attempt WHERE provider = 'cashfree' "
                "AND (raw->>'payment_amount')::numeric * 100 <> amount_minor"
            )
        )

    assert mismatched == 0


def test_each_ingestion_is_in_the_audit_log(merchant: Merchant) -> None:
    with tenant_session(merchant.tenant_id) as session:
        entries = session.scalars(
            select(m.AuditLog).where(m.AuditLog.ref == "payu:transactions.jsonl")
        ).all()

    assert entries
    assert {entry.kind for entry in entries} == {"connector_call"}
    assert all(
        len(entry.payload_hash) == 64 and set(entry.payload_hash) != {"0"} for entry in entries
    )
