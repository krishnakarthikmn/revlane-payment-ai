"""Sources that only ever arrive as files: marketplace payouts, the bank
statement and the merchant's own order export."""

import hashlib
from datetime import datetime

from revlane.connectors.base import (
    BankCreditRecord,
    BaseConnector,
    CanonicalRecord,
    MappingError,
    OrderRecord,
    Payload,
    RawRecord,
    SettlementLineRecord,
    SettlementRecord,
    iso_date,
    iso_time,
    minor,
    required,
    text,
)
from revlane.connectors.sanitize import scrub

BANK_COLUMNS = (
    "Date",
    "Narration",
    "Chq./Ref.No.",
    "Value Dt",
    "Withdrawal Amt.",
    "Deposit Amt.",
    "Closing Balance",
)


class MarketplaceConnector(BaseConnector):
    """A payout file: one `LINE` row per order, one `PAYOUT` row per payout
    carrying the amount actually paid."""

    provider = "marketplace"

    def classify(self, payload: Payload) -> str | None:
        kind = payload.get("record_type")
        if "payout_id" not in payload:
            return None
        return {"LINE": "payout_line", "PAYOUT": "payout"}.get(str(kind))

    def map(self, raw: RawRecord) -> list[CanonicalRecord]:
        payload = raw.payload
        payout_id = str(required(payload, "payout_id"))
        if raw.kind == "payout":
            return [
                SettlementRecord(
                    provider=self.provider,
                    settlement_id=payout_id,
                    # What was paid. It can be less than the lines add up to.
                    net_minor=minor(required(payload, "payout_amount")),
                    utr=text(payload.get("utr")),
                    settled_on=iso_date(str(required(payload, "payout_date"))),
                    raw=scrub(payload),
                    gross_minor=minor(required(payload, "gross_amount")),
                    fee_minor=minor(required(payload, "commission")),
                    tax_minor=minor(required(payload, "gst_on_commission")),
                )
            ]
        if raw.kind == "payout_line":
            return [
                SettlementLineRecord(
                    provider=self.provider,
                    settlement_id=payout_id,
                    # The marketplace pays per order, so the order id is the line.
                    line_ref=str(required(payload, "order_id")),
                    attempt_id=None,
                    line_type="capture",
                    gross_minor=minor(required(payload, "gross_amount")),
                    fee_minor=minor(required(payload, "commission")),
                    tax_minor=minor(required(payload, "gst_on_commission")),
                    net_minor=minor(required(payload, "net_amount")),
                    fee_type="commission",
                    raw=scrub(payload),
                )
            ]
        raise MappingError(f"The marketplace has no record kind {raw.kind!r}")


class BankConnector(BaseConnector):
    """A bank statement in HDFC's CSV layout. Only credits are kept."""

    provider = "bank"

    def classify(self, payload: Payload) -> str | None:
        return "statement_line" if "Narration" in payload and "Deposit Amt." in payload else None

    def map(self, raw: RawRecord) -> list[CanonicalRecord]:
        if raw.kind != "statement_line":
            raise MappingError(f"The bank has no record kind {raw.kind!r}")
        payload = raw.payload
        deposit = text(payload.get("Deposit Amt."))
        if deposit is None:
            return []  # a withdrawal
        try:
            credited_on = datetime.strptime(str(required(payload, "Date")), "%d/%m/%y").date()
        except ValueError as error:
            raise MappingError(f"Not a statement date: {payload.get('Date')!r}") from error
        # The closing balance is part of the line, so two equal credits on one
        # day are still two lines.
        line = "|".join(str(payload.get(column) or "") for column in BANK_COLUMNS)
        narration = text(payload.get("Narration"))
        return [
            BankCreditRecord(
                utr=text(payload.get("Chq./Ref.No.")),
                amount_minor=minor(deposit),
                credited_on=credited_on,
                narration=scrub(narration) if narration else None,
                line_hash=hashlib.sha256(line.encode()).hexdigest(),
                raw=scrub(payload),
            )
        ]


class OrdersConnector(BaseConnector):
    """The merchant's own export of bookings."""

    provider = "orders"

    def classify(self, payload: Payload) -> str | None:
        return "order" if "order_id" in payload and "placed_at" in payload else None

    def map(self, raw: RawRecord) -> list[CanonicalRecord]:
        if raw.kind != "order":
            raise MappingError(f"The order export has no record kind {raw.kind!r}")
        payload = raw.payload
        return [
            OrderRecord(
                merchant_order_id=str(required(payload, "order_id")),
                amount_minor=minor(required(payload, "amount")),
                currency=str(payload.get("currency") or "INR"),
                status=str(required(payload, "status")),
                fulfilment_status=text(payload.get("fulfilment_status")),
                channel=text(payload.get("channel")) or "web",
                placed_at=iso_time(str(required(payload, "placed_at"))),
                raw=scrub(payload),
            )
        ]
