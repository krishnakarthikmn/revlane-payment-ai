"""Writes the synthetic merchant in each provider's own file and payload shape.

All provider knowledge on the generating side lives here. The shapes follow
each provider's public documentation where it is clear; every choice made
where it is not is recorded in ASSUMPTIONS.md next to this file.
"""

import calendar
import csv
import io
import json
from collections.abc import Iterable, Iterator
from datetime import date, datetime, timedelta
from typing import Any

from revlane.money import to_decimal_string as rupees
from revlane.synth import config as c
from revlane.synth.world import Attempt, Line, Refund, Settlement, World

IST = timedelta(hours=5, minutes=30)
Files = dict[str, str]


class Number(str):
    """A decimal written into JSON as a bare number, without passing through a float."""


def dumps(value: object) -> str:
    if isinstance(value, Number):
        return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(f"{json.dumps(k)}:{dumps(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(dumps(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def jsonl(records: Iterable[dict[str, Any]]) -> str:
    return "".join(dumps(record) + "\n" for record in records)


def table(header: list[str], rows: Iterable[list[str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


def epoch(local: datetime) -> int:
    """Seconds since the epoch for a naive India-time timestamp."""
    return calendar.timegm((local - IST).timetuple())


def iso(local: datetime) -> str:
    return local.strftime("%Y-%m-%dT%H:%M:%S+05:30")


def plain(local: datetime) -> str:
    return local.strftime("%Y-%m-%d %H:%M:%S")


def at_ten(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time()) + timedelta(hours=10)


def email(shopper: int) -> str:
    return f"shopper{shopper}@example.com"


def phone(shopper: int) -> str:
    return f"+9198{shopper:08d}"


UPI_HANDLE = {"gpay": "okhdfcbank", "phonepe": "ybl", "paytm": "paytm", "bhim": "upi"}
ISSUER = {
    "HDFC": "HDFC",
    "ICICI": "ICIC",
    "SBI": "SBIN",
    "AXIS": "UTIB",
    "KOTAK": "KKBK",
    "YES": "YESB",
}


def vpa(attempt: Attempt) -> str:
    return f"shopper{attempt.shopper}@{UPI_HANDLE[attempt.upi_app or 'bhim']}"


def last4(attempt: Attempt) -> str:
    return f"{attempt.shopper % 10_000:04d}"


# --- Razorpay --------------------------------------------------------------------

RAZORPAY_ERRORS = {
    "technical_timeout": ("GATEWAY_ERROR", "gateway_timeout"),
    "insufficient_funds": ("BAD_REQUEST_ERROR", "insufficient_funds"),
    "bank_declined": ("BAD_REQUEST_ERROR", "payment_declined"),
    "auth_failed": ("BAD_REQUEST_ERROR", "authentication_failed"),
    "auth_abandoned": ("BAD_REQUEST_ERROR", "payment_cancelled"),
    "risk_blocked": ("BAD_REQUEST_ERROR", "payment_risk_check_failed"),
}


def razorpay_status(attempt: Attempt) -> str:
    if attempt.odd_status:
        return attempt.odd_status
    return {
        "succeeded": "captured",
        "failed": "failed",
        "abandoned": "created",
        # Authorised, never captured, then refunded by Razorpay on its own.
        "auto_refunded": "refunded",
    }[attempt.truth]


def razorpay_payment(attempt: Attempt, status: str | None = None) -> dict[str, Any]:
    status = status or razorpay_status(attempt)
    code, reason = RAZORPAY_ERRORS.get(attempt.reason or "", (None, None))
    failed = status == "failed"
    refunded = attempt.truth == "auto_refunded"
    record: dict[str, Any] = {
        "id": attempt.attempt_id,
        "entity": "payment",
        "amount": attempt.amount,
        "currency": "INR",
        "status": status,
        "order_id": f"order_{attempt.order_id[3:]}{attempt.seq}",
        "method": attempt.method,
        "amount_refunded": attempt.amount if refunded else 0,
        "refund_status": "full" if refunded else None,
        "captured": status == "captured",
        "description": f"Acme Travel booking {attempt.order_id}",
        "bank": attempt.bank if attempt.method in ("netbanking", "upi") else None,
        "wallet": c.WALLETS[attempt.shopper % len(c.WALLETS)]
        if attempt.method == "wallet"
        else None,
        "vpa": vpa(attempt) if attempt.method == "upi" else None,
        "email": email(attempt.shopper),
        "contact": phone(attempt.shopper),
        "notes": {"booking_id": attempt.order_id},
        "fee": attempt.fee + attempt.tax if status == "captured" else None,
        "tax": attempt.tax if status == "captured" else None,
        "error_code": code if failed else None,
        "error_reason": reason if failed else None,
        "created_at": epoch(attempt.created),
    }
    if attempt.method == "card":
        record["card"] = {
            "id": f"card_{attempt.attempt_id[4:]}",
            "entity": "card",
            "name": f"SHOPPER {attempt.shopper}",
            "last4": last4(attempt),
            "network": {
                "visa": "Visa",
                "mastercard": "MasterCard",
                "rupay": "RuPay",
                "amex": "American Express",
            }[attempt.network or "visa"],
            "type": attempt.card_type,
            "issuer": ISSUER[attempt.bank],
            "international": False,
        }
    return record


def razorpay_recon(line: Line, settlement: Settlement, day: date) -> dict[str, Any]:
    kind = {"payment": "payment", "refund": "refund", "fee": "adjustment"}[line.kind]
    attempt = line.attempt
    return {
        "entity_id": line.ref,
        "type": kind,
        "debit": -line.net if line.net < 0 else 0,
        "credit": line.net if line.net > 0 else 0,
        "amount": abs(line.gross),
        "currency": "INR",
        # Negative on a refund line: the fee and tax handed back.
        "fee": line.fee,
        "tax": line.tax,
        "fee_type": line.fee_type,
        "on_hold": False,
        "settled": True,
        "created_at": epoch(line.refund.at if line.refund else attempt.created)
        if attempt
        else epoch(at_ten(day)),
        "settled_at": epoch(at_ten(day)),
        "settlement_id": settlement.settlement_id,
        "settlement_utr": settlement.utr,
        "description": "Instant settlement fee" if line.kind == "fee" else None,
        "payment_id": attempt.attempt_id if attempt else None,
        "method": attempt.method if attempt else None,
        "card_network": attempt.network if attempt else None,
        "card_type": attempt.card_type if attempt else None,
        "notes": {"booking_id": line.order_id} if line.order_id else {},
    }


def razorpay(world: World) -> Files:
    cal = world.cal
    attempts = sorted((a for a in world.attempts if a.provider == "razorpay"), key=by_time)
    refunds = sorted(
        (r for r in world.refunds if r.provider == "razorpay"), key=lambda r: (r.at, r.refund_id)
    )
    settlements = [s for s in world.settlements if s.provider == "razorpay"]

    def refund_record(refund: Refund) -> dict[str, Any]:
        auto = refund.kind == "automatic"
        return {
            "id": refund.refund_id,
            "entity": "refund",
            "amount": refund.amount,
            "currency": "INR",
            "payment_id": refund.attempt.attempt_id,
            "notes": {
                "reason": "payment_not_captured_auto_refund" if auto else "customer_cancellation"
            },
            "status": "processed",
            "speed_processed": "normal",
            "created_at": epoch(refund.at),
        }

    def events() -> Iterator[dict[str, Any]]:
        # Webhooks as they would have arrived on the last day, and for trap T3.
        for attempt in attempts:
            if attempt.day != cal.last and attempt.tag != "T3":
                continue
            status = razorpay_status(attempt)
            name = {"captured": "payment.captured", "failed": "payment.failed"}.get(status)
            if name is None:
                continue
            yield {
                "entity": "event",
                "account_id": "acc_AcmeTravel0001",
                "event": name,
                "contains": ["payment"],
                "payload": {"payment": {"entity": razorpay_payment(attempt, status)}},
                "created_at": epoch(attempt.created) + 4,
            }

    return {
        "razorpay/payments.jsonl": jsonl(razorpay_payment(a) for a in attempts),
        "razorpay/refunds.jsonl": jsonl(refund_record(r) for r in refunds),
        "razorpay/settlements.jsonl": jsonl(
            {
                "id": s.settlement_id,
                "entity": "settlement",
                "amount": s.paid,
                "status": "processed",
                "fees": s.fee + s.tax,
                "tax": s.tax,
                "utr": s.utr,
                "created_at": epoch(at_ten(cal.days[s.day])),
            }
            for s in settlements
        ),
        "razorpay/settlement_recon.jsonl": jsonl(
            razorpay_recon(line, s, cal.days[s.day]) for s in settlements for line in ordered(s)
        ),
        "razorpay/webhooks.jsonl": jsonl(events()),
    }


# --- PayU ------------------------------------------------------------------------

PAYU_ERRORS = {
    "technical_timeout": ("E308", "Bank was unable to authenticate"),
    "insufficient_funds": ("E312", "Insufficient funds"),
    "bank_declined": ("E303", "Transaction declined by bank"),
    "auth_failed": ("E311", "3D Secure authentication failed"),
    "auth_abandoned": ("E1605", "Transaction cancelled at bank page"),
    "risk_blocked": ("E505", "Transaction blocked by risk rules"),
    "user_cancelled": ("E1603", "Cancelled by user"),
}
PAYU_MODE = {"upi": "UPI", "netbanking": "NB", "wallet": "CASH"}


def payu_status(attempt: Attempt) -> tuple[str, str]:
    """(status, unmappedstatus)."""
    if attempt.odd_status:
        return "pending", attempt.odd_status
    if attempt.truth == "abandoned":
        return "failure", "userCancelled" if attempt.shopper % 2 else "bounced"
    if attempt.truth == "failed":
        return "failure", "dropped" if attempt.shopper % 7 == 0 else "failed"
    return {"succeeded": ("success", "captured"), "pending_then_failed": ("pending", "pending")}[
        attempt.truth
    ]


def payu_transaction(attempt: Attempt, later: bool = False) -> dict[str, Any]:
    status, unmapped = ("failure", "failed") if later else payu_status(attempt)
    failed = status == "failure"
    code, message = PAYU_ERRORS.get(attempt.reason or "", ("E500", "Transaction failed"))
    mode = PAYU_MODE.get(attempt.method) or ("CC" if attempt.card_type == "credit" else "DC")
    updated = attempt.failed_at if later and attempt.failed_at else attempt.created
    record: dict[str, Any] = {
        "mihpayid": attempt.attempt_id,
        "mode": mode,
        "status": status,
        "unmappedstatus": unmapped,
        "key": "acmTrv",
        "txnid": f"{attempt.order_id}-{attempt.seq}",
        "amount": rupees(attempt.amount),
        "addedon": plain(attempt.created),
        "updated_on": plain(updated),
        "productinfo": "Acme Travel booking",
        "firstname": f"Shopper{attempt.shopper}",
        "email": email(attempt.shopper),
        "phone": phone(attempt.shopper)[3:],
        "udf1": attempt.order_id,
        "PG_TYPE": f"{mode}-PG",
        "bank_ref_num": f"{attempt.attempt_id[-9:]}{attempt.seq}" if status == "success" else None,
        "bankcode": "UPI" if attempt.method == "upi" else ISSUER[attempt.bank],
        "issuing_bank": attempt.bank,
        "error": code if failed else "E000",
        "error_Message": message if failed else "No Error",
    }
    if attempt.method == "card":
        record["cardnum"] = f"512345XXXXXX{last4(attempt)}"
        record["card_type"] = {
            "visa": "VISA",
            "mastercard": "MAST",
            "rupay": "RUPAY",
            "amex": "AMEX",
        }[attempt.network or "visa"]
    if attempt.method == "upi":
        record["upi_va"] = vpa(attempt)
    return record


PAYU_SETTLEMENT_HEADER = [
    "settlement_id",
    "settlement_date",
    "utr",
    "settlement_amount",
    "line_id",
    "record_type",
    "mihpayid",
    "txnid",
    "request_id",
    "amount",
    "mdr",
    "gst",
    "net_amount",
]


def payu_settlement_rows(world: World, settlement: Settlement) -> Iterator[list[str]]:
    day = world.cal.days[settlement.day].isoformat()
    for line in ordered(settlement):
        attempt = line.attempt
        assert attempt is not None
        yield [
            settlement.settlement_id,
            day,
            settlement.utr,
            rupees(settlement.paid),
            line.line_ref,
            line.kind,
            attempt.attempt_id,
            f"{attempt.order_id}-{attempt.seq}",
            line.ref if line.kind == "refund" else "",
            rupees(line.gross),
            rupees(line.fee),
            rupees(line.tax),
            rupees(line.net),
        ]


def payu(world: World) -> Files:
    attempts = sorted((a for a in world.attempts if a.provider == "payu"), key=by_time)
    refunds = sorted(
        (r for r in world.refunds if r.provider == "payu"), key=lambda r: (r.at, r.refund_id)
    )
    settlements = [s for s in world.settlements if s.provider == "payu"]

    def transactions() -> Iterator[dict[str, Any]]:
        # A status report per transaction; trap T4 reports twice, two days apart.
        stream = [(a.created, a.attempt_id, a, False) for a in attempts]
        stream += [(a.failed_at, a.attempt_id, a, True) for a in attempts if a.failed_at]
        for _, _, attempt, later in sorted(stream, key=lambda item: (item[0], item[1])):
            yield payu_transaction(attempt, later)

    files = {
        "payu/transactions.jsonl": jsonl(transactions()),
        "payu/refunds.jsonl": jsonl(
            {
                "request_id": r.refund_id,
                "mihpayid": r.attempt.attempt_id,
                "txnid": f"{r.attempt.order_id}-{r.attempt.seq}",
                "amt": rupees(r.amount),
                "status": "success",
                "refund_type": "auto" if r.kind == "automatic" else "merchant",
                "addedon": plain(r.at),
            }
            for r in refunds
        ),
        "payu/settlements.csv": table(
            PAYU_SETTLEMENT_HEADER,
            (row for s in settlements for row in payu_settlement_rows(world, s)),
        ),
    }
    if world.redelivered is not None:
        # Trap T2: the same settlement, delivered a second time in its own file.
        files["payu/settlements_redelivery.csv"] = table(
            PAYU_SETTLEMENT_HEADER, payu_settlement_rows(world, world.redelivered)
        )
    return files


# --- Cashfree ----------------------------------------------------------------------

CASHFREE_STATUS = {"succeeded": "SUCCESS", "failed": "FAILED", "abandoned": "USER_DROPPED"}
CASHFREE_GROUP = {"upi": "upi", "netbanking": "net_banking", "wallet": "wallet"}


def cashfree_group(attempt: Attempt) -> str:
    return CASHFREE_GROUP.get(attempt.method) or f"{attempt.card_type}_card"


def cashfree_payment(attempt: Attempt) -> dict[str, Any]:
    status = attempt.odd_status or CASHFREE_STATUS[attempt.truth]
    method: dict[str, Any]
    if attempt.method == "card":
        method = {
            "card": {
                "channel": "link",
                "card_number": f"XXXXXXXXXXXX{last4(attempt)}",
                "card_network": attempt.network,
                "card_type": f"{attempt.card_type}_card",
                "card_bank_name": attempt.bank,
            }
        }
    elif attempt.method == "upi":
        method = {"upi": {"channel": "collect", "upi_id": vpa(attempt)}}
    elif attempt.method == "netbanking":
        method = {"netbanking": {"channel": "link", "netbanking_bank_name": attempt.bank}}
    else:
        method = {
            "app": {"channel": "link", "provider": c.WALLETS[attempt.shopper % len(c.WALLETS)]}
        }
    return {
        "cf_payment_id": attempt.attempt_id,
        "order_id": attempt.order_id,
        "entity": "payment",
        "payment_currency": "INR",
        "payment_amount": Number(rupees(attempt.amount)),
        "order_amount": Number(rupees(attempt.amount)),
        "payment_status": status,
        "payment_message": "Transaction successful"
        if status == "SUCCESS"
        else "Transaction not completed",
        "payment_time": iso(attempt.created),
        "bank_reference": f"{attempt.attempt_id[-8:]}{attempt.seq}"
        if status == "SUCCESS"
        else None,
        "payment_group": cashfree_group(attempt),
        "payment_method": method,
        "error_details": {
            "error_code": attempt.reason,
            "error_reason": attempt.reason,
            "error_source": "bank",
        }
        if status == "FAILED"
        else None,
    }


def cashfree(world: World) -> Files:
    cal = world.cal
    attempts = sorted((a for a in world.attempts if a.provider == "cashfree"), key=by_time)
    refunds = sorted(
        (r for r in world.refunds if r.provider == "cashfree"), key=lambda r: (r.at, r.refund_id)
    )
    settlements = [s for s in world.settlements if s.provider == "cashfree"]

    def rows() -> Iterator[list[str]]:
        for settlement in settlements:
            day = cal.days[settlement.day].isoformat()
            for line in ordered(settlement):
                attempt = line.attempt
                assert attempt is not None
                event = "REFUND" if line.kind == "refund" else "PAYMENT"
                yield [
                    settlement.settlement_id,
                    day,
                    settlement.utr,
                    rupees(settlement.paid),
                    line.line_ref,
                    event,
                    attempt.attempt_id,
                    attempt.order_id,
                    line.ref if line.kind == "refund" else "",
                    rupees(line.gross),
                    rupees(line.fee),
                    rupees(line.tax),
                    rupees(line.net),
                    cashfree_group(attempt),
                    attempt.network or "",
                ]

    return {
        "cashfree/orders.jsonl": jsonl(
            {
                "cf_order_id": f"21{a.attempt_id[4:]}",
                "order_id": a.order_id,
                "entity": "order",
                "order_currency": "INR",
                "order_amount": Number(rupees(a.amount)),
                "order_status": "PAID" if a.truth == "succeeded" else "ACTIVE",
                "created_at": iso(a.created - timedelta(seconds=30)),
                "customer_details": {
                    "customer_id": f"shopper{a.shopper}",
                    "customer_email": email(a.shopper),
                    "customer_phone": phone(a.shopper)[3:],
                },
            }
            for a in attempts
        ),
        "cashfree/payments.jsonl": jsonl(cashfree_payment(a) for a in attempts),
        "cashfree/refunds.jsonl": jsonl(
            {
                "cf_refund_id": r.refund_id,
                "cf_payment_id": r.attempt.attempt_id,
                "order_id": r.attempt.order_id,
                "entity": "refund",
                "refund_amount": Number(rupees(r.amount)),
                "refund_currency": "INR",
                "refund_status": "SUCCESS",
                "refund_type": "MERCHANT_INITIATED",
                "created_at": iso(r.at),
            }
            for r in refunds
        ),
        "cashfree/settlements.csv": table(
            [
                "cf_settlement_id",
                "settlement_date",
                "settlement_utr",
                "settlement_amount",
                "event_id",
                "event_type",
                "cf_payment_id",
                "order_id",
                "cf_refund_id",
                "event_amount",
                "service_charge",
                "service_tax",
                "event_settlement_amount",
                "payment_group",
                "card_network",
            ],
            rows(),
        ),
    }


# --- Marketplace, bank, orders, contracts ---------------------------------------------


def marketplace(world: World) -> Files:
    def rows() -> Iterator[list[str]]:
        for payout in (s for s in world.settlements if s.provider == "marketplace"):
            day = world.cal.days[payout.day].isoformat()
            head = [payout.settlement_id, day, payout.utr]
            for line in ordered(payout):
                yield [
                    "LINE",
                    *head,
                    line.ref,
                    rupees(line.gross),
                    rupees(line.fee),
                    rupees(line.tax),
                    rupees(line.net),
                    "",
                ]
            yield [
                "PAYOUT",
                *head,
                "",
                rupees(payout.gross),
                rupees(payout.fee),
                rupees(payout.tax),
                "",
                rupees(payout.paid),
            ]

    header = [
        "record_type",
        "payout_id",
        "payout_date",
        "utr",
        "order_id",
        "gross_amount",
        "commission",
        "gst_on_commission",
        "net_amount",
        "payout_amount",
    ]
    return {"marketplace/payouts.csv": table(header, rows())}


def bank(world: World) -> Files:
    """One statement per calendar month, in HDFC's CSV layout, credits only."""
    files: Files = {}
    balance = 2_500_000 * 100
    months: dict[str, list[list[str]]] = {}
    for credit in world.credits:
        day = world.cal.days[credit.day]
        balance += credit.amount
        stamp = day.strftime("%d/%m/%y")
        months.setdefault(day.strftime("%Y-%m"), []).append(
            [
                stamp,
                credit.narration,
                credit.utr or "",
                stamp,
                "",
                rupees(credit.amount),
                rupees(balance),
            ]
        )
    header = [
        "Date",
        "Narration",
        "Chq./Ref.No.",
        "Value Dt",
        "Withdrawal Amt.",
        "Deposit Amt.",
        "Closing Balance",
    ]
    for month, rows in months.items():
        files[f"bank/hdfc_statement_{month}.csv"] = table(header, rows)
    return files


def orders(world: World) -> Files:
    return {
        "orders/bookings.csv": table(
            [
                "order_id",
                "placed_at",
                "amount",
                "currency",
                "status",
                "fulfilment_status",
                "channel",
            ],
            (
                [
                    o.order_id,
                    iso(o.placed),
                    rupees(o.amount),
                    "INR",
                    o.status,
                    o.fulfilment,
                    o.channel,
                ]
                for o in world.orders
            ),
        )
    }


def contracts(world: World) -> Files:
    start = world.cal.days[0].replace(month=1, day=1).isoformat()
    rows: list[dict[str, Any]] = []
    for provider, rates in c.CONTRACT_RATES.items():
        for key, rate in rates.items():
            rows.append(
                {
                    "provider": provider,
                    "method": "card" if key == "amex" else key,
                    "card_network": "amex" if key == "amex" else None,
                    "card_type": None,
                    "rate_bps": rate,
                    "fixed_fee_minor": 0,
                    "tax_rate_bps": c.TAX_BPS,
                    # False: on a refund, the tax on the fee comes back with the fee.
                    "tax_on_refunded_fee": False,
                    "allowed_fee_types": ["payment"],
                    "effective_from": start,
                    "effective_to": None,
                }
            )
    rows.append(
        {
            "provider": "marketplace",
            "method": "commission",
            "card_network": None,
            "card_type": None,
            "rate_bps": c.MARKETPLACE_COMMISSION_BPS,
            "fixed_fee_minor": 0,
            "tax_rate_bps": c.TAX_BPS,
            "tax_on_refunded_fee": False,
            "allowed_fee_types": ["commission"],
            "effective_from": start,
            "effective_to": None,
        }
    )
    return {"contracts/fee_contracts.json": json.dumps(rows, indent=2) + "\n"}


def by_time(attempt: Attempt) -> tuple[datetime, str]:
    return attempt.created, attempt.attempt_id


def ordered(settlement: Settlement) -> list[Line]:
    return sorted(settlement.lines, key=lambda line: (line.kind, line.ref))


def render(world: World) -> Iterator[tuple[str, str]]:
    """Every file as (relative path, contents), one provider at a time."""
    for part in (razorpay, payu, cashfree, marketplace, bank, orders, contracts):
        yield from sorted(part(world).items())
