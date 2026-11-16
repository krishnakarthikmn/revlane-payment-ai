"""Razorpay: payments, refunds, settlements, settlement recon and webhooks.

Amounts are already in paise. `fee` on a payment and `fees` on a settlement
include tax. Shapes follow Razorpay's API; the choices made where its
documentation is unclear are in `revlane/synth/ASSUMPTIONS.md`.
"""

import hashlib
import hmac
import json
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from revlane.connectors import statuses
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
    from_epoch,
    paise,
    required,
    text,
    upi_app,
)
from revlane.connectors.live import LiveAccess, basic_auth
from revlane.connectors.sanitize import normalise_contact, scrub
from revlane.vault.base import CredentialRef

PROVIDER = "razorpay"
API = "https://api.razorpay.com/v1"
PAGE = 100
AUTO_REFUND_NOTE = "payment_not_captured_auto_refund"
LINE_TYPES = {"payment": "capture", "refund": "refund", "adjustment": "fee_adjustment"}


def payment(
    payload: Payload, source: str, reported_at: datetime | None = None
) -> list[CanonicalRecord]:
    attempt_id = str(required(payload, "id"))
    status = str(required(payload, "status"))
    created = from_epoch(required(payload, "created_at"))
    card = payload.get("card") or {}
    notes = payload.get("notes") or {}
    raw = scrub(payload)
    stage = None
    if status == "refunded":
        stage = statuses.CAPTURED if payload.get("captured") else statuses.NOT_CAPTURED
    return [
        AttemptRecord(
            provider=PROVIDER,
            attempt_id=attempt_id,
            merchant_order_id=text(notes.get("booking_id")) if isinstance(notes, dict) else None,
            amount_minor=paise(required(payload, "amount")),
            currency=str(payload.get("currency") or "INR"),
            method=text(payload.get("method")),
            instrument_bank=bank(payload.get("bank") or card.get("issuer")),
            card_network=card_network(card.get("network")),
            card_type=text(card.get("type")),
            upi_app=upi_app(payload.get("vpa")),
            first_seen_at=created,
            raw=raw,
            shopper_contact=normalise_contact(payload.get("contact"), payload.get("email")),
        ),
        EventRecord(
            provider=PROVIDER,
            attempt_id=attempt_id,
            provider_status=status,
            provider_stage=stage,
            provider_error_code=text(payload.get("error_reason") or payload.get("error_code")),
            # A snapshot carries no time for its status; the payment's own time stands in.
            occurred_at=reported_at or created,
            source=source,
            raw=raw,
        ),
    ]


def webhook(payload: Payload) -> list[CanonicalRecord]:
    if not str(payload.get("event", "")).startswith("payment."):
        return []
    try:
        entity = payload["payload"]["payment"]["entity"]
    except (KeyError, TypeError) as error:
        raise MappingError("Webhook has no payment entity") from error
    return payment(entity, "webhook", from_epoch(required(payload, "created_at")))


def refund(payload: Payload) -> list[CanonicalRecord]:
    notes = payload.get("notes") or {}
    automatic = isinstance(notes, dict) and notes.get("reason") == AUTO_REFUND_NOTE
    return [
        RefundRecord(
            provider=PROVIDER,
            attempt_id=str(required(payload, "payment_id")),
            refund_id=str(required(payload, "id")),
            type="automatic" if automatic else "merchant",
            amount_minor=paise(required(payload, "amount")),
            status=str(required(payload, "status")),
            occurred_at=from_epoch(required(payload, "created_at")),
            raw=scrub(payload),
        )
    ]


def settlement(payload: Payload) -> list[CanonicalRecord]:
    net = paise(required(payload, "amount"))
    fees = paise(payload.get("fees") or 0)  # includes tax
    tax = paise(payload.get("tax") or 0)
    return [
        SettlementRecord(
            provider=PROVIDER,
            settlement_id=str(required(payload, "id")),
            net_minor=net,
            utr=text(payload.get("utr")),
            settled_on=from_epoch(required(payload, "created_at")).astimezone(IST).date(),
            raw=scrub(payload),
            gross_minor=net + fees,
            fee_minor=fees - tax,
            tax_minor=tax,
        )
    ]


def settlement_line(payload: Payload) -> list[CanonicalRecord]:
    kind = str(required(payload, "type"))
    if kind not in LINE_TYPES:
        raise MappingError(f"Unknown settlement line type {kind!r}")
    amount = paise(payload.get("amount") or 0)
    is_money_movement = kind in ("payment", "refund")
    return [
        SettlementLineRecord(
            provider=PROVIDER,
            settlement_id=str(required(payload, "settlement_id")),
            line_ref=str(required(payload, "entity_id")),
            attempt_id=text(payload.get("payment_id")),
            line_type=LINE_TYPES[kind],
            gross_minor=-amount if kind == "refund" else amount,
            fee_minor=paise(payload.get("fee") or 0),
            tax_minor=paise(payload.get("tax") or 0),
            net_minor=paise(payload.get("credit") or 0) - paise(payload.get("debit") or 0),
            fee_type=text(payload.get("fee_type")) or ("payment" if is_money_movement else None),
            raw=scrub(payload),
        )
    ]


class RazorpayConnector(BaseConnector):
    provider = PROVIDER

    def __init__(self, live: LiveAccess | None = None) -> None:
        self.live = live

    def classify(self, payload: Payload) -> str | None:
        entity = payload.get("entity")
        if entity in ("payment", "refund", "settlement"):
            return str(entity)
        if entity == "event":
            return "webhook"
        if "entity_id" in payload and "settlement_id" in payload:
            return "settlement_line"
        return None

    def map(self, raw: RawRecord) -> list[CanonicalRecord]:
        if raw.kind == "payment":
            return payment(raw.payload, raw.source)
        if raw.kind == "webhook":
            return webhook(raw.payload)
        if raw.kind == "refund":
            return refund(raw.payload)
        if raw.kind == "settlement":
            return settlement(raw.payload)
        if raw.kind == "settlement_line":
            return settlement_line(raw.payload)
        raise MappingError(f"Razorpay has no record kind {raw.kind!r}")

    # --- Live sandbox calls: test-mode keys only, off unless PROVIDER_LIVE_CALLS ---

    def _live(self) -> LiveAccess:
        if self.live is None:
            raise RuntimeError("This connector was built without live access.")
        self.live.require_enabled()
        return self.live

    def _headers(self) -> dict[str, str]:
        credentials = self._live().credentials()
        key_id = credentials.get("key_id", "")
        if not key_id.startswith("rzp_test_"):
            # Rule 10: never a production API.
            raise PermissionError("Only Razorpay test-mode keys (rzp_test_...) may be used.")
        return {"Authorization": basic_auth(key_id, credentials.get("key_secret", ""))}

    def authorize(self, tenant_id: UUID) -> CredentialRef:
        live = self._live()
        self._headers()
        return live.store(tenant_id, PROVIDER)

    def _pages(self, path: str, kind: str, params: dict[str, str]) -> Iterator[RawRecord]:
        live, headers, skip = self._live(), self._headers(), 0
        while True:
            page = live.transport.request(
                "GET",
                f"{API}/{path}",
                headers=headers,
                params={**params, "count": str(PAGE), "skip": str(skip)},
            )
            items = page.get("items", []) if isinstance(page, dict) else []
            for item in items:
                yield RawRecord(PROVIDER, kind, item, "api")
            if len(items) < PAGE:
                return
            skip += PAGE

    def backfill(self, since: date) -> Iterator[RawRecord]:
        start = datetime(since.year, since.month, since.day, tzinfo=IST)
        window = {"from": str(int(start.timestamp()))}
        yield from self._pages("payments", "payment", window)
        yield from self._pages("refunds", "refund", window)
        yield from self._pages("settlements", "settlement", window)
        today = datetime.now(tz=IST).date()
        year, month = since.year, since.month
        while (year, month) <= (today.year, today.month):
            yield from self._pages(
                "settlements/recon/combined",
                "settlement_line",
                {"year": str(year), "month": str(month)},
            )
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    def poll(self, cursor: str | None) -> Iterator[RawRecord]:
        """Payments created since the cursor, which is epoch seconds."""
        params = {"from": cursor} if cursor else {}
        yield from self._pages("payments", "payment", params)

    def handle_webhook(self, payload: bytes, signature: str) -> list[RawRecord]:
        secret = self._live_secret()
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise PermissionError("Webhook signature does not match.")
        event = json.loads(payload, parse_float=Decimal)
        if not isinstance(event, dict):
            raise MappingError("Webhook body is not an object")
        return [RawRecord(PROVIDER, "webhook", event, "webhook")]

    def _live_secret(self) -> str:
        if self.live is None:
            raise RuntimeError("This connector was built without live access.")
        secret = self.live.credentials().get("webhook_secret", "")
        if not secret:
            raise PermissionError("No webhook secret is stored for Razorpay.")
        return secret
