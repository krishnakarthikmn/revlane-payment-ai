"""The records the engines work on: plain values, already read from the database.

An engine never sees a row, a session or a provider payload. The caller loads
these, hands them over, and stores what comes back. Amounts are paise.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    amount_minor: int
    status: str
    placed_on: date


@dataclass(frozen=True, slots=True)
class Attempt:
    provider: str
    attempt_id: str
    order_id: str | None
    amount_minor: int
    status: str  # the canonical, derived status
    paid_on: date
    method: str | None
    card_network: str | None
    card_type: str | None

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.attempt_id}"


@dataclass(frozen=True, slots=True)
class Refund:
    provider: str
    refund_id: str
    attempt_id: str
    type: str  # merchant or automatic
    amount_minor: int
    refunded_on: date

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.refund_id}"


@dataclass(frozen=True, slots=True)
class Settlement:
    provider: str
    settlement_id: str
    net_minor: int  # what the provider says it paid out
    utr: str | None
    settled_on: date

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.settlement_id}"


@dataclass(frozen=True, slots=True)
class Line:
    """One settlement line. Net is gross less fee less tax."""

    provider: str
    settlement_id: str
    line_ref: str
    attempt_id: str | None
    line_type: str  # capture, refund, chargeback, reversal or fee_adjustment
    gross_minor: int
    fee_minor: int
    tax_minor: int
    net_minor: int
    fee_type: str | None

    @property
    def line_id(self) -> str:
        """The line as a finding's evidence names it."""
        return f"{self.settlement_id}/{self.line_ref}"

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.line_id}"

    @property
    def settlement_key(self) -> str:
        return f"{self.provider}:{self.settlement_id}"


@dataclass(frozen=True, slots=True)
class BankCredit:
    credit_id: str  # the hash of the statement line
    utr: str | None
    amount_minor: int
    credited_on: date


@dataclass(frozen=True, slots=True)
class Contract:
    """One row of a fee contract: the rate agreed for a method over a period."""

    provider: str
    method: str
    card_network: str | None  # None covers every network with no row of its own
    card_type: str | None
    rate_bps: int
    fixed_fee_minor: int
    tax_rate_bps: int
    # True when the contract lets the provider keep the tax on a fee it hands back.
    tax_on_refunded_fee: bool
    allowed_fee_types: frozenset[str]
    effective_from: date
    effective_to: date | None

    def in_force(self, on: date) -> bool:
        return self.effective_from <= on and (self.effective_to is None or on <= self.effective_to)


@dataclass(frozen=True, slots=True)
class Link:
    """One pairing of two records, as `match_link` stores it."""

    left_type: str
    left_id: str
    right_type: str
    right_id: str
    rule: str
    confidence: Decimal
    amount_minor: int | None
