"""Contract tests for the mappers: one raw record in each provider's shape,
and exactly the canonical records it must become."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from revlane.connectors import base as c
from revlane.connectors.registry import (
    PROVIDERS,
    UnknownFile,
    connector_for,
    parse_json_line,
    read_records,
)
from revlane.connectors.sanitize import (
    normalise_contact,
    scrub,
    scrub_text,
    shopper_token,
    tenant_token_key,
)

IST = timezone(timedelta(hours=5, minutes=30))


def mapped(provider: str, payload: dict[str, Any], source: str = "file") -> list[Any]:
    connector = connector_for(provider)
    kind = connector.classify(payload)
    assert kind is not None, "the connector does not recognise this record"
    return connector.map(c.RawRecord(provider, kind, payload, source))


def only[T](records: list[Any], kind: type[T]) -> T:
    found = [record for record in records if isinstance(record, kind)]
    assert len(found) == 1
    return found[0]


# --- Razorpay ------------------------------------------------------------------

RAZORPAY_PAYMENT: dict[str, Any] = {
    "id": "pay_A1",
    "entity": "payment",
    "amount": 1_050_000,
    "currency": "INR",
    "status": "captured",
    "order_id": "order_X1",
    "method": "card",
    "amount_refunded": 0,
    "captured": True,
    "description": "Acme Travel booking BK-1",
    "bank": None,
    "vpa": None,
    "email": "shopper7@example.com",
    "contact": "+919800000007",
    "notes": {"booking_id": "BK-1"},
    "fee": 23_789,
    "tax": 3_629,
    "error_code": None,
    "error_reason": None,
    "created_at": 1_794_800_000,
    "card": {
        "id": "card_A1",
        "name": "SHOPPER 7",
        "last4": "0007",
        "network": "American Express",
        "type": "credit",
        "issuer": "ICIC",
    },
}


def test_razorpay_payment() -> None:
    records = mapped("razorpay", RAZORPAY_PAYMENT)
    attempt, event = only(records, c.AttemptRecord), only(records, c.EventRecord)
    created = datetime.fromtimestamp(1_794_800_000, tz=UTC)

    assert (attempt.provider, attempt.attempt_id) == ("razorpay", "pay_A1")
    assert attempt.merchant_order_id == "BK-1"
    assert attempt.amount_minor == 1_050_000
    assert (attempt.method, attempt.card_network, attempt.card_type) == ("card", "amex", "credit")
    assert attempt.instrument_bank == "ICICI"
    assert attempt.first_seen_at == created
    assert attempt.shopper_contact == "phone:9800000007"
    assert (event.provider_status, event.provider_stage, event.occurred_at) == (
        "captured",
        None,
        created,
    )
    assert event.source == "file"


def test_razorpay_failed_upi_payment() -> None:
    payload = RAZORPAY_PAYMENT | {
        "status": "failed",
        "method": "upi",
        "captured": False,
        "bank": "HDFC",
        "vpa": "shopper7@ybl",
        "card": None,
        "error_code": "BAD_REQUEST_ERROR",
        "error_reason": "payment_declined",
    }
    records = mapped("razorpay", payload)

    assert only(records, c.AttemptRecord).upi_app == "phonepe"
    assert only(records, c.AttemptRecord).instrument_bank == "HDFC"
    assert only(records, c.EventRecord).provider_error_code == "payment_declined"


@pytest.mark.parametrize(("captured", "stage"), [(True, "captured"), (False, "not_captured")])
def test_razorpay_refunded_payment_says_whether_it_was_ever_captured(
    captured: bool, stage: str
) -> None:
    records = mapped("razorpay", RAZORPAY_PAYMENT | {"status": "refunded", "captured": captured})

    assert only(records, c.EventRecord).provider_stage == stage


def test_razorpay_webhook_is_an_event_at_the_time_it_was_sent() -> None:
    event = {
        "entity": "event",
        "event": "payment.failed",
        "payload": {"payment": {"entity": RAZORPAY_PAYMENT | {"status": "failed"}}},
        "created_at": 1_794_800_004,
    }
    records = mapped("razorpay", event)

    assert only(records, c.EventRecord).source == "webhook"
    assert only(records, c.EventRecord).occurred_at == datetime.fromtimestamp(1_794_800_004, tz=UTC)
    assert mapped("razorpay", event | {"event": "order.paid"}) == []


def test_razorpay_refund_settlement_and_lines() -> None:
    refund = only(
        mapped(
            "razorpay",
            {
                "id": "rfnd_1",
                "entity": "refund",
                "amount": 1_050_000,
                "payment_id": "pay_A1",
                "notes": {"reason": "payment_not_captured_auto_refund"},
                "status": "processed",
                "created_at": 1_794_800_000,
            },
        ),
        c.RefundRecord,
    )
    assert (refund.type, refund.amount_minor, refund.attempt_id) == (
        "automatic",
        1_050_000,
        "pay_A1",
    )

    settlement = only(
        mapped(
            "razorpay",
            {
                "id": "setl_1",
                "entity": "settlement",
                "amount": 1_026_211,
                "fees": 23_789,
                "tax": 3_629,
                "utr": "UTR1",
                # 19:30 UTC on the 9th is 01:00 on the 10th in India.
                "created_at": int(datetime(2026, 11, 9, 19, 30, tzinfo=UTC).timestamp()),
            },
        ),
        c.SettlementRecord,
    )
    assert (settlement.gross_minor, settlement.fee_minor, settlement.tax_minor) == (
        1_050_000,
        20_160,
        3_629,
    )
    assert (settlement.net_minor, settlement.utr) == (1_026_211, "UTR1")
    assert settlement.settled_on == date(2026, 11, 10)

    line: dict[str, Any] = {
        "entity_id": "pay_A1",
        "type": "payment",
        "debit": 0,
        "credit": 1_026_211,
        "amount": 1_050_000,
        "fee": 20_160,
        "tax": 3_629,
        "fee_type": None,
        "settlement_id": "setl_1",
        "payment_id": "pay_A1",
    }
    capture = only(mapped("razorpay", line), c.SettlementLineRecord)
    assert (capture.line_type, capture.line_ref, capture.fee_type) == (
        "capture",
        "pay_A1",
        "payment",
    )
    assert (capture.gross_minor, capture.fee_minor, capture.tax_minor, capture.net_minor) == (
        1_050_000,
        20_160,
        3_629,
        1_026_211,
    )

    returned = only(
        mapped(
            "razorpay",
            line
            | {
                "entity_id": "rfnd_1",
                "type": "refund",
                "debit": 1_026_211,
                "credit": 0,
                "fee": -20_160,
                "tax": -3_629,
            },
        ),
        c.SettlementLineRecord,
    )
    assert (returned.line_type, returned.gross_minor, returned.net_minor) == (
        "refund",
        -1_050_000,
        -1_026_211,
    )

    extra = only(
        mapped(
            "razorpay",
            line
            | {
                "entity_id": "adj_1",
                "type": "adjustment",
                "debit": 3_540,
                "credit": 0,
                "amount": 0,
                "fee": 3_000,
                "tax": 540,
                "fee_type": "instant_settlement",
                "payment_id": None,
            },
        ),
        c.SettlementLineRecord,
    )
    assert (extra.line_type, extra.fee_type, extra.net_minor, extra.attempt_id) == (
        "fee_adjustment",
        "instant_settlement",
        -3_540,
        None,
    )


# --- PayU ----------------------------------------------------------------------

PAYU_TRANSACTION: dict[str, Any] = {
    "mihpayid": "403993715500000001",
    "mode": "NB",
    "status": "pending",
    "unmappedstatus": "pending",
    "txnid": "BK-2-1",
    "amount": "8450.50",
    "addedon": "2026-11-01 10:15:00",
    "updated_on": "2026-11-01 10:15:00",
    "firstname": "Shopper9",
    "email": "shopper9@example.com",
    "phone": "9800000009",
    "udf1": "BK-2",
    "issuing_bank": "SBI",
    "error": "E000",
    "error_Message": "No Error",
}


def test_payu_transaction() -> None:
    records = mapped("payu", PAYU_TRANSACTION)
    attempt, event = only(records, c.AttemptRecord), only(records, c.EventRecord)

    assert (attempt.attempt_id, attempt.merchant_order_id) == ("403993715500000001", "BK-2")
    assert attempt.amount_minor == 845_050
    assert (attempt.method, attempt.instrument_bank, attempt.card_type) == (
        "netbanking",
        "SBI",
        None,
    )
    assert attempt.first_seen_at == datetime(2026, 11, 1, 10, 15, tzinfo=IST)
    assert attempt.shopper_contact == "phone:9800000009"
    assert (event.provider_status, event.provider_stage, event.provider_error_code) == (
        "pending",
        "pending",
        None,
    )


def test_payu_second_report_is_a_second_event_for_the_same_attempt() -> None:
    later = PAYU_TRANSACTION | {
        "status": "failure",
        "unmappedstatus": "failed",
        "updated_on": "2026-11-03 12:00:00",
        "error": "E303",
    }
    first = only(mapped("payu", PAYU_TRANSACTION), c.EventRecord)
    second = only(mapped("payu", later), c.EventRecord)

    assert first.attempt_id == second.attempt_id
    assert first.event_key != second.event_key
    assert (second.provider_status, second.provider_error_code) == ("failed", "E303")
    assert second.occurred_at == datetime(2026, 11, 3, 12, 0, tzinfo=IST)
    # The same report read twice is the same event.
    assert first.event_key == only(mapped("payu", dict(PAYU_TRANSACTION)), c.EventRecord).event_key


def test_payu_card_transaction() -> None:
    card = PAYU_TRANSACTION | {"mode": "CC", "card_type": "MAST", "cardnum": "512345XXXXXX0009"}
    attempt = only(mapped("payu", card), c.AttemptRecord)

    assert (attempt.method, attempt.card_type, attempt.card_network) == (
        "card",
        "credit",
        "mastercard",
    )


def test_payu_refund_and_settlement_row() -> None:
    refund = only(
        mapped(
            "payu",
            {
                "request_id": "R1",
                "mihpayid": "4039",
                "amt": "8450.50",
                "status": "success",
                "refund_type": "merchant",
                "addedon": "2026-11-02 09:00:00",
            },
        ),
        c.RefundRecord,
    )
    assert (refund.type, refund.amount_minor, refund.refund_id) == ("merchant", 845_050, "R1")

    records = mapped(
        "payu",
        {
            "settlement_id": "PS1",
            "settlement_date": "2026-11-02",
            "utr": "UTR2",
            "settlement_amount": "8264.57",
            "line_id": "P:4039",
            "record_type": "payment",
            "mihpayid": "4039",
            "txnid": "BK-2-1",
            "request_id": "",
            "amount": "8450.50",
            "mdr": "157.57",
            "gst": "28.36",
            "net_amount": "8264.57",
        },
    )
    settlement, line = only(records, c.SettlementRecord), only(records, c.SettlementLineRecord)
    assert (settlement.settlement_id, settlement.net_minor, settlement.utr) == (
        "PS1",
        826_457,
        "UTR2",
    )
    assert settlement.settled_on == date(2026, 11, 2)
    assert settlement.gross_minor is None  # summed from the lines
    assert (line.line_ref, line.line_type, line.attempt_id) == ("P:4039", "capture", "4039")
    assert (line.gross_minor, line.fee_minor, line.tax_minor, line.net_minor) == (
        845_050,
        15_757,
        2_836,
        826_457,
    )


# --- Cashfree ------------------------------------------------------------------

CASHFREE_PAYMENT = parse_json_line(
    '{"cf_payment_id":"5114910000001","order_id":"BK-3","entity":"payment",'
    '"payment_currency":"INR","payment_amount":12999.90,"payment_status":"FAILED",'
    '"payment_time":"2026-11-05T18:30:00+05:30","payment_group":"debit_card",'
    '"payment_method":{"card":{"channel":"link","card_number":"XXXXXXXXXXXX0003",'
    '"card_network":"rupay","card_type":"debit_card","card_bank_name":"KOTAK"}},'
    '"error_details":{"error_code":"insufficient_funds","error_source":"bank"}}'
)


def test_cashfree_payment_amount_never_passes_through_a_float() -> None:
    assert isinstance(CASHFREE_PAYMENT["payment_amount"], Decimal)

    records = mapped("cashfree", CASHFREE_PAYMENT)
    attempt, event = only(records, c.AttemptRecord), only(records, c.EventRecord)

    assert attempt.amount_minor == 1_299_990
    assert (attempt.method, attempt.card_type, attempt.card_network, attempt.instrument_bank) == (
        "card",
        "debit",
        "rupay",
        "KOTAK",
    )
    assert attempt.merchant_order_id == "BK-3"
    assert attempt.shopper_contact is None
    assert (event.provider_status, event.provider_error_code) == ("FAILED", "insufficient_funds")
    assert event.occurred_at == datetime(2026, 11, 5, 18, 30, tzinfo=IST)

    with pytest.raises(c.MappingError, match="exact"):
        mapped("cashfree", CASHFREE_PAYMENT | {"payment_amount": 12999.9})


def test_cashfree_order_ties_the_shopper_to_the_order() -> None:
    records = mapped(
        "cashfree",
        {
            "order_id": "BK-3",
            "entity": "order",
            "customer_details": {
                "customer_email": "Shopper3@Example.com",
                "customer_phone": "9800000003",
            },
        },
    )

    assert records == [c.ShopperRecord("cashfree", "BK-3", "phone:9800000003")]


def test_cashfree_refund_and_settlement_row() -> None:
    refund = only(
        mapped(
            "cashfree",
            parse_json_line(
                '{"cf_refund_id":"RF1","cf_payment_id":"51149","entity":"refund",'
                '"refund_amount":500.00,"refund_status":"SUCCESS",'
                '"refund_type":"MERCHANT_INITIATED","created_at":"2026-11-06T10:00:00+05:30"}'
            ),
        ),
        c.RefundRecord,
    )
    assert (refund.type, refund.amount_minor) == ("merchant", 50_000)

    records = mapped(
        "cashfree",
        {
            "cf_settlement_id": "CS1",
            "settlement_date": "2026-11-07",
            "settlement_utr": "UTR3",
            "settlement_amount": "-10.00",
            "event_id": "REFUND:RF1",
            "event_type": "REFUND",
            "cf_payment_id": "51149",
            "order_id": "BK-3",
            "cf_refund_id": "RF1",
            "event_amount": "-500.00",
            "service_charge": "-9.85",
            "service_tax": "-1.77",
            "event_settlement_amount": "-488.38",
        },
    )
    line = only(records, c.SettlementLineRecord)
    assert (line.line_type, line.line_ref) == ("refund", "REFUND:RF1")
    assert (line.gross_minor, line.fee_minor, line.tax_minor, line.net_minor) == (
        -50_000,
        -985,
        -177,
        -48_838,
    )
    assert only(records, c.SettlementRecord).net_minor == -1_000


# --- Marketplace, bank, orders ---------------------------------------------------


def test_marketplace_payout_and_line() -> None:
    row = {
        "record_type": "LINE",
        "payout_id": "MP1",
        "payout_date": "2026-11-08",
        "utr": "UTR4",
        "order_id": "BK-4",
        "gross_amount": "10000.00",
        "commission": "1200.00",
        "gst_on_commission": "216.00",
        "net_amount": "8584.00",
        "payout_amount": "",
    }
    line = only(mapped("marketplace", row), c.SettlementLineRecord)
    assert (line.settlement_id, line.line_ref, line.fee_type, line.net_minor) == (
        "MP1",
        "BK-4",
        "commission",
        858_400,
    )

    payout = only(
        mapped(
            "marketplace",
            row
            | {
                "record_type": "PAYOUT",
                "order_id": "",
                "net_amount": "",
                "payout_amount": "8234.00",
            },
        ),
        c.SettlementRecord,
    )
    # Paid less than the lines add up to: the mapper reports it and judges nothing.
    assert (payout.gross_minor, payout.fee_minor, payout.tax_minor, payout.net_minor) == (
        1_000_000,
        120_000,
        21_600,
        823_400,
    )


BANK_LINE = {
    "Date": "09/11/26",
    "Narration": "NEFT CR-YESB0000001-RAZORPAY SOFTWARE PVT LTD-ACME TRAVEL-UTR770803117609",
    "Chq./Ref.No.": "UTR770803117609",
    "Value Dt": "09/11/26",
    "Withdrawal Amt.": "",
    "Deposit Amt.": "18997021.76",
    "Closing Balance": "1530922158.88",
}


def test_bank_credit() -> None:
    credit = only(mapped("bank", BANK_LINE), c.BankCreditRecord)

    assert (credit.utr, credit.amount_minor, credit.credited_on) == (
        "UTR770803117609",
        1_899_702_176,
        date(2026, 11, 9),
    )
    assert credit.narration == BANK_LINE["Narration"]
    assert len(credit.line_hash) == 64


def test_bank_line_hash_tells_equal_credits_apart_and_blank_reference_is_none() -> None:
    first = only(mapped("bank", BANK_LINE | {"Chq./Ref.No.": ""}), c.BankCreditRecord)
    second = only(
        mapped("bank", BANK_LINE | {"Chq./Ref.No.": "", "Closing Balance": "1549919180.64"}),
        c.BankCreditRecord,
    )

    assert first.utr is None
    assert first.line_hash != second.line_hash
    assert (
        first.line_hash
        == only(mapped("bank", BANK_LINE | {"Chq./Ref.No.": ""}), c.BankCreditRecord).line_hash
    )


def test_bank_withdrawal_is_not_a_credit() -> None:
    assert mapped("bank", BANK_LINE | {"Deposit Amt.": "", "Withdrawal Amt.": "500.00"}) == []


def test_order() -> None:
    order = only(
        mapped(
            "orders",
            {
                "order_id": "BK-5",
                "placed_at": "2026-11-09T08:00:00+05:30",
                "amount": "10500.00",
                "currency": "INR",
                "status": "confirmed",
                "fulfilment_status": "ticketed",
                "channel": "app",
            },
        ),
        c.OrderRecord,
    )

    assert (order.merchant_order_id, order.amount_minor, order.status, order.channel) == (
        "BK-5",
        1_050_000,
        "confirmed",
        "app",
    )
    assert order.placed_at == datetime(2026, 11, 9, 8, 0, tzinfo=IST)


# --- Bad records ---------------------------------------------------------------


def test_a_record_missing_a_field_is_refused_not_guessed() -> None:
    broken = {key: value for key, value in PAYU_TRANSACTION.items() if key != "amount"}

    with pytest.raises(c.MappingError, match="amount"):
        mapped("payu", broken)
    with pytest.raises(c.MappingError, match="paisa"):
        mapped("payu", PAYU_TRANSACTION | {"amount": "10.005"})
    with pytest.raises(c.MappingError, match="timestamp"):
        mapped("payu", PAYU_TRANSACTION | {"addedon": "yesterday"})


def test_reading_a_file_keeps_going_past_a_bad_line() -> None:
    lines = ['{"entity":"payment","id":"pay_1"}\n', "not json\n", '{"entity":"refund"}\n', "\n"]
    records = list(read_records(connector_for("razorpay"), "payments.jsonl", lines))

    assert [type(record).__name__ for record in records] == [
        "RawRecord",
        "MappingError",
        "RawRecord",
    ]


def test_a_file_from_another_provider_is_refused() -> None:
    with pytest.raises(UnknownFile, match="razorpay"):
        list(read_records(connector_for("razorpay"), "x.jsonl", ['{"mihpayid":"1"}\n']))
    with pytest.raises(UnknownFile, match="only"):
        list(read_records(connector_for("bank"), "statement.pdf", []))


# --- The protocol --------------------------------------------------------------


@pytest.mark.parametrize("provider", PROVIDERS)
def test_no_connector_can_act_on_a_provider(provider: str) -> None:
    connector = connector_for(provider)

    assert connector.actions() == []
    with pytest.raises(c.NotSupported, match="Nothing is sent"):
        connector.execute(object())


@pytest.mark.parametrize("provider", ["cashfree", "marketplace", "bank", "orders"])
def test_file_sources_have_no_live_side(provider: str) -> None:
    connector = connector_for(provider)

    with pytest.raises(c.NotSupported):
        connector.authorize(uuid4())
    with pytest.raises(c.NotSupported):
        connector.backfill(date(2026, 11, 1))
    with pytest.raises(c.NotSupported):
        connector.handle_webhook(b"{}", "")


# --- Rule 7: card numbers and contact details are dropped ------------------------

SENSITIVE = (
    "shopper7@example.com",
    "+919800000007",
    "9800000009",
    "SHOPPER 7",
    "Shopper9",
    "0007",
    "512345XXXXXX0009",
    "XXXXXXXXXXXX0003",
    "shopper7@ybl",
)


@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        ("razorpay", RAZORPAY_PAYMENT | {"vpa": "shopper7@ybl"}),
        ("payu", PAYU_TRANSACTION | {"cardnum": "512345XXXXXX0009", "upi_va": "shopper7@ybl"}),
        ("cashfree", CASHFREE_PAYMENT),
    ],
)
def test_no_canonical_record_keeps_a_card_number_or_contact(
    provider: str, payload: dict[str, Any]
) -> None:
    for record in mapped(provider, payload):
        kept = repr(record)  # shopper_contact is left out of the repr on purpose
        for secret in SENSITIVE:
            assert secret not in kept


def test_scrub_removes_what_the_field_names_miss() -> None:
    assert scrub({"note": "call +91 9800000007 or a.b@example.com"}) == {
        "note": "call [removed] or [removed]"
    }
    assert scrub_text("card 4111111111111111 used") == "card [removed] used"
    assert scrub_text("card 411111XXXXXX1111 used") == "card [removed] used"
    assert scrub_text("pay to shopper7@okhdfcbank now") == "pay to [removed] now"


def test_scrub_leaves_references_and_amounts_alone() -> None:
    narration = "NEFT CR-YESB0000001-RAZORPAY SOFTWARE PVT LTD-ACME TRAVEL-UTR770803117609"

    assert scrub_text(narration) == narration
    assert scrub_text("IMPS 917234567890 ref 4111111111111112") == (
        "IMPS 917234567890 ref 4111111111111112"  # not a valid card number
    )
    assert scrub({"amount": Decimal("12.50"), "count": 3, "ok": True, "none": None}) == {
        "amount": Decimal("12.50"),
        "count": 3,
        "ok": True,
        "none": None,
    }


def test_scrub_does_not_change_the_payload_it_is_given() -> None:
    payload = {"email": "a@example.com", "card": {"last4": "1234", "network": "Visa"}}
    scrub(payload)

    assert payload == {"email": "a@example.com", "card": {"last4": "1234", "network": "Visa"}}
    assert scrub(payload) == {"card": {"network": "Visa"}}


def test_shopper_token_is_a_keyed_hash_that_differs_by_tenant() -> None:
    a, b = uuid4(), uuid4()
    contact = normalise_contact("+91 98000-00007", "x@example.com")
    assert contact == "phone:9800000007"
    assert normalise_contact("9800000007") == contact  # the same shopper at another gateway
    assert normalise_contact(None, " X@Example.com ") == "email:x@example.com"
    assert normalise_contact(None, None) is None

    token = shopper_token(tenant_token_key("root", a), contact)

    assert len(token) == 64
    assert "9800000007" not in token
    assert token == shopper_token(tenant_token_key("root", a), contact)
    assert token != shopper_token(tenant_token_key("root", b), contact)
    assert token != shopper_token(tenant_token_key("other-root", a), contact)
    with pytest.raises(RuntimeError, match="TOKEN_HASH_ROOT_KEY"):
        tenant_token_key("", a)
