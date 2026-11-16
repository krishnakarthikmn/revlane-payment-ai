"""Cashfree: orders, payments, refunds and settlement events. File-based.

Amounts are rupee decimals. Payment records carry no contact details; the
order record does, so it is what ties a shopper to an order's attempts.
"""

from revlane.connectors.base import (
    AttemptRecord,
    BaseConnector,
    CanonicalRecord,
    EventRecord,
    MappingError,
    Payload,
    RawRecord,
    RefundRecord,
    SettlementLineRecord,
    SettlementRecord,
    ShopperRecord,
    bank,
    card_network,
    iso_date,
    iso_time,
    minor,
    required,
    text,
    upi_app,
)
from revlane.connectors.sanitize import normalise_contact, scrub

PROVIDER = "cashfree"
# payment_group -> (method, card type)
GROUPS: dict[str, tuple[str, str | None]] = {
    "upi": ("upi", None),
    "net_banking": ("netbanking", None),
    "wallet": ("wallet", None),
    "credit_card": ("card", "credit"),
    "debit_card": ("card", "debit"),
}
LINE_TYPES = {"PAYMENT": "capture", "REFUND": "refund"}
SETTLEMENT_FIELDS = ("cf_settlement_id", "settlement_date", "settlement_utr", "settlement_amount")


def payment(payload: Payload, source: str) -> list[CanonicalRecord]:
    attempt_id = str(required(payload, "cf_payment_id"))
    at = iso_time(str(required(payload, "payment_time")))
    method, card_type = GROUPS.get(str(payload.get("payment_group") or ""), (None, None))
    instrument = payload.get("payment_method") or {}
    card = instrument.get("card") or {}
    netbanking = instrument.get("netbanking") or {}
    upi = instrument.get("upi") or {}
    error = payload.get("error_details") or {}
    raw = scrub(payload)
    return [
        AttemptRecord(
            provider=PROVIDER,
            attempt_id=attempt_id,
            merchant_order_id=text(payload.get("order_id")),
            amount_minor=minor(required(payload, "payment_amount")),
            currency=str(payload.get("payment_currency") or "INR"),
            method=method,
            instrument_bank=bank(
                card.get("card_bank_name") or netbanking.get("netbanking_bank_name")
            ),
            card_network=card_network(card.get("card_network")),
            card_type=card_type,
            upi_app=upi_app(upi.get("upi_id")),
            first_seen_at=at,
            raw=raw,
        ),
        EventRecord(
            provider=PROVIDER,
            attempt_id=attempt_id,
            provider_status=str(required(payload, "payment_status")),
            provider_stage=None,
            provider_error_code=text(error.get("error_code")),
            occurred_at=at,
            source=source,
            raw=raw,
        ),
    ]


def order(payload: Payload) -> list[CanonicalRecord]:
    customer = payload.get("customer_details") or {}
    contact = normalise_contact(customer.get("customer_phone"), customer.get("customer_email"))
    if contact is None:
        return []
    return [ShopperRecord(PROVIDER, str(required(payload, "order_id")), contact)]


def refund(payload: Payload) -> list[CanonicalRecord]:
    merchant = payload.get("refund_type") == "MERCHANT_INITIATED"
    return [
        RefundRecord(
            provider=PROVIDER,
            attempt_id=str(required(payload, "cf_payment_id")),
            refund_id=str(required(payload, "cf_refund_id")),
            type="merchant" if merchant else "automatic",
            amount_minor=minor(required(payload, "refund_amount")),
            status=str(required(payload, "refund_status")),
            occurred_at=iso_time(str(required(payload, "created_at"))),
            raw=scrub(payload),
        )
    ]


def settlement_line(payload: Payload) -> list[CanonicalRecord]:
    kind = str(required(payload, "event_type"))
    if kind not in LINE_TYPES:
        raise MappingError(f"Unknown settlement event type {kind!r}")
    settlement_id = str(required(payload, "cf_settlement_id"))
    return [
        SettlementRecord(
            provider=PROVIDER,
            settlement_id=settlement_id,
            net_minor=minor(required(payload, "settlement_amount")),
            utr=text(payload.get("settlement_utr")),
            settled_on=iso_date(str(required(payload, "settlement_date"))),
            raw={key: payload.get(key) for key in SETTLEMENT_FIELDS},
        ),
        SettlementLineRecord(
            provider=PROVIDER,
            settlement_id=settlement_id,
            line_ref=str(required(payload, "event_id")),
            attempt_id=text(payload.get("cf_payment_id")),
            line_type=LINE_TYPES[kind],
            gross_minor=minor(required(payload, "event_amount")),
            fee_minor=minor(required(payload, "service_charge")),
            tax_minor=minor(required(payload, "service_tax")),
            net_minor=minor(required(payload, "event_settlement_amount")),
            fee_type="payment",
            raw=scrub(payload),
        ),
    ]


class CashfreeConnector(BaseConnector):
    provider = PROVIDER

    def classify(self, payload: Payload) -> str | None:
        entity = payload.get("entity")
        if entity in ("payment", "order", "refund"):
            return str(entity)
        if "event_id" in payload and "cf_settlement_id" in payload:
            return "settlement_line"
        return None

    def map(self, raw: RawRecord) -> list[CanonicalRecord]:
        if raw.kind == "payment":
            return payment(raw.payload, raw.source)
        if raw.kind == "order":
            return order(raw.payload)
        if raw.kind == "refund":
            return refund(raw.payload)
        if raw.kind == "settlement_line":
            return settlement_line(raw.payload)
        raise MappingError(f"Cashfree has no record kind {raw.kind!r}")
