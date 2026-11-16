"""Milestone 4 acceptance: both engines against the synthetic merchant's answer key.

- R1-R3 and F1-F3 are found with the exact record ids and amounts;
- reconciliation totals ₹3,10,000 and fees ₹2,60,000 over 1,912 lines;
- traps T1, T2, T3, T5 and T6 produce no finding.
"""

import ast
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from revlane.audit import store
from revlane.audit.contracts import load_contracts_file
from revlane.audit.run import AuditReport, run_audit
from revlane.bootstrap import ensure_tenant
from revlane.connectors.base import IST
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.engine import fees, matching
from revlane.engine import findings as fd
from revlane.ingest.pipeline import ingest_directory
from revlane.synth.generate import generate

AS_OF = date(2026, 11, 16)
SCALE = 0.02
RECONCILIATION = ("paid_auto_refunded", "settled_short", "captured_not_settled")
FEES = ("fee_above_contract", "tax_on_refunded_fee", "fee_not_in_contract")

pytestmark = pytest.mark.usefixtures("database")


@dataclass
class Audited:
    tenant_id: UUID
    path: Path
    key: dict[str, Any]
    report: AuditReport

    def leak(self, leak_id: str) -> dict[str, Any]:
        found: dict[str, Any] = next(leak for leak in self.key["leaks"] if leak["id"] == leak_id)
        return found

    def trap(self, trap_id: str) -> dict[str, list[str]]:
        found: dict[str, list[str]] = next(t for t in self.key["traps"] if t["id"] == trap_id)[
            "record_ids"
        ]
        return found

    def findings(self, *types: str) -> list[m.Finding]:
        with tenant_session(self.tenant_id) as session:
            query = select(m.Finding).order_by(m.Finding.type, m.Finding.period_start)
            if types:
                query = query.where(m.Finding.type.in_(types))
            return list(session.scalars(query))

    def links(self, left_type: str, right_type: str) -> list[m.MatchLink]:
        with tenant_session(self.tenant_id) as session:
            return list(
                session.scalars(
                    select(m.MatchLink).where(
                        m.MatchLink.left_type == left_type, m.MatchLink.right_type == right_type
                    )
                )
            )

    def count(self, model: type[Any]) -> int:
        with tenant_session(self.tenant_id) as session:
            return session.scalar(select(func.count()).select_from(model)) or 0


@pytest.fixture(scope="module")
def audited(database: None, tmp_path_factory: pytest.TempPathFactory) -> Audited:
    result = generate(seed=42, as_of=AS_OF, scale=SCALE, out_root=tmp_path_factory.mktemp("synth"))
    tenant_id = uuid4()
    ensure_tenant(tenant_id, "audit-synth")
    ingest_directory(tenant_id, result.path, now=datetime.combine(AS_OF, time.max, tzinfo=IST))
    load_contracts_file(tenant_id, result.path / "contracts" / "fee_contracts.json")
    return Audited(tenant_id, result.path, result.answer_key, run_audit(tenant_id, as_of=AS_OF))


def evidence_ids(findings: list[m.Finding], kind: str) -> list[str]:
    return [record for finding in findings for record in finding.evidence.get(kind, [])]


def every_evidence_id(findings: list[m.Finding]) -> set[str]:
    return {
        record
        for finding in findings
        for kind, records in finding.evidence.items()
        if kind != "items"
        for record in records
    }


# --- The planted leaks ----------------------------------------------------------------


@pytest.mark.parametrize("leak_id", ["R1", "R2", "R3", "F1", "F2", "F3"])
def test_each_planted_leak_is_found_with_its_exact_records_and_amounts(
    audited: Audited, leak_id: str
) -> None:
    leak = audited.leak(leak_id)
    found = audited.findings(leak["type"])

    assert found, f"{leak_id} was not found"
    assert {finding.provider for finding in found} == {leak["provider"]}
    assert {finding.state for finding in found} == {leak["expected_state"]}
    assert sum(finding.amount_minor for finding in found) == leak["amount_minor"]
    for kind, record_ids in leak["record_ids"].items():
        assert sorted(evidence_ids(found, kind)) == sorted(record_ids), kind
    items = {
        item["id"]: item["amount_minor"] for finding in found for item in finding.evidence["items"]
    }
    assert items == {item["id"]: item["amount_minor"] for item in leak["items"]}


def test_nothing_else_is_reported(audited: Audited) -> None:
    found = Counter((finding.type, finding.provider) for finding in audited.findings())
    planted = Counter((leak["type"], leak["provider"]) for leak in audited.key["leaks"])

    # One finding per leak: each sits inside one calendar month.
    assert found == planted


def test_the_totals_are_the_ones_on_the_prototypes_screens(audited: Audited) -> None:
    reconciliation = sum(f.amount_minor for f in audited.findings(*RECONCILIATION))
    fee_findings = audited.findings(*FEES)

    assert reconciliation == audited.key["totals_minor"]["reconciliation"] == 310_000 * 100
    assert sum(f.amount_minor for f in fee_findings) == 260_000 * 100
    assert len(evidence_ids(fee_findings, "settlement_lines")) == 1_912
    assert audited.report.totals_minor == {"reconciliation": 31_000_000, "fee_audit": 26_000_000}


def test_the_fee_audit_sits_in_the_fee_month(audited: Audited) -> None:
    year, month = (int(part) for part in audited.key["fee_month"].split("-"))

    for finding in audited.findings(*FEES):
        assert finding.period_start == date(year, month, 1)
        assert finding.rule_version == fees.RULES_VERSION
    for finding in audited.findings(*RECONCILIATION):
        assert finding.rule_version == matching.RULES_VERSION


def test_every_finding_records_how_it_began(audited: Audited) -> None:
    with tenant_session(audited.tenant_id) as session:
        events = list(session.scalars(select(m.FindingEvent)))

    assert len(events) == 6
    assert {(e.from_state, e.to_state, e.actor) for e in events} == {
        (None, "recoverable", "engine")
    }


def test_every_fee_line_was_checked_against_a_contract(audited: Audited) -> None:
    assert audited.report.fee_lines_unchecked == {}
    assert audited.report.fee_lines_checked == audited.count(m.SettlementLine)


def test_loading_the_contract_file_again_changes_nothing(audited: Audited) -> None:
    rows = audited.count(m.FeeContract)

    again = load_contracts_file(
        audited.tenant_id, audited.path / "contracts" / "fee_contracts.json"
    )

    assert (again["read"], again["added"], again["updated"]) == (rows, 0, 0)
    assert audited.count(m.FeeContract) == rows == 16


# --- The traps ---------------------------------------------------------------------------


def test_trap_t1_one_credit_covering_three_settlements_is_linked_as_a_group(
    audited: Audited,
) -> None:
    trap = audited.trap("T1")
    grouped = [
        link for link in audited.links("settlement", "bank_credit") if link.rule == "group_sum"
    ]

    assert sorted(link.left_id for link in grouped) == sorted(
        f"payu:{settlement_id}" for settlement_id in trap["settlements"]
    )
    assert len({link.right_id for link in grouped}) == 1
    credited_on, amount = trap["bank_credits"][0].split(":")
    assert sum(link.amount_minor or 0 for link in grouped) == int(amount)
    with tenant_session(audited.tenant_id) as session:
        credit = session.scalar(
            select(m.BankCredit).where(m.BankCredit.line_hash == grouped[0].right_id)
        )
    assert credit is not None
    assert (credit.credited_on.isoformat(), credit.amount_minor) == (credited_on, int(amount))
    assert not every_evidence_id(audited.findings()) & set(trap["settlements"])


def test_trap_t6_a_credit_two_days_late_is_matched_inside_the_window(audited: Audited) -> None:
    trap = audited.trap("T6")
    (settlement_id,) = trap["settlements"]
    by_window = [
        link for link in audited.links("settlement", "bank_credit") if link.rule == "amount_date"
    ]

    assert [link.left_id for link in by_window] == [f"razorpay:{settlement_id}"]
    assert by_window[0].amount_minor == int(trap["bank_credits"][0].split(":")[1])
    assert settlement_id not in every_evidence_id(audited.findings())


def test_every_settlement_reaches_the_bank(audited: Audited) -> None:
    links = audited.links("settlement", "bank_credit")

    assert audited.report.unmatched_settlements == []
    assert len({link.left_id for link in links}) == audited.count(m.Settlement)
    assert Counter(link.rule for link in links) == {
        "utr": audited.count(m.Settlement) - 4,
        "group_sum": 3,
        "amount_date": 1,
    }
    # Only the links made without a reference wait for a person to look at them.
    assert audited.report.review_links == 4


def test_trap_t2_a_settlement_delivered_twice_is_counted_once(audited: Audited) -> None:
    (settlement_id,) = audited.trap("T2")["settlements"]
    with tenant_session(audited.tenant_id) as session:
        lines = session.scalar(
            select(func.count())
            .select_from(m.SettlementLine)
            .where(m.SettlementLine.settlement_id == settlement_id)
        )
        linked = session.scalar(
            select(func.count())
            .select_from(m.MatchLink)
            .where(
                m.MatchLink.right_type == "settlement_line",
                m.MatchLink.right_id.like(f"payu:{settlement_id}/%"),
            )
        )

    assert lines and linked == lines
    assert settlement_id not in every_evidence_id(audited.findings())


def test_trap_t3_a_failed_attempt_then_a_retry_is_one_order_and_no_finding(
    audited: Audited,
) -> None:
    trap = audited.trap("T3")
    by_order = Counter(
        link.left_id
        for link in audited.links("order", "payment_attempt")
        if link.left_id in trap["orders"]
    )

    assert set(by_order.values()) == {2}
    assert len(by_order) == len(trap["orders"])
    assert not every_evidence_id(audited.findings()) & set(trap["orders"] + trap["attempts"])


def test_trap_t5_a_refund_netted_against_a_later_payout_is_matched(audited: Audited) -> None:
    trap = audited.trap("T5")
    refund_links = {
        link.left_id: link.right_id for link in audited.links("refund", "settlement_line")
    }
    capture_links = {
        link.left_id: link.right_id for link in audited.links("payment_attempt", "settlement_line")
    }

    for attempt_id, refund_id in zip(trap["attempts"], trap["refunds"], strict=True):
        refund_settlement = refund_links[f"cashfree:{refund_id}"].split("/")[0]
        capture_settlement = capture_links[f"cashfree:{attempt_id}"].split("/")[0]
        assert refund_settlement != capture_settlement
    assert not every_evidence_id(audited.findings()) & set(trap["attempts"] + trap["refunds"])


# --- Links ----------------------------------------------------------------------------------


def test_there_is_a_link_for_every_pairing(audited: Audited) -> None:
    with tenant_session(audited.tenant_id) as session:
        attempts_with_order = session.scalar(
            select(func.count())
            .select_from(m.PaymentAttempt)
            .join(
                m.Order,
                (m.Order.tenant_id == m.PaymentAttempt.tenant_id)
                & (m.Order.merchant_order_id == m.PaymentAttempt.merchant_order_id),
            )
        )
        captures = session.scalar(
            select(func.count())
            .select_from(m.SettlementLine)
            .where(
                m.SettlementLine.line_type == "capture", m.SettlementLine.attempt_id.is_not(None)
            )
        )
        refund_lines = session.scalar(
            select(func.count())
            .select_from(m.SettlementLine)
            .where(m.SettlementLine.line_type == "refund")
        )

    assert len(audited.links("order", "payment_attempt")) == attempts_with_order
    assert len(audited.links("payment_attempt", "settlement_line")) == captures
    assert len(audited.links("refund", "settlement_line")) == refund_lines
    assert {link.created_by for link in audited.links("order", "payment_attempt")} == {"engine"}


# --- Running again, and the lifecycle -----------------------------------------------------------


def test_running_the_audit_again_changes_nothing(audited: Audited) -> None:
    before = (
        audited.count(m.MatchLink),
        audited.count(m.Finding),
        audited.count(m.FindingEvent),
    )

    again = run_audit(audited.tenant_id, as_of=AS_OF)

    assert (again.links_added, again.findings_created, again.findings_updated) == (0, 0, 0)
    assert again.findings_moved == 0
    assert again.totals_minor == audited.report.totals_minor
    assert before == (
        audited.count(m.MatchLink),
        audited.count(m.Finding),
        audited.count(m.FindingEvent),
    )


def test_a_finding_moves_through_its_lifecycle_and_every_step_is_recorded(
    audited: Audited,
) -> None:
    (finding,) = audited.findings("fee_not_in_contract")

    with tenant_session(audited.tenant_id) as session:
        store.move_finding(
            session, audited.tenant_id, finding.id, "claimed", actor="user", reason="Claim sent"
        )
    with tenant_session(audited.tenant_id) as session, pytest.raises(fd.InvalidTransition):
        store.move_finding(session, audited.tenant_id, finding.id, "lost", actor="user")
    # A later run leaves a claimed finding alone while the leak is still there.
    again = run_audit(audited.tenant_id, as_of=AS_OF)

    assert (again.findings_created, again.findings_moved) == (0, 0)
    (after,) = audited.findings("fee_not_in_contract")
    assert (after.state, after.amount_minor) == ("claimed", finding.amount_minor)
    with tenant_session(audited.tenant_id) as session:
        events = list(
            session.scalars(
                select(m.FindingEvent)
                .where(m.FindingEvent.finding_id == finding.id)
                .order_by(m.FindingEvent.occurred_at)
            )
        )
    assert [(e.from_state, e.to_state, e.actor, e.reason) for e in events[1:]] == [
        ("recoverable", "claimed", "user", "Claim sent")
    ]
    # Claimed money is no longer counted as waiting to be claimed.
    assert again.totals_minor["fee_audit"] == 26_000_000 - finding.amount_minor


def test_a_link_a_person_undid_stays_undone(audited: Audited) -> None:
    (late,) = [
        link for link in audited.links("settlement", "bank_credit") if link.rule == "amount_date"
    ]
    with tenant_session(audited.tenant_id) as session:
        row = session.get(m.MatchLink, late.id)
        assert row is not None
        row.active = False

    again = run_audit(audited.tenant_id, as_of=AS_OF)

    assert again.links_added == 0
    assert again.unmatched_settlements == [late.left_id]
    # Put it back so the other tests see the world as it was.
    with tenant_session(audited.tenant_id) as session:
        row = session.get(m.MatchLink, late.id)
        assert row is not None
        row.active = True


# --- The engines are pure -------------------------------------------------------------------------

ENGINE = Path(__file__).resolve().parents[1] / "revlane" / "engine"
ALLOWED_IMPORTS = {
    "bisect",
    "collections",
    "dataclasses",
    "datetime",
    "decimal",
    "itertools",
    "math",
    "typing",
    "revlane.money",
    "revlane.engine",
    "revlane.connectors.statuses",  # the status mapping table: data, no behaviour
}


@pytest.mark.parametrize("module", sorted(path.name for path in ENGINE.glob("*.py")))
def test_engine_modules_touch_nothing_but_their_arguments(module: str) -> None:
    source = (ENGINE / module).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {f"{node.module}.{alias.name}" for alias in node.names}

    def allowed(name: str) -> bool:
        return any(name == ok or name.startswith(f"{ok}.") for ok in ALLOWED_IMPORTS)

    assert sorted(name for name in imported if not allowed(name)) == []
    # No clock, no randomness, no files: the caller passes in the date.
    for banned in (".now(", ".today(", "open(", "random"):
        assert banned not in source, f"{module} uses {banned}"
