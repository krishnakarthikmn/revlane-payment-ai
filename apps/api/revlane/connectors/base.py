"""What every connector shares: the protocol, raw records and canonical records.

A mapper is a pure function: one raw provider record in, canonical records
out. Ingestion writes the canonical records; nothing else knows a provider's
field names.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from revlane.money import from_decimal_string
from revlane.vault.base import CredentialRef

IST = timezone(timedelta(hours=5, minutes=30))
Payload = dict[str, Any]


class MappingError(ValueError):
    """A raw record the mapper cannot turn into canonical records."""


class NotSupported(RuntimeError):
    """The connector does not offer this operation."""


@dataclass(frozen=True, slots=True)
class RawRecord:
    provider: str
    kind: str
    payload: Payload
    source: str = "file"  # api, webhook or file


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    provider: str
    attempt_id: str
    merchant_order_id: str | None
    amount_minor: int
    currency: str
    method: str | None
    instrument_bank: str | None
    card_network: str | None
    card_type: str | None
    upi_app: str | None
    first_seen_at: datetime
    raw: Payload
    # The shopper's normalised phone or email. Ingestion hashes it and throws
    # it away; it is never written anywhere.
    shopper_contact: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class EventRecord:
    provider: str
    attempt_id: str
    provider_status: str
    provider_stage: str | None
    provider_error_code: str | None
    occurred_at: datetime
    source: str
    raw: Payload

    @property
    def event_key(self) -> str:
        """The same report delivered twice has the same key."""
        return "|".join(
            (self.provider_status, self.provider_stage or "", self.occurred_at.isoformat())
        )


@dataclass(frozen=True, slots=True)
class RefundRecord:
    provider: str
    attempt_id: str
    refund_id: str
    type: str  # merchant or automatic
    amount_minor: int
    status: str
    occurred_at: datetime
    raw: Payload


@dataclass(frozen=True, slots=True)
class SettlementRecord:
    provider: str
    settlement_id: str
    net_minor: int
    utr: str | None
    settled_on: date
    raw: Payload
    # None when the provider's file gives no totals: they are summed from the lines.
    gross_minor: int | None = None
    fee_minor: int | None = None
    tax_minor: int | None = None


@dataclass(frozen=True, slots=True)
class SettlementLineRecord:
    provider: str
    settlement_id: str
    line_ref: str
    attempt_id: str | None
    line_type: str
    gross_minor: int
    fee_minor: int
    tax_minor: int
    net_minor: int
    fee_type: str | None
    raw: Payload


@dataclass(frozen=True, slots=True)
class BankCreditRecord:
    utr: str | None
    amount_minor: int
    credited_on: date
    narration: str | None
    line_hash: str
    raw: Payload


@dataclass(frozen=True, slots=True)
class OrderRecord:
    merchant_order_id: str
    amount_minor: int
    currency: str
    status: str
    fulfilment_status: str | None
    channel: str
    placed_at: datetime
    raw: Payload


@dataclass(frozen=True, slots=True)
class ShopperRecord:
    """Ties a shopper to the attempts of one order, for providers whose payment
    records carry no contact details."""

    provider: str
    merchant_order_id: str
    shopper_contact: str = field(repr=False)


CanonicalRecord = (
    AttemptRecord
    | EventRecord
    | RefundRecord
    | SettlementRecord
    | SettlementLineRecord
    | BankCreditRecord
    | OrderRecord
    | ShopperRecord
)


@dataclass(frozen=True, slots=True)
class ActionSpec:
    name: str


class Connector(Protocol):
    """docs/technical-design.md, section 5."""

    provider: str

    def authorize(self, tenant_id: UUID) -> CredentialRef: ...
    def backfill(self, since: date) -> Iterator[RawRecord]: ...
    def poll(self, cursor: str | None) -> Iterator[RawRecord]: ...
    def handle_webhook(self, payload: bytes, signature: str) -> list[RawRecord]: ...
    def classify(self, payload: Payload) -> str | None: ...
    def map(self, raw: RawRecord) -> list[CanonicalRecord]: ...
    def actions(self) -> list[ActionSpec]: ...
    def execute(self, action: object) -> object: ...


class BaseConnector:
    """Defaults for a source that is read from files only."""

    provider = ""

    def authorize(self, tenant_id: UUID) -> CredentialRef:
        raise NotSupported(f"{self.provider} is read from files; there is nothing to authorise.")

    def backfill(self, since: date) -> Iterator[RawRecord]:
        raise NotSupported(f"{self.provider} is read from files only.")

    def poll(self, cursor: str | None) -> Iterator[RawRecord]:
        raise NotSupported(f"{self.provider} is read from files only.")

    def handle_webhook(self, payload: bytes, signature: str) -> list[RawRecord]:
        raise NotSupported(f"{self.provider} sends no webhooks.")

    def classify(self, payload: Payload) -> str | None:
        raise NotImplementedError

    def map(self, raw: RawRecord) -> list[CanonicalRecord]:
        raise NotImplementedError

    def actions(self) -> list[ActionSpec]:
        # The first version reads; it never writes to a provider.
        return []

    def execute(self, action: object) -> object:
        raise NotSupported("Nothing is sent to a provider in the first version.")


# --- Helpers shared by the mappers ---------------------------------------------


def minor(value: object) -> int:
    """A provider's amount in major units (rupees) as paise. Never through a float."""
    if isinstance(value, bool | float):
        raise MappingError(f"Amount arrived as {type(value).__name__}; it must be exact")
    if isinstance(value, int | Decimal | str):
        try:
            return from_decimal_string(str(value))
        except ValueError as error:
            raise MappingError(str(error)) from error
    raise MappingError(f"Not an amount: {value!r}")


def paise(value: object) -> int:
    """An amount the provider already writes in paise."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise MappingError(f"Expected whole paise, got {value!r}")
    return value


def from_epoch(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MappingError(f"Expected epoch seconds, got {value!r}")
    return datetime.fromtimestamp(value, tz=timezone.utc)  # noqa: UP017


def india_time(value: str) -> datetime:
    """`YYYY-MM-DD HH:MM:SS`, written in India time with no offset."""
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    except ValueError as error:
        raise MappingError(f"Not a timestamp: {value!r}") from error


def iso_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise MappingError(f"Not a timestamp: {value!r}") from error
    if parsed.tzinfo is None:
        raise MappingError(f"Timestamp has no offset: {value!r}")
    return parsed


def iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise MappingError(f"Not a date: {value!r}") from error


def text(value: object) -> str | None:
    """A field as text, with empty strings and nulls as None."""
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


CARD_NETWORKS = {
    "visa": "visa",
    "mastercard": "mastercard",
    "mast": "mastercard",
    "rupay": "rupay",
    "american express": "amex",
    "amex": "amex",
    "diners club": "diners",
    "diners": "diners",
    "maestro": "maestro",
}

# Bank codes (the first four letters of an IFSC) to one name per bank.
BANKS = {
    "HDFC": "HDFC",
    "ICIC": "ICICI",
    "ICICI": "ICICI",
    "SBIN": "SBI",
    "SBI": "SBI",
    "UTIB": "AXIS",
    "AXIS": "AXIS",
    "KKBK": "KOTAK",
    "KOTAK": "KOTAK",
    "YESB": "YES",
    "YES": "YES",
}

# The handle after the @ in a UPI id, to the app that issues it.
UPI_APPS = {
    "okhdfcbank": "gpay",
    "okicici": "gpay",
    "okaxis": "gpay",
    "oksbi": "gpay",
    "ybl": "phonepe",
    "ibl": "phonepe",
    "axl": "phonepe",
    "paytm": "paytm",
    "upi": "bhim",
}


def card_network(value: object) -> str | None:
    name = text(value)
    if name is None:
        return None
    return CARD_NETWORKS.get(name.lower(), name.lower())


def bank(value: object) -> str | None:
    name = text(value)
    if name is None:
        return None
    return BANKS.get(name.upper(), name.upper())


def upi_app(vpa: object) -> str | None:
    address = text(vpa)
    if address is None or "@" not in address:
        return None
    return UPI_APPS.get(address.rsplit("@", 1)[1].lower())


def required(payload: Payload, key: str) -> object:
    try:
        value: object = payload[key]
    except KeyError as error:
        raise MappingError(f"Missing field {key!r}") from error
    if value is None or value == "":
        raise MappingError(f"Empty field {key!r}")
    return value
