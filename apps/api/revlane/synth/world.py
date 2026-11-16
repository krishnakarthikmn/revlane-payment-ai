"""Builds the synthetic merchant in memory: every order, attempt, refund,
settlement and bank credit, with the leaks and traps planted among them.

Nothing here knows a provider's file format; `render.py` does that. Everything
is driven by one seeded random generator, so the same seed and date always
build the same world.
"""

from bisect import bisect
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import accumulate
from random import Random

from revlane.money import bps_of
from revlane.synth import config as c
from revlane.synth.config import Calendar

BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


class GenerationError(Exception):
    """The requested scale or date cannot carry the planted leaks."""


@dataclass(slots=True)
class Order:
    order_id: str
    placed: datetime
    amount: int
    status: str
    fulfilment: str
    channel: str


@dataclass(slots=True)
class Attempt:
    provider: str
    attempt_id: str
    order_id: str
    seq: int
    day: int
    created: datetime
    amount: int
    method: str
    network: str | None
    card_type: str | None
    bank: str
    upi_app: str | None
    shopper: int
    # What really happened: succeeded, failed, abandoned, auto_refunded,
    # pending_then_failed (trap T4) or unknown (trap T7).
    truth: str
    reason: str | None = None
    fee: int = 0
    tax: int = 0
    odd_status: str | None = None
    failed_at: datetime | None = None
    tag: str | None = None


@dataclass(slots=True)
class Refund:
    provider: str
    refund_id: str
    attempt: Attempt
    amount: int
    kind: str  # merchant or automatic
    at: datetime
    tag: str | None = None


@dataclass(slots=True)
class Line:
    """One line of a settlement. Net is gross less fee less tax."""

    provider: str
    day: int  # the first day it can be settled
    kind: str  # payment, refund or fee
    ref: str
    gross: int
    fee: int
    tax: int
    attempt: Attempt | None = None
    refund: Refund | None = None
    order_id: str | None = None
    fee_type: str | None = None
    tag: str | None = None
    settlement_id: str = ""

    @property
    def net(self) -> int:
        return self.gross - self.fee - self.tax

    @property
    def line_ref(self) -> str:
        """The line's reference as the provider's settlement file writes it."""
        if self.provider == "payu":
            return f"{'R' if self.kind == 'refund' else 'P'}:{self.ref}"
        if self.provider == "cashfree":
            return f"{'REFUND' if self.kind == 'refund' else 'PAYMENT'}:{self.ref}"
        return self.ref


@dataclass(slots=True)
class Settlement:
    provider: str
    settlement_id: str
    day: int
    utr: str
    lines: list[Line]
    paid: int  # what the provider actually paid out
    tag: str | None = None

    @property
    def gross(self) -> int:
        return sum(line.gross for line in self.lines)

    @property
    def fee(self) -> int:
        return sum(line.fee for line in self.lines)

    @property
    def tax(self) -> int:
        return sum(line.tax for line in self.lines)


@dataclass(slots=True)
class BankCredit:
    day: int
    amount: int
    utr: str | None
    narration: str
    tag: str | None = None


@dataclass
class Leak:
    id: str
    type: str
    provider: str
    amount: int
    records: dict[str, list[str]]
    items: list[tuple[str, int]]


@dataclass
class Trap:
    id: str
    expect: str
    records: dict[str, list[str]]


class Picker[T]:
    """Weighted choice with integer weights."""

    def __init__(self, table: Sequence[tuple[T, int]]) -> None:
        self.values = [value for value, _ in table]
        self.bounds = list(accumulate(weight for _, weight in table))

    def pick(self, rng: Random) -> T:
        return self.values[bisect(self.bounds, rng.randrange(self.bounds[-1]))]


PROVIDER = Picker(c.PROVIDERS)
METHOD = Picker(c.METHODS)
NETWORK = Picker(c.CARD_NETWORKS)
BANK = Picker(c.BANKS)
UPI_APP = Picker(c.UPI_APPS)
HOUR = Picker(c.HOURS)
OUTCOME = Picker(c.OUTCOMES)
BAND = Picker(tuple(((low, high), weight) for low, high, weight in c.AMOUNT_BANDS))
REASON = {method: Picker(table) for method, table in c.FAILURE_REASONS.items()}


def fit_amounts(
    rng: Random, count: int, total: int, cost: Callable[[int], int], unit: int
) -> list[int]:
    """`count` amounts, each a multiple of `unit`, whose costs add up to exactly `total`.

    The amounts vary around a common size; the last one is searched for so the
    sum lands on the paisa. All arithmetic is in integers.
    """
    probe = 100_000 * unit
    weights = [rng.randint(60, 140) for _ in range(count)]
    target = total * probe // cost(probe)
    amounts = [max(unit, target * w // sum(weights) // unit * unit) for w in weights]
    for _ in range(500):
        rest = total - sum(cost(a) for a in amounts[:-1])
        guess = rest * probe // cost(probe) // unit * unit
        for step in range(50_000):
            for candidate in (guess + step * unit, guess - step * unit):
                if candidate > 0 and cost(candidate) == rest:
                    amounts[-1] = candidate
                    return amounts
        amounts[-2] += unit  # nudge the one before and look again
    raise GenerationError("Could not fit the planted amounts to their total")


@dataclass
class World:
    seed: int
    cal: Calendar
    scale: float
    rng: Random = field(init=False)
    orders: list[Order] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    refunds: list[Refund] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)
    credits: list[BankCredit] = field(default_factory=list)
    leaks: list[Leak] = field(default_factory=list)
    traps: list[Trap] = field(default_factory=list)
    redelivered: Settlement | None = None
    shoppers: int = 0
    _ids: set[str] = field(default_factory=set)
    _order_counter: int = 1_000_000
    _fee_lines: dict[str, list[Line]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.rng = Random(self.seed)
        self.shoppers = max(500, round(200_000 * self.scale))

    # --- identifiers and times -------------------------------------------------

    def new_id(self, prefix: str, length: int, alphabet: str = BASE62) -> str:
        while True:
            value = prefix + "".join(self.rng.choice(alphabet) for _ in range(length))
            if value not in self._ids:
                self._ids.add(value)
                return value

    def digits(self, prefix: str, length: int) -> str:
        return self.new_id(prefix, length, "0123456789")

    def attempt_id(self, provider: str) -> str:
        if provider == "razorpay":
            return self.new_id("pay_", 14)
        if provider == "payu":
            return self.digits("4039", 11)
        return self.digits("5114", 8)

    def refund_id(self, provider: str) -> str:
        if provider == "razorpay":
            return self.new_id("rfnd_", 14)
        if provider == "payu":
            return self.digits("1318", 8)
        return self.digits("refund_", 10)

    def at(self, day: int, second: int | None = None) -> datetime:
        if second is None:
            second = HOUR.pick(self.rng) * 3600 + self.rng.randrange(3600)
        midnight = datetime.combine(self.cal.days[day], datetime.min.time())
        return midnight + timedelta(seconds=second)

    def amount(self) -> int:
        low, high = BAND.pick(self.rng)
        return self.rng.randrange(low, high) * 100

    # --- building blocks ---------------------------------------------------------

    def add_order(self, placed: datetime, amount: int, channel: str = "web") -> Order:
        self._order_counter += 1
        order = Order(
            f"BK-{self._order_counter}", placed, amount, "unconfirmed", "unfulfilled", channel
        )
        self.orders.append(order)
        return order

    def add_attempt(
        self,
        provider: str,
        day: int,
        amount: int,
        method: str,
        *,
        truth: str = "succeeded",
        network: str | None = None,
        card_type: str | None = None,
        bank: str | None = None,
        order: Order | None = None,
        when: datetime | None = None,
        reason: str | None = None,
        charged_bps: int | None = None,
        tag: str | None = None,
    ) -> Attempt:
        when = when or self.at(day)
        seq = 1
        if order is None:
            order = self.add_order(when - timedelta(seconds=self.rng.randrange(20, 240)), amount)
        else:
            seq = 1 + sum(1 for a in self.attempts[-8:] if a.order_id == order.order_id)
        if method == "card":
            network = network or NETWORK.pick(self.rng)
            card_type = card_type or self.card_type(network)
        attempt = Attempt(
            provider=provider,
            attempt_id=self.attempt_id(provider),
            order_id=order.order_id,
            seq=seq,
            day=day,
            created=when,
            amount=amount,
            method=method,
            network=network if method == "card" else None,
            card_type=card_type if method == "card" else None,
            bank=bank or BANK.pick(self.rng),
            upi_app=UPI_APP.pick(self.rng) if method == "upi" else None,
            shopper=self.rng.randrange(self.shoppers),
            truth=truth,
            reason=reason,
            tag=tag,
        )
        if truth == "succeeded":
            rate = (
                charged_bps
                if charged_bps is not None
                else c.contract_rate(provider, method, network)
            )
            attempt.fee = bps_of(amount, rate)
            attempt.tax = bps_of(attempt.fee, c.TAX_BPS)
            order.status, order.fulfilment = "confirmed", "fulfilled"
        self.attempts.append(attempt)
        return attempt

    def card_type(self, network: str) -> str:
        if network == "amex":
            return "credit"
        if network == "rupay":
            return "debit"
        return "credit" if self.rng.randrange(100) < 55 else "debit"

    def settle(self, attempt: Attempt, tag: str | None = None) -> Line | None:
        """Queue the payment for the next day's settlement, if that day is in the period."""
        if attempt.day + 1 > self.cal.last:
            return None
        line = Line(
            provider=attempt.provider,
            day=attempt.day + 1,
            kind="payment",
            ref=attempt.attempt_id,
            gross=attempt.amount,
            fee=attempt.fee,
            tax=attempt.tax,
            attempt=attempt,
            order_id=attempt.order_id,
            tag=tag,
        )
        self.lines.append(line)
        return line

    def add_refund(
        self,
        attempt: Attempt,
        at: datetime,
        *,
        kind: str = "merchant",
        day: int | None = None,
        return_tax: bool = True,
        tag: str | None = None,
    ) -> tuple[Refund, Line | None]:
        refund = Refund(
            attempt.provider,
            self.refund_id(attempt.provider),
            attempt,
            attempt.amount,
            kind,
            at,
            tag,
        )
        self.refunds.append(refund)
        if kind == "automatic" or day is None or day + 1 > self.cal.last:
            return refund, None
        # A refund reverses the payment and hands back its fee and the tax on it.
        line = Line(
            provider=attempt.provider,
            day=day + 1,
            kind="refund",
            ref=refund.refund_id,
            gross=-attempt.amount,
            fee=-attempt.fee,
            tax=-attempt.tax if return_tax else 0,
            attempt=attempt,
            refund=refund,
            order_id=attempt.order_id,
            tag=tag,
        )
        self.lines.append(line)
        return refund, line

    def order_of(self, attempt: Attempt) -> Order:
        # Orders are created in order-id sequence, so the id gives the position.
        return self.orders[int(attempt.order_id[3:]) - 1_000_001]

    # --- the ordinary business ---------------------------------------------------

    def background(self) -> None:
        cal = self.cal
        per_day = max(6, round(c.ATTEMPTS_PER_DAY * self.scale))
        spread = max(1, per_day // 20)
        incident_day = max(i for i, d in enumerate(cal.days[:-1]) if d.weekday() == 1)
        for day in range(len(cal.days)):
            in_fee_month = cal.fee_month_first <= day <= cal.fee_month_last
            for _ in range(per_day + self.rng.randint(-spread, spread)):
                provider = PROVIDER.pick(self.rng)
                method = METHOD.pick(self.rng)
                network = NETWORK.pick(self.rng) if method == "card" else None
                if network == "amex" and provider == "cashfree" and in_fee_month:
                    network = "visa"  # the month's Cashfree Amex volume is the planted F1 set
                bank = BANK.pick(self.rng)
                when = self.at(day)
                truth = OUTCOME.pick(self.rng)
                # Colour for the preview agents; nothing is tested against these.
                if truth == "succeeded" and self.rng.randrange(100) < self.extra_failure(
                    provider, method, network, bank, day == incident_day, when.hour
                ):
                    truth = "failed"
                reason = REASON[method].pick(self.rng) if truth == "failed" else None
                if truth == "abandoned":
                    reason = "user_cancelled"
                attempt = self.add_attempt(
                    provider,
                    day,
                    self.amount(),
                    method,
                    truth=truth,
                    network=network,
                    bank=bank,
                    when=when,
                    reason=reason,
                )
                if truth != "succeeded":
                    continue
                self.settle(attempt)
                refund_day = day + self.rng.randint(1, 5)
                if self.rng.randrange(1000) < c.MERCHANT_REFUND_PER_MILLE and refund_day < cal.last:
                    self.add_refund(attempt, self.at(refund_day), day=refund_day)
                    order = self.order_of(attempt)
                    order.status, order.fulfilment = "cancelled", "cancelled"

    @staticmethod
    def extra_failure(
        provider: str, method: str, network: str | None, bank: str, incident: bool, hour: int
    ) -> int:
        if (
            incident
            and provider == "razorpay"
            and method == "upi"
            and bank == "HDFC"
            and hour in (14, 15)
        ):
            return 30
        if provider == "razorpay" and method == "card" and bank == "ICICI" and network != "amex":
            return 6
        return 0

    # --- planted leaks -------------------------------------------------------------

    def plant_fee_leaks(self) -> None:
        cal, rng = self.cal, self.rng
        first, last = cal.fee_month_first, cal.fee_month_last

        # F1: Cashfree charges 2.15% on Amex against a 2.00% contract.
        f1: list[Line] = []
        for amount in fit_amounts(rng, c.F1_COUNT, c.F1_TOTAL, c.f1_overcharge, 100):
            attempt = self.add_attempt(
                "cashfree",
                rng.randint(first, last - 1),
                amount,
                "card",
                network="amex",
                charged_bps=c.F1_CHARGED_BPS,
                tag="F1",
            )
            line = self.settle(attempt, tag="F1")
            assert line is not None
            f1.append(line)

        # F2: PayU returns the fee on a refunded card payment but keeps the tax on it.
        # Each booking is cancelled the day it is paid, so the payment and its
        # refund reach the same settlement and no payout is pushed below zero.
        f2: list[Line] = []
        for amount in fit_amounts(rng, c.F2_COUNT, c.F2_TOTAL, c.f2_tax_kept, 100):
            day = rng.randint(first, last - 1)
            network = "visa" if rng.randrange(2) else "mastercard"
            when = self.at(day, rng.randrange(7 * 3600, 20 * 3600))
            attempt = self.add_attempt(
                "payu", day, amount, "card", network=network, when=when, tag="F2"
            )
            self.settle(attempt)
            _, line = self.add_refund(
                attempt,
                when + timedelta(minutes=rng.randint(20, 200)),
                day=day,
                return_tax=False,
                tag="F2",
            )
            assert line is not None
            order = self.order_of(attempt)
            order.status, order.fulfilment = "cancelled", "cancelled"
            f2.append(line)

        # F3: Razorpay charges an instant-settlement fee the contract does not allow.
        f3: list[Line] = []
        for fee in fit_amounts(rng, c.F3_COUNT, c.F3_TOTAL, c.with_tax, 1):
            line = Line(
                provider="razorpay",
                day=rng.randint(first + 1, last),
                kind="fee",
                ref=self.new_id("adj_", 14),
                gross=0,
                fee=fee,
                tax=bps_of(fee, c.TAX_BPS),
                fee_type=c.F3_FEE_TYPE,
                tag="F3",
            )
            self.lines.append(line)
            f3.append(line)
        self._fee_lines = {"F1": f1, "F2": f2, "F3": f3}

    def plant_reconciliation_leaks(self) -> None:
        cal, rng = self.cal, self.rng

        # R1: paid, the order never confirmed, the provider refunded on its own.
        r1: list[Refund] = []
        for amount in fit_amounts(rng, c.R1_COUNT, c.R1_TOTAL, lambda a: a, 100):
            day = rng.randint(cal.last - 6, cal.last)
            when = self.at(day, rng.randrange(7 * 3600, 20 * 3600))
            attempt = self.add_attempt(
                "razorpay",
                day,
                amount,
                "card" if rng.randrange(3) else "upi",
                truth="auto_refunded",
                when=when,
                tag="R1",
            )
            refund, _ = self.add_refund(
                attempt, when + timedelta(minutes=rng.randint(45, 180)), kind="automatic", tag="R1"
            )
            r1.append(refund)
        self.leaks.append(
            Leak(
                "R1",
                "paid_auto_refunded",
                "razorpay",
                c.R1_TOTAL,
                {
                    "orders": [r.attempt.order_id for r in r1],
                    "attempts": [r.attempt.attempt_id for r in r1],
                    "refunds": [r.refund_id for r in r1],
                },
                [(r.attempt.attempt_id, r.amount) for r in r1],
            )
        )

        # R3: captured six days ago, and still in no settlement.
        r3 = [
            self.add_attempt(
                "payu", cal.last - 6, amount, "upi" if rng.randrange(2) else "card", tag="R3"
            )
            for amount in fit_amounts(rng, c.R3_COUNT, c.R3_TOTAL, lambda a: a, 100)
        ]
        self.leaks.append(
            Leak(
                "R3",
                "captured_not_settled",
                "payu",
                c.R3_TOTAL,
                {"orders": [a.order_id for a in r3], "attempts": [a.attempt_id for a in r3]},
                [(a.attempt_id, a.amount) for a in r3],
            )
        )

    # --- traps ---------------------------------------------------------------------

    def plant_attempt_traps(self) -> None:
        cal, rng = self.cal, self.rng

        # T3: fails, then succeeds on a retry within ten minutes. One order, two attempts.
        t3: dict[str, list[str]] = {"orders": [], "attempts": []}
        for _ in range(c.T3_COUNT):
            day = rng.randint(30, cal.last - 9)
            amount = self.amount()
            first = self.add_attempt(
                "razorpay", day, amount, "upi", truth="failed", reason="technical_timeout", tag="T3"
            )
            retry = self.add_attempt(
                "razorpay",
                day,
                amount,
                "upi",
                order=self.order_of(first),
                when=first.created + timedelta(seconds=rng.randint(60, 540)),
                tag="T3",
            )
            self.settle(retry)
            t3["orders"].append(first.order_id)
            t3["attempts"] += [first.attempt_id, retry.attempt_id]
        self.traps.append(Trap("T3", "no_finding", t3))

        # T4: reported pending, then failed two days later.
        t4: list[str] = []
        for _ in range(c.T4_COUNT):
            attempt = self.add_attempt(
                "payu",
                rng.randint(30, cal.last - 12),
                self.amount(),
                "netbanking",
                truth="pending_then_failed",
                reason="bank_declined",
                tag="T4",
            )
            attempt.failed_at = attempt.created + timedelta(
                days=2, seconds=rng.randrange(3600, 21600)
            )
            t4.append(attempt.attempt_id)
        self.traps.append(Trap("T4", "status_changes_history_kept", {"attempts": t4}))

        # T5: a refund that comes out of a later payout than the one that paid the booking.
        t5: dict[str, list[str]] = {"attempts": [], "refunds": []}
        for _ in range(c.T5_COUNT):
            day = rng.randint(40, cal.last - 20)
            attempt = self.add_attempt("cashfree", day, self.amount(), "upi", tag="T5")
            self.settle(attempt)
            refund, _ = self.add_refund(attempt, self.at(day + 3), day=day + 3, tag="T5")
            order = self.order_of(attempt)
            order.status, order.fulfilment = "cancelled", "cancelled"
            t5["attempts"].append(attempt.attempt_id)
            t5["refunds"].append(refund.refund_id)
        self.traps.append(Trap("T5", "no_finding", t5))

        # T7: a status value no mapping knows.
        t7: list[str] = []
        for provider, status in c.T7_STATUSES:
            attempt = self.add_attempt(
                provider,
                rng.randint(30, cal.last - 9),
                self.amount(),
                "upi",
                truth="unknown",
                tag="T7",
            )
            attempt.odd_status = status
            t7.append(attempt.attempt_id)
        self.traps.append(Trap("T7", "mapped_to_unknown_with_alert", {"attempts": t7}))

    # --- settlements -----------------------------------------------------------------

    def settlement_id(self, provider: str, day: int) -> str:
        stamp = self.cal.days[day].strftime("%Y%m%d")
        if provider == "razorpay":
            return self.new_id("setl_", 14)
        if provider == "payu":
            return f"PAYU-SETL-{stamp}"
        if provider == "cashfree":
            return self.digits("77", 7)
        return f"MKT-PO-{stamp}"

    def build_settlements(self) -> None:
        """Each provider pays out daily. A day whose lines do not net to a positive
        amount pays nothing: its lines wait for the next payout."""
        by_provider: dict[str, dict[int, list[Line]]] = {}
        for line in self.lines:
            by_provider.setdefault(line.provider, {}).setdefault(line.day, []).append(line)
        for provider, _ in c.PROVIDERS:
            waiting: list[Line] = []
            for day in range(len(self.cal.days)):
                waiting += by_provider.get(provider, {}).get(day, [])
                paid = sum(line.net for line in waiting)
                if not waiting or paid <= 0:
                    continue
                settlement = Settlement(
                    provider,
                    self.settlement_id(provider, day),
                    day,
                    self.digits("UTR", 12),
                    waiting,
                    paid,
                )
                for line in waiting:
                    line.settlement_id = settlement.settlement_id
                self.settlements.append(settlement)
                waiting = []
            overdue = [
                line for line in waiting if line.kind == "payment" and line.day <= self.cal.last - 3
            ]
            if overdue:
                raise GenerationError(
                    f"{provider}: refunds outweigh payments at the end of the period, so "
                    f"{len(overdue)} payments never settle. Use a larger scale."
                )

    def build_marketplace(self) -> None:
        """Orders sold through a marketplace, paid out weekly. The three most
        recent payouts arrive short (R2)."""
        cal, rng = self.cal, self.rng
        per_day = max(3, round(c.MARKETPLACE_ORDERS_PER_DAY * self.scale))
        placed: dict[int, list[Order]] = {}
        for day in range(len(cal.days)):
            for _ in range(per_day):
                order = self.add_order(self.at(day), self.amount(), channel="marketplace")
                if rng.randrange(100) < 95:
                    order.status, order.fulfilment = "confirmed", "fulfilled"
                    placed.setdefault(day, []).append(order)
                else:
                    order.status, order.fulfilment = "cancelled", "cancelled"

        payout_days = list(range(cal.last - 1, 8, -7))[::-1]
        short = dict(zip(payout_days[-3:], c.R2_SHORTFALLS, strict=True))
        r2: list[Settlement] = []
        for day in payout_days:
            lines = []
            for order_day in range(day - 9, day - 2):
                for order in placed.get(order_day, []):
                    commission = bps_of(order.amount, c.MARKETPLACE_COMMISSION_BPS)
                    lines.append(
                        Line(
                            provider="marketplace",
                            day=day,
                            kind="payment",
                            ref=order.order_id,
                            gross=order.amount,
                            fee=commission,
                            tax=bps_of(commission, c.TAX_BPS),
                            order_id=order.order_id,
                        )
                    )
            owed = sum(line.net for line in lines)
            shortfall = short.get(day, 0)
            if owed <= shortfall:
                raise GenerationError(
                    "Marketplace payouts are too small to arrive short. Use a larger scale."
                )
            payout = Settlement(
                "marketplace",
                self.settlement_id("marketplace", day),
                day,
                self.digits("UTR", 12),
                lines,
                owed - shortfall,
                tag="R2" if shortfall else None,
            )
            for line in lines:
                line.settlement_id = payout.settlement_id
            self.settlements.append(payout)
            if shortfall:
                r2.append(payout)
        self.leaks.append(
            Leak(
                "R2",
                "settled_short",
                "marketplace",
                sum(c.R2_SHORTFALLS),
                {"settlements": [s.settlement_id for s in r2]},
                [(s.settlement_id, sum(line.net for line in s.lines) - s.paid) for s in r2],
            )
        )

    # --- the bank ----------------------------------------------------------------------

    NARRATION = {
        "razorpay": "NEFT CR-YESB0000001-RAZORPAY SOFTWARE PVT LTD-ACME TRAVEL",
        "payu": "NEFT CR-ICIC0000104-PAYU PAYMENTS PVT LTD-ACME TRAVEL",
        "cashfree": "NEFT CR-UTIB0000007-CASHFREE PAYMENTS INDIA-ACME TRAVEL",
        "marketplace": "NEFT CR-HDFC0000240-TRIPBAZAAR MARKETPLACE-ACME TRAVEL",
    }

    def build_bank(self) -> None:
        cal = self.cal
        of = {
            provider: [s for s in self.settlements if s.provider == provider]
            for provider in self.NARRATION
        }

        # T1: three PayU settlements on consecutive days arrive as one credit, no reference.
        group = next(
            (
                trio
                for trio in zip(of["payu"], of["payu"][1:], of["payu"][2:], strict=False)
                if trio[0].day >= 20 and trio[2].day - trio[0].day == 2
            ),
            None,
        )
        # T6: a Saturday settlement that reaches the bank on Monday, no reference.
        late = next(
            (
                s
                for s in of["razorpay"]
                if 30 <= s.day <= cal.last - 10 and cal.days[s.day].weekday() == 5
            ),
            None,
        )
        if group is None or late is None:
            raise GenerationError("No settlements to carry traps T1 and T6. Use a larger scale.")
        grouped = {s.settlement_id for s in group}

        for settlement in self.settlements:
            narration = self.NARRATION[settlement.provider]
            if settlement.settlement_id in grouped:
                if settlement is group[2]:
                    total = sum(s.paid for s in group)
                    self.credits.append(
                        BankCredit(settlement.day, total, None, f"{narration}-BULK", "T1")
                    )
                continue
            if settlement is late:
                self.credits.append(
                    BankCredit(settlement.day + 2, settlement.paid, None, narration, "T6")
                )
                continue
            self.credits.append(
                BankCredit(
                    settlement.day, settlement.paid, settlement.utr, f"{narration}-{settlement.utr}"
                )
            )
        # Credits that have nothing to do with any provider.
        for day in range(4, len(cal.days), 9):
            self.credits.append(
                BankCredit(
                    day,
                    self.rng.randrange(5_000, 90_000) * 100,
                    self.digits("UTR", 12),
                    "NEFT CR-SBIN0000691-ACME HOLDINGS-INTERCOMPANY",
                )
            )
        self.credits.sort(key=lambda credit: (credit.day, credit.narration, credit.amount))

        self.traps.append(
            Trap(
                "T1",
                "no_finding",
                {
                    "settlements": [s.settlement_id for s in group],
                    "bank_credits": [
                        f"{cal.days[group[2].day].isoformat()}:{sum(s.paid for s in group)}"
                    ],
                },
            )
        )
        self.traps.append(
            Trap(
                "T6",
                "no_finding",
                {
                    "settlements": [late.settlement_id],
                    "bank_credits": [f"{cal.days[late.day + 2].isoformat()}:{late.paid}"],
                },
            )
        )
        # T2: one PayU settlement file is delivered a second time.
        self.redelivered = next(s for s in of["payu"] if s.settlement_id not in grouped)
        self.traps.append(
            Trap("T2", "ingested_once", {"settlements": [self.redelivered.settlement_id]})
        )

    # --- finish ---------------------------------------------------------------------------

    def record_fee_leaks(self) -> None:
        spec = (
            (
                "F1",
                "fee_above_contract",
                "cashfree",
                c.F1_TOTAL,
                lambda line: c.f1_overcharge(line.gross),
            ),
            (
                "F2",
                "tax_on_refunded_fee",
                "payu",
                c.F2_TOTAL,
                lambda line: c.f2_tax_kept(-line.gross),
            ),
            ("F3", "fee_not_in_contract", "razorpay", c.F3_TOTAL, lambda line: line.fee + line.tax),
        )
        for leak_id, kind, provider, total, amount_of in spec:
            lines = self._fee_lines[leak_id]
            records = {
                "settlement_lines": [f"{line.settlement_id}/{line.line_ref}" for line in lines]
            }
            if leak_id != "F3":
                records["attempts"] = [line.attempt.attempt_id for line in lines if line.attempt]
            if leak_id == "F2":
                records["refunds"] = [line.refund.refund_id for line in lines if line.refund]
            self.leaks.append(
                Leak(
                    leak_id,
                    kind,
                    provider,
                    total,
                    records,
                    [(f"{line.settlement_id}/{line.line_ref}", amount_of(line)) for line in lines],
                )
            )

    def check(self) -> None:
        """The world must carry exactly what docs/synthetic-merchant.md promises."""
        by_id = {leak.id: leak for leak in self.leaks}
        for leak in self.leaks:
            if sum(amount for _, amount in leak.items) != leak.amount:
                raise GenerationError(f"{leak.id} does not add up to its planted amount")
        reconciliation = sum(by_id[k].amount for k in ("R1", "R2", "R3"))
        fees = sum(by_id[k].amount for k in ("F1", "F2", "F3"))
        fee_lines = [line for lines in self._fee_lines.values() for line in lines]
        if reconciliation != c.RECONCILIATION_TOTAL or fees != c.FEE_AUDIT_TOTAL:
            raise GenerationError("Planted totals do not match the document")
        if len(fee_lines) != c.FEE_AUDIT_LINES or not all(line.settlement_id for line in fee_lines):
            raise GenerationError("Some planted fee lines never settled. Use a larger scale.")
        month = range(self.cal.fee_month_first, self.cal.fee_month_last + 1)
        settled_on = {s.settlement_id: s.day for s in self.settlements}
        if any(settled_on[line.settlement_id] not in month for line in fee_lines):
            raise GenerationError(
                "A planted fee line settled outside the fee month. Use a larger scale."
            )
        for settlement in self.settlements:
            owed = sum(line.net for line in settlement.lines)
            short = owed - settlement.paid
            if settlement.paid <= 0 or short != (
                dict(by_id["R2"].items).get(settlement.settlement_id, 0)
            ):
                raise GenerationError(f"Settlement {settlement.settlement_id} is inconsistent")


def build_world(seed: int, cal: Calendar, scale: float) -> World:
    world = World(seed, cal, scale)
    world.background()
    world.plant_fee_leaks()
    world.plant_reconciliation_leaks()
    world.plant_attempt_traps()
    world.build_settlements()
    world.build_marketplace()
    world.build_bank()
    world.record_fee_leaks()
    world.leaks.sort(key=lambda leak: leak.id)
    world.traps.sort(key=lambda trap: trap.id)
    world.check()
    return world
