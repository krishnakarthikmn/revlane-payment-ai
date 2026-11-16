"""PayU: transaction status reports, refunds and settlement lines.

Amounts are rupee strings. A transaction can be reported more than once; each
report is one event, ordered by `updated_on`. The canonical status comes from
`unmappedstatus`, never from the coarser `status`.
"""

import hashlib
import hmac
import json
import urllib.parse
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from revlane.connectors.base import (
    IST,
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
    bank,
    card_network,
    india_time,
    iso_date,
    minor,
    required,
    text,
    upi_app,
)
from revlane.connectors.live import LiveAccess
from revlane.connectors.sanitize import normalise_contact, scrub
from revlane.vault.base import CredentialRef

PROVIDER = "payu"
# The test host only. There is no setting that points this at production.
QUERY_API = "https://test.payu.in/merchant/postservice?form=2"
NO_ERROR = "E000"
# mode -> (method, card type)
MODES: dict[str, tuple[str, str | None]] = {
    "UPI": ("upi", None),
    "NB": ("netbanking", None),
    "CASH": ("wallet", None),
    "CC": ("card", "credit"),
    "DC": ("card", "debit"),
}
LINE_TYPES = {"payment": "capture", "refund": "refund"}
SETTLEMENT_FIELDS = ("settlement_id", "settlement_date", "utr", "settlement_amount")
# The query API names a few fields differently from a transaction report.
QUERY_FIELDS = {"id": "mihpayid", "card_no": "cardnum", "error_code": "error"}


def transaction(payload: Payload, source: str) -> list[CanonicalRecord]:
    attempt_id = str(required(payload, "mihpayid"))
    method, card_type = MODES.get(str(payload.get("mode") or "").upper(), (None, None))
    added = india_time(str(required(payload, "addedon")))
    error = text(payload.get("error"))
    raw = scrub(payload)
    return [
        AttemptRecord(
            provider=PROVIDER,
            attempt_id=attempt_id,
            merchant_order_id=text(payload.get("udf1")),
            amount_minor=minor(required(payload, "amount")),
            currency="INR",
            method=method,
            instrument_bank=bank(payload.get("issuing_bank")),
            card_network=card_network(payload.get("card_type")) if method == "card" else None,
            card_type=card_type,
            upi_app=upi_app(payload.get("upi_va")),
            first_seen_at=added,
            raw=raw,
            shopper_contact=normalise_contact(payload.get("phone"), payload.get("email")),
        ),
        EventRecord(
            provider=PROVIDER,
            attempt_id=attempt_id,
            provider_status=str(required(payload, "unmappedstatus")),
            provider_stage=text(payload.get("status")),
            provider_error_code=None if error in (None, NO_ERROR) else error,
            occurred_at=india_time(str(payload.get("updated_on") or payload["addedon"])),
            source=source,
            raw=raw,
        ),
    ]


def refund(payload: Payload) -> list[CanonicalRecord]:
    return [
        RefundRecord(
            provider=PROVIDER,
            attempt_id=str(required(payload, "mihpayid")),
            refund_id=str(required(payload, "request_id")),
            type="automatic" if payload.get("refund_type") == "auto" else "merchant",
            amount_minor=minor(required(payload, "amt")),
            status=str(required(payload, "status")),
            occurred_at=india_time(str(required(payload, "addedon"))),
            raw=scrub(payload),
        )
    ]


def settlement_line(payload: Payload) -> list[CanonicalRecord]:
    """One row of the settlement file: the settlement it belongs to, and the line."""
    kind = str(required(payload, "record_type"))
    if kind not in LINE_TYPES:
        raise MappingError(f"Unknown settlement record type {kind!r}")
    settlement_id = str(required(payload, "settlement_id"))
    return [
        SettlementRecord(
            provider=PROVIDER,
            settlement_id=settlement_id,
            net_minor=minor(required(payload, "settlement_amount")),
            utr=text(payload.get("utr")),
            settled_on=iso_date(str(required(payload, "settlement_date"))),
            raw={key: payload.get(key) for key in SETTLEMENT_FIELDS},
        ),
        SettlementLineRecord(
            provider=PROVIDER,
            settlement_id=settlement_id,
            line_ref=str(required(payload, "line_id")),
            attempt_id=text(payload.get("mihpayid")),
            line_type=LINE_TYPES[kind],
            gross_minor=minor(required(payload, "amount")),
            fee_minor=minor(required(payload, "mdr")),
            tax_minor=minor(required(payload, "gst")),
            net_minor=minor(required(payload, "net_amount")),
            fee_type="payment",
            raw=scrub(payload),
        ),
    ]


def response_hash(payload: Payload, key: str, salt: str) -> str:
    """PayU's reverse hash over a transaction response."""
    fields = [
        salt,
        payload.get("status", ""),
        *[""] * 5,
        *[payload.get(f"udf{n}", "") for n in (5, 4, 3, 2, 1)],
        payload.get("email", ""),
        payload.get("firstname", ""),
        payload.get("productinfo", ""),
        payload.get("amount", ""),
        payload.get("txnid", ""),
        key,
    ]
    return hashlib.sha512("|".join(str(field or "") for field in fields).encode()).hexdigest()


class PayUConnector(BaseConnector):
    provider = PROVIDER

    def __init__(self, live: LiveAccess | None = None) -> None:
        self.live = live

    def classify(self, payload: Payload) -> str | None:
        if "unmappedstatus" in payload:
            return "transaction"
        if "request_id" in payload and "amt" in payload:
            return "refund"
        if "line_id" in payload and "settlement_id" in payload:
            return "settlement_line"
        return None

    def map(self, raw: RawRecord) -> list[CanonicalRecord]:
        if raw.kind == "transaction":
            return transaction(raw.payload, raw.source)
        if raw.kind == "refund":
            return refund(raw.payload)
        if raw.kind == "settlement_line":
            return settlement_line(raw.payload)
        raise MappingError(f"PayU has no record kind {raw.kind!r}")

    # --- Live sandbox calls: test.payu.in only, off unless PROVIDER_LIVE_CALLS -----

    def _credentials(self) -> tuple[str, str]:
        if self.live is None:
            raise RuntimeError("This connector was built without live access.")
        credentials = self.live.credentials()
        key, salt = credentials.get("key", ""), credentials.get("salt", "")
        if not key or not salt:
            raise PermissionError("PayU needs a sandbox key and salt.")
        return key, salt

    def authorize(self, tenant_id: UUID) -> CredentialRef:
        if self.live is None:
            raise RuntimeError("This connector was built without live access.")
        self.live.require_enabled()
        self._credentials()
        return self.live.store(tenant_id, PROVIDER)

    def _transactions(self, start: date, end: date) -> Iterator[RawRecord]:
        assert self.live is not None
        key, salt = self._credentials()
        command, day = "get_Transaction_Details", start
        while day <= end:
            var1 = day.isoformat()
            digest = hashlib.sha512(f"{key}|{command}|{var1}|{salt}".encode()).hexdigest()
            reply = self.live.transport.request(
                "POST",
                QUERY_API,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                form={"key": key, "command": command, "var1": var1, "var2": var1, "hash": digest},
            )
            rows = reply.get("Transaction_details", []) if isinstance(reply, dict) else []
            for row in rows if isinstance(rows, list) else []:
                renamed = {QUERY_FIELDS.get(name, name): value for name, value in row.items()}
                yield RawRecord(PROVIDER, "transaction", renamed, "api")
            day += timedelta(days=1)

    def backfill(self, since: date) -> Iterator[RawRecord]:
        if self.live is None:
            raise RuntimeError("This connector was built without live access.")
        self.live.require_enabled()
        yield from self._transactions(since, datetime.now(tz=IST).date())

    def poll(self, cursor: str | None) -> Iterator[RawRecord]:
        """Transactions since the cursor, which is an ISO date."""
        today = datetime.now(tz=IST).date()
        yield from self.backfill(iso_date(cursor) if cursor else today)

    def handle_webhook(self, payload: bytes, signature: str) -> list[RawRecord]:
        key, salt = self._credentials()
        body = payload.decode("utf-8")
        report: Payload
        if body.lstrip().startswith("{"):
            parsed = json.loads(body, parse_float=Decimal)
            if not isinstance(parsed, dict):
                raise MappingError("Webhook body is not an object")
            report = parsed
        else:
            report = dict(urllib.parse.parse_qsl(body, keep_blank_values=True))
        given = signature or str(report.get("hash", ""))
        if not hmac.compare_digest(response_hash(report, key, salt), given):
            raise PermissionError("Webhook hash does not match.")
        return [RawRecord(PROVIDER, "transaction", report, "webhook")]
