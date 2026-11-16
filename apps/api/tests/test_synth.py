"""The synthetic merchant: determinism, and the answer key checked against the files.

The leak checks below read the generated provider files, not the generator's
internal state, and recompute each planted leak from scratch. They are a
reference for what the engines must find in Milestone 4.
"""

import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from revlane.money import bps_of
from revlane.money import from_decimal_string as paise
from revlane.synth import config as c
from revlane.synth.__main__ import main
from revlane.synth.generate import Result, generate

AS_OF = date(2026, 11, 16)
SCALE = 0.02
Row = dict[str, str]


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> Result:
    return generate(seed=42, as_of=AS_OF, scale=SCALE, out_root=tmp_path_factory.mktemp("synth"))


@pytest.fixture(scope="module")
def key(run: Result) -> dict[str, Any]:
    with (run.path / "answer_key.json").open(encoding="utf-8") as handle:
        loaded: dict[str, Any] = json.load(handle)
    return loaded


def records(run: Result, name: str) -> list[dict[str, Any]]:
    with (run.path / name).open(encoding="utf-8") as handle:
        return [json.loads(line, parse_float=Decimal) for line in handle]


def rows(run: Result, name: str) -> list[Row]:
    with (run.path / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def leak(key: dict[str, Any], leak_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(item for item in key["leaks"] if item["id"] == leak_id)
    return found


def trap(key: dict[str, Any], trap_id: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = next(t for t in key["traps"] if t["id"] == trap_id)["record_ids"]
    return found


def contract_rate(run: Result, provider: str, method: str, network: str) -> int:
    with (run.path / "contracts/fee_contracts.json").open(encoding="utf-8") as handle:
        contracts = json.load(handle)
    mine = [r for r in contracts if r["provider"] == provider and r["method"] == method]
    exact = [r for r in mine if r["card_network"] == network]
    rate: int = (exact or [r for r in mine if r["card_network"] is None])[0]["rate_bps"]
    return rate


# --- determinism ---------------------------------------------------------------


def test_two_runs_with_the_same_seed_are_byte_identical(run: Result, tmp_path: Path) -> None:
    again = generate(seed=42, as_of=AS_OF, scale=SCALE, out_root=tmp_path)

    assert again.files == run.files
    for name in run.files:
        assert (again.path / name).read_bytes() == (run.path / name).read_bytes()


def test_a_different_seed_gives_a_different_merchant_with_the_same_totals(
    run: Result, tmp_path: Path
) -> None:
    other = generate(seed=7, as_of=AS_OF, scale=SCALE, out_root=tmp_path)

    assert other.files["razorpay/payments.jsonl"] != run.files["razorpay/payments.jsonl"]
    assert other.answer_key["totals_minor"] == run.answer_key["totals_minor"]


def test_every_file_in_the_document_is_written(run: Result) -> None:
    expected = {
        "razorpay/payments.jsonl",
        "razorpay/settlements.jsonl",
        "razorpay/settlement_recon.jsonl",
        "razorpay/refunds.jsonl",
        "razorpay/webhooks.jsonl",
        "payu/transactions.jsonl",
        "payu/settlements.csv",
        "payu/refunds.jsonl",
        "cashfree/orders.jsonl",
        "cashfree/payments.jsonl",
        "cashfree/settlements.csv",
        "marketplace/payouts.csv",
        "orders/bookings.csv",
        "contracts/fee_contracts.json",
        "answer_key.json",
        "bank/hdfc_statement_2026-08.csv",
        "bank/hdfc_statement_2026-09.csv",
        "bank/hdfc_statement_2026-10.csv",
        "bank/hdfc_statement_2026-11.csv",
    }

    assert expected <= set(run.files)
    assert (run.path.parent / "ASSUMPTIONS.md").read_text(encoding="utf-8").startswith("# Assum")


# --- the answer key ----------------------------------------------------------------


def test_answer_key_totals_match_the_document(key: dict[str, Any]) -> None:
    amounts = {item["id"]: item["amount_minor"] for item in key["leaks"]}

    assert key["totals_minor"] == {"reconciliation": 31_000_000, "fee_audit": 26_000_000}
    assert amounts == {
        "R1": 13_000_000,
        "R2": 12_000_000,
        "R3": 6_000_000,
        "F1": 16_000_000,
        "F2": 6_000_000,
        "F3": 4_000_000,
    }
    assert key["fee_audit_lines"] == 1_912
    assert key["seed"] == 42
    assert key["generated_on"] == "2026-11-16"
    assert key["period"] == {"start": "2026-08-19", "end": "2026-11-16"}
    assert key["fee_month"] == "2026-10"


def test_each_leak_lists_its_records_and_adds_up(key: dict[str, Any]) -> None:
    counts = {"R1": 40, "R2": 3, "R3": 22, "F1": 1_000, "F2": 800, "F3": 112}

    for item in key["leaks"]:
        assert len(item["items"]) == counts[item["id"]]
        assert sum(i["amount_minor"] for i in item["items"]) == item["amount_minor"]
        assert all(type(i["amount_minor"]) is int and i["amount_minor"] > 0 for i in item["items"])
        assert item["expected_state"] == "recoverable"
    assert [t["id"] for t in key["traps"]] == ["T1", "T2", "T3", "T4", "T5", "T6", "T7"]


# --- reconciliation leaks, recomputed from the files -----------------------------------


def test_r1_paid_then_auto_refunded(run: Result, key: dict[str, Any]) -> None:
    payments = records(run, "razorpay/payments.jsonl")
    auto = [p for p in payments if p["status"] == "refunded" and not p["captured"]]
    refunds = {r["payment_id"]: r for r in records(run, "razorpay/refunds.jsonl")}
    settled = {line["payment_id"] for line in records(run, "razorpay/settlement_recon.jsonl")}
    orders = {o["order_id"]: o for o in rows(run, "orders/bookings.csv")}
    r1 = leak(key, "R1")

    assert sorted(p["id"] for p in auto) == sorted(r1["record_ids"]["attempts"])
    assert len(auto) == 40
    assert sum(p["amount"] for p in auto) == 13_000_000
    for payment in auto:
        refund = refunds[payment["id"]]
        placed = datetime.fromtimestamp(payment["created_at"]).date()
        assert refund["notes"]["reason"] == "payment_not_captured_auto_refund"
        assert refund["amount"] == payment["amount"]
        assert payment["id"] not in settled
        assert orders[payment["notes"]["booking_id"]]["status"] == "unconfirmed"
        assert AS_OF - timedelta(days=7) <= placed <= AS_OF


def test_r2_three_marketplace_payouts_arrive_short(run: Result, key: dict[str, Any]) -> None:
    owed: dict[str, int] = defaultdict(int)
    paid: dict[str, int] = {}
    utr: dict[str, str] = {}
    for row in rows(run, "marketplace/payouts.csv"):
        if row["record_type"] == "LINE":
            owed[row["payout_id"]] += paise(row["net_amount"])
        else:
            paid[row["payout_id"]] = paise(row["payout_amount"])
            utr[row["payout_id"]] = row["utr"]
    short = {payout: owed[payout] - paid[payout] for payout in paid if owed[payout] != paid[payout]}
    credited = {r["Chq./Ref.No."]: paise(r["Deposit Amt."]) for r in bank_rows(run)}

    assert short == {i["id"]: i["amount_minor"] for i in leak(key, "R2")["items"]}
    assert sorted(short.values()) == [3_500_000, 4_000_000, 4_500_000]
    assert sorted(short) == sorted(paid)[-3:]
    # The bank received the short amount, so the money really is missing.
    assert all(credited[utr[payout]] == paid[payout] for payout in paid)


def test_r3_captured_and_not_settled(run: Result, key: dict[str, Any]) -> None:
    settled = {row["mihpayid"] for row in rows(run, "payu/settlements.csv")}
    overdue = [
        t
        for t in records(run, "payu/transactions.jsonl")
        if t["unmappedstatus"] == "captured"
        and t["mihpayid"] not in settled
        # Next-day settlement plus two working days has passed.
        and datetime.fromisoformat(t["addedon"]).date() <= AS_OF - timedelta(days=5)
    ]

    assert sorted(t["mihpayid"] for t in overdue) == sorted(
        leak(key, "R3")["record_ids"]["attempts"]
    )
    assert len(overdue) == 22
    assert sum(paise(t["amount"]) for t in overdue) == 6_000_000


def test_no_other_provider_has_overdue_unsettled_payments(run: Result) -> None:
    cutoff = AS_OF - timedelta(days=3)
    razorpay_settled = {r["payment_id"] for r in records(run, "razorpay/settlement_recon.jsonl")}
    cashfree_settled = {r["cf_payment_id"] for r in rows(run, "cashfree/settlements.csv")}

    razorpay_overdue = [
        p["id"]
        for p in records(run, "razorpay/payments.jsonl")
        if p["status"] == "captured"
        and p["id"] not in razorpay_settled
        and datetime.fromtimestamp(p["created_at"]).date() <= cutoff
    ]
    cashfree_overdue = [
        p["cf_payment_id"]
        for p in records(run, "cashfree/payments.jsonl")
        if p["payment_status"] == "SUCCESS"
        and p["cf_payment_id"] not in cashfree_settled
        and datetime.fromisoformat(p["payment_time"]).date() <= cutoff
    ]

    assert razorpay_overdue == []
    assert cashfree_overdue == []


# --- fee leaks, recomputed from the files ------------------------------------------------


def with_tax(fee: int) -> int:
    return fee + bps_of(fee, 1_800)


def test_f1_cashfree_amex_above_contract(run: Result, key: dict[str, Any]) -> None:
    amex = contract_rate(run, "cashfree", "card", "amex")
    over: dict[str, int] = {}
    for row in rows(run, "cashfree/settlements.csv"):
        if row["event_type"] != "PAYMENT":
            continue
        method = (
            "card"
            if row["payment_group"].endswith("_card")
            else {"net_banking": "netbanking"}.get(row["payment_group"], row["payment_group"])
        )
        rate = contract_rate(run, "cashfree", method, row["card_network"])
        charged = paise(row["service_charge"]) + paise(row["service_tax"])
        agreed = with_tax(bps_of(paise(row["event_amount"]), rate))
        if charged != agreed:
            over[f"{row['cf_settlement_id']}/{row['event_id']}"] = charged - agreed
            assert row["card_network"] == "amex"
            assert row["settlement_date"].startswith("2026-10")

    assert amex == 200
    assert over == {i["id"]: i["amount_minor"] for i in leak(key, "F1")["items"]}
    assert len(over) == 1_000
    assert sum(over.values()) == 16_000_000


def test_f2_payu_keeps_tax_on_refunded_fees(run: Result, key: dict[str, Any]) -> None:
    kept: dict[str, int] = {}
    for row in rows(run, "payu/settlements.csv"):
        if row["record_type"] != "refund":
            continue
        fee_back, tax_back = -paise(row["mdr"]), -paise(row["gst"])
        due = bps_of(fee_back, 1_800)
        if tax_back != due:
            kept[f"{row['settlement_id']}/{row['line_id']}"] = due - tax_back
            assert tax_back == 0
            assert row["settlement_date"].startswith("2026-10")

    assert kept == {i["id"]: i["amount_minor"] for i in leak(key, "F2")["items"]}
    assert len(kept) == 800
    assert sum(kept.values()) == 6_000_000


def test_f3_razorpay_fee_not_in_contract(run: Result, key: dict[str, Any]) -> None:
    extra = {
        f"{line['settlement_id']}/{line['entity_id']}": line["fee"] + line["tax"]
        for line in records(run, "razorpay/settlement_recon.jsonl")
        if line["type"] == "adjustment"
    }
    days = {
        datetime.fromtimestamp(line["settled_at"]).strftime("%Y-%m")
        for line in records(run, "razorpay/settlement_recon.jsonl")
        if line["type"] == "adjustment"
    }

    assert extra == {i["id"]: i["amount_minor"] for i in leak(key, "F3")["items"]}
    assert len(extra) == 112
    assert sum(extra.values()) == 4_000_000
    assert days == {"2026-10"}


def test_fee_leaks_cover_1912_lines_and_26_lakh(key: dict[str, Any]) -> None:
    lines = [i for leak_id in ("F1", "F2", "F3") for i in leak(key, leak_id)["items"]]

    assert len(lines) == 1_912
    assert len({i["id"] for i in lines}) == 1_912
    assert sum(i["amount_minor"] for i in lines) == 26_000_000


def test_razorpay_and_payu_payment_fees_follow_the_contract(run: Result) -> None:
    """No overcharge anywhere except the planted ones."""
    for line in records(run, "razorpay/settlement_recon.jsonl"):
        if line["type"] != "payment":
            continue
        rate = contract_rate(run, "razorpay", line["method"], line["card_network"])
        fee = bps_of(line["amount"], rate)
        assert (line["fee"], line["tax"]) == (fee, bps_of(fee, 1_800))

    cards = {t["mihpayid"]: t.get("card_type", "") for t in records(run, "payu/transactions.jsonl")}
    modes = {t["mihpayid"]: t["mode"] for t in records(run, "payu/transactions.jsonl")}
    for row in rows(run, "payu/settlements.csv"):
        if row["record_type"] != "payment":
            continue
        mode = modes[row["mihpayid"]]
        method = {"UPI": "upi", "NB": "netbanking", "CASH": "wallet"}.get(mode, "card")
        network = "amex" if cards[row["mihpayid"]] == "AMEX" else ""
        fee = bps_of(paise(row["amount"]), contract_rate(run, "payu", method, network))
        assert (paise(row["mdr"]), paise(row["gst"])) == (fee, bps_of(fee, 1_800))


# --- settlements and the bank ------------------------------------------------------------


def bank_rows(run: Result) -> list[Row]:
    return [
        row for name in sorted(run.files) if name.startswith("bank/") for row in rows(run, name)
    ]


def gateway_settlements(run: Result) -> dict[str, tuple[str, int, int]]:
    """utr -> (settlement id, amount paid, sum of line nets), for the three gateways."""
    out: dict[str, tuple[str, int, int]] = {}
    nets: dict[str, int] = defaultdict(int)
    for line in records(run, "razorpay/settlement_recon.jsonl"):
        nets[line["settlement_id"]] += line["credit"] - line["debit"]
    for s in records(run, "razorpay/settlements.jsonl"):
        out[s["utr"]] = (s["id"], s["amount"], nets[s["id"]])
    for name, sid, utr_field, amount, net in (
        ("payu/settlements.csv", "settlement_id", "utr", "settlement_amount", "net_amount"),
        (
            "cashfree/settlements.csv",
            "cf_settlement_id",
            "settlement_utr",
            "settlement_amount",
            "event_settlement_amount",
        ),
    ):
        totals: dict[str, int] = defaultdict(int)
        head: dict[str, tuple[str, int]] = {}
        for row in rows(run, name):
            totals[row[sid]] += paise(row[net])
            head[row[sid]] = (row[utr_field], paise(row[amount]))
        for settlement, (utr, paid) in head.items():
            out[utr] = (settlement, paid, totals[settlement])
    return out


def test_every_gateway_settlement_pays_exactly_the_sum_of_its_lines(run: Result) -> None:
    settlements = gateway_settlements(run)

    assert len(settlements) > 200
    assert all(paid == owed and paid > 0 for _, paid, owed in settlements.values())


def test_bank_credits_match_settlements_by_reference_except_the_two_traps(
    run: Result, key: dict[str, Any]
) -> None:
    settlements = gateway_settlements(run)
    credits = {
        r["Chq./Ref.No."]: paise(r["Deposit Amt."]) for r in bank_rows(run) if r["Chq./Ref.No."]
    }
    unreferenced = sorted(s for utr, (s, _, _) in settlements.items() if utr not in credits)
    by_id = {s: paid for s, paid, _ in settlements.values()}

    assert all(credits[utr] == paid for utr, (_, paid, _) in settlements.items() if utr in credits)
    assert unreferenced == sorted(trap(key, "T1")["settlements"] + trap(key, "T6")["settlements"])

    # T1: one credit, no reference, equal to the three settlements together.
    blank = [r for r in bank_rows(run) if not r["Chq./Ref.No."]]
    group_total = sum(by_id[s] for s in trap(key, "T1")["settlements"])
    assert len(trap(key, "T1")["settlements"]) == 3
    assert [paise(r["Deposit Amt."]) for r in blank if "BULK" in r["Narration"]] == [group_total]
    assert group_total not in by_id.values()

    # T6: settled on a Saturday, credited two days later, no reference.
    (late,) = trap(key, "T6")["settlements"]
    (credit,) = [r for r in blank if "BULK" not in r["Narration"]]
    credited = datetime.strptime(credit["Date"], "%d/%m/%y").date()
    settled = next(
        datetime.fromtimestamp(s["created_at"]).date()
        for s in records(run, "razorpay/settlements.jsonl")
        if s["id"] == late
    )
    assert paise(credit["Deposit Amt."]) == by_id[late]
    assert settled.weekday() == 5
    assert credited - settled == timedelta(days=2)


def test_t2_one_settlement_is_delivered_twice(run: Result, key: dict[str, Any]) -> None:
    original = rows(run, "payu/settlements.csv")
    again = rows(run, "payu/settlements_redelivery.csv")
    (settlement,) = trap(key, "T2")["settlements"]

    assert again
    assert again == [row for row in original if row["settlement_id"] == settlement]


def test_t3_fails_then_succeeds_within_ten_minutes(run: Result, key: dict[str, Any]) -> None:
    by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for payment in records(run, "razorpay/payments.jsonl"):
        by_order[payment["notes"]["booking_id"]].append(payment)

    assert len(trap(key, "T3")["orders"]) == 10
    for order in trap(key, "T3")["orders"]:
        first, retry = sorted(by_order[order], key=lambda p: p["created_at"])
        assert (first["status"], retry["status"]) == ("failed", "captured")
        assert 0 < retry["created_at"] - first["created_at"] <= 600
        assert first["amount"] == retry["amount"]


def test_t4_pending_then_failed_two_days_later(run: Result, key: dict[str, Any]) -> None:
    reports: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for report in records(run, "payu/transactions.jsonl"):
        reports[report["mihpayid"]].append(report)
    twice = sorted(txn for txn, seen in reports.items() if len(seen) > 1)

    assert twice == sorted(trap(key, "T4")["attempts"])
    assert len(twice) == 8
    for txn in twice:
        first, second = reports[txn]
        gap = datetime.fromisoformat(second["updated_on"]) - datetime.fromisoformat(
            first["updated_on"]
        )
        assert (first["unmappedstatus"], second["unmappedstatus"]) == ("pending", "failed")
        assert timedelta(days=2) <= gap < timedelta(days=3)


def test_t5_refund_comes_out_of_a_later_payout(run: Result, key: dict[str, Any]) -> None:
    paid_in: dict[str, str] = {}
    refunded_in: dict[str, str] = {}
    for row in rows(run, "cashfree/settlements.csv"):
        target = refunded_in if row["event_type"] == "REFUND" else paid_in
        target[row["cf_payment_id"]] = row["settlement_date"]

    assert len(trap(key, "T5")["attempts"]) == 5
    for attempt in trap(key, "T5")["attempts"]:
        assert refunded_in[attempt] > paid_in[attempt]


def test_t7_five_records_carry_a_status_nobody_knows(run: Result, key: dict[str, Any]) -> None:
    known = {
        "razorpay": {"created", "authorized", "captured", "refunded", "failed"},
        "payu": {
            "captured",
            "auth",
            "failed",
            "dropped",
            "pending",
            "in progress",
            "bounced",
            "userCancelled",
            "initiated",
            "autoRefund",
        },
        "cashfree": {
            "SUCCESS",
            "FAILED",
            "PENDING",
            "NOT_ATTEMPTED",
            "USER_DROPPED",
            "CANCELLED",
            "FLAGGED",
        },
    }
    odd = (
        [
            p["id"]
            for p in records(run, "razorpay/payments.jsonl")
            if p["status"] not in known["razorpay"]
        ]
        + [
            t["mihpayid"]
            for t in records(run, "payu/transactions.jsonl")
            if t["unmappedstatus"] not in known["payu"]
        ]
        + [
            p["cf_payment_id"]
            for p in records(run, "cashfree/payments.jsonl")
            if p["payment_status"] not in known["cashfree"]
        ]
    )

    assert sorted(odd) == sorted(trap(key, "T7")["attempts"])
    assert len(odd) == 5


# --- shape and hygiene ------------------------------------------------------------------------


def test_volume_follows_the_documented_mix(run: Result) -> None:
    providers: Counter[str] = Counter()
    for name, count in (
        ("razorpay/payments.jsonl", "razorpay"),
        ("payu/transactions.jsonl", "payu"),
        ("cashfree/payments.jsonl", "cashfree"),
    ):
        providers[count] = len(records(run, name))
    orders = rows(run, "orders/bookings.csv")

    # Everyday volume is 2% of 4,000 a day; the planted records come on top.
    assert 6_500 < sum(providers.values()) < 10_500
    assert providers["razorpay"] > providers["payu"] > 0
    assert {o["channel"] for o in orders} == {"web", "marketplace"}
    assert len({o["order_id"] for o in orders}) == len(orders)


def test_amounts_are_written_without_floats(run: Result) -> None:
    for payment in records(run, "cashfree/payments.jsonl")[:200]:
        assert isinstance(payment["payment_amount"], Decimal)
        assert payment["payment_amount"].as_tuple().exponent == -2
    for payment in records(run, "razorpay/payments.jsonl")[:200]:
        assert type(payment["amount"]) is int
    for txn in records(run, "payu/transactions.jsonl")[:200]:
        assert paise(txn["amount"]) > 0


def test_every_contact_detail_is_obviously_synthetic(run: Result) -> None:
    for payment in records(run, "razorpay/payments.jsonl"):
        assert payment["email"].endswith("@example.com")
        assert payment["contact"].startswith("+9198")
    for txn in records(run, "payu/transactions.jsonl"):
        assert txn["email"].endswith("@example.com")
        assert "X" in txn.get("cardnum", "X")


# --- the command and its limits ------------------------------------------------------------------


def test_make_synth_command_writes_and_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--as-of", "2026-11-16", "--scale", "0.02", "--out", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "leaks planted  F1, F2, F3, R1, R2, R3" in out
    assert (tmp_path / "42" / "answer_key.json").exists()


def test_another_generation_date_moves_the_period_and_keeps_the_totals(tmp_path: Path) -> None:
    result = generate(seed=42, as_of=date(2027, 3, 9), scale=SCALE, out_root=tmp_path)

    assert result.answer_key["period"] == {"start": "2026-12-10", "end": "2027-03-09"}
    assert result.answer_key["fee_month"] == "2027-02"
    assert result.answer_key["totals_minor"] == {
        "reconciliation": 31_000_000,
        "fee_audit": 26_000_000,
    }


def test_a_scale_of_zero_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        generate(scale=0, out_root=tmp_path)


def test_calendar_picks_the_last_full_month() -> None:
    cal = c.calendar_for(date(2026, 11, 16))

    assert len(cal.days) == 90
    assert (cal.days[cal.fee_month_first], cal.days[cal.fee_month_last]) == (
        date(2026, 10, 1),
        date(2026, 10, 31),
    )
