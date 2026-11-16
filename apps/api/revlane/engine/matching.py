"""The three-way match. See docs/technical-design.md, section 6.

1. Order to attempt, on the merchant's order id.
2. Attempt to settlement line, on the provider's payment id.
3. Settlement to bank credit: on UTR; otherwise on amount and date inside a
   window; otherwise a group of settlements from one provider that sums to
   one credit.
4. Payout to lines: a payout lower than the sum of its lines is short.
5. What is left over becomes a finding, with the ids of its records.

Every function here is pure: records and a date in, links and findings out.
Change a rule, raise `RULES_VERSION`.
"""

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations

from revlane.engine.findings import Detected, Item, group
from revlane.engine.records import Attempt, BankCredit, Line, Link, Order, Refund, Settlement

RULES_VERSION = "1"

SUCCEEDED, AUTO_REFUNDED = "succeeded", "auto_refunded"
EXACT = Decimal(1)
# Matches made without a reference are sound but not certain: a person can review them.
BY_AMOUNT_AND_DATE = Decimal("0.9")
BY_GROUP_SUM = Decimal("0.8")
LINES_PER_TOLERANCE_STEP = 1_000
SATURDAY = 5

CAUSE_AUTO_REFUNDED = "auto_refunded_order_not_confirmed"
CAUSE_NOT_SETTLED = "no_settlement_line_after_grace"
CAUSE_PAYOUT_BELOW_LINES = "payout_below_lines"
CAUSE_CREDIT_BELOW_PAYOUT = "bank_credit_below_payout"

Pairing = Callable[[Settlement, BankCredit], bool]


def _daily() -> dict[str, int]:
    # Days from payment to settlement. The gateways settle the next day.
    return {"razorpay": 1, "payu": 1, "cashfree": 1}


@dataclass(frozen=True)
class MatchRules:
    cycle_days: Mapping[str, int] = field(default_factory=_daily)
    grace_working_days: int = 2
    bank_window_days: int = 3
    # A group, or a payout against its lines, may be off by this much per 1,000 lines.
    group_tolerance_minor: int = 100
    # The most settlements considered together for one credit.
    max_group_candidates: int = 12
    # Order statuses that mean the merchant kept the sale.
    confirmed_statuses: frozenset[str] = frozenset({"confirmed"})

    @classmethod
    def from_policy(cls, policy: Mapping[str, object]) -> "MatchRules":
        """A tenant's own values, from `tenant.policy["matching"]`."""
        default = cls()
        cycles = dict(default.cycle_days)
        own_cycles = policy.get("cycle_days")
        if isinstance(own_cycles, Mapping):
            cycles.update({str(k): int(v) for k, v in own_cycles.items()})

        def whole(name: str, fallback: int) -> int:
            value = policy.get(name)
            return int(value) if isinstance(value, int | str) else fallback

        statuses = policy.get("confirmed_statuses")
        return cls(
            cycle_days=cycles,
            grace_working_days=whole("grace_working_days", default.grace_working_days),
            bank_window_days=whole("bank_window_days", default.bank_window_days),
            group_tolerance_minor=whole("group_tolerance_minor", default.group_tolerance_minor),
            max_group_candidates=whole("max_group_candidates", default.max_group_candidates),
            confirmed_statuses=(
                frozenset(str(s) for s in statuses)
                if isinstance(statuses, list | tuple)
                else default.confirmed_statuses
            ),
        )

    def tolerance(self, line_count: int) -> int:
        """One step of tolerance for every started 1,000 lines."""
        steps = max(1, -(-line_count // LINES_PER_TOLERANCE_STEP))
        return steps * self.group_tolerance_minor


# --- Step 1: order to attempt ---------------------------------------------------


def link_orders(orders: Sequence[Order], attempts: Sequence[Attempt]) -> list[Link]:
    known = {order.order_id for order in orders}
    return [
        Link(
            "order",
            attempt.order_id,
            "payment_attempt",
            attempt.key,
            "order_id",
            EXACT,
            attempt.amount_minor,
        )
        for attempt in attempts
        if attempt.order_id is not None and attempt.order_id in known
    ]


def find_auto_refunded(
    orders: Sequence[Order],
    attempts: Sequence[Attempt],
    refunds: Sequence[Refund],
    rules: MatchRules,
) -> list[Item]:
    """Paid, then refunded by the provider on its own, on an order the merchant never kept."""
    kept = {order.order_id for order in orders if order.status in rules.confirmed_statuses}
    automatic: dict[str, list[Refund]] = defaultdict(list)
    for refund in refunds:
        if refund.type == "automatic":
            automatic[f"{refund.provider}:{refund.attempt_id}"].append(refund)

    items = []
    for attempt in attempts:
        own = automatic.get(attempt.key, [])
        if attempt.status != AUTO_REFUNDED and not own:
            continue
        if attempt.order_id in kept:
            continue
        records = [("orders", attempt.order_id)] if attempt.order_id else []
        records.append(("attempts", attempt.attempt_id))
        records += [("refunds", refund.refund_id) for refund in own]
        amount = sum(refund.amount_minor for refund in own) if own else attempt.amount_minor
        items.append(Item(attempt.attempt_id, amount, attempt.paid_on, tuple(records)))
    return items


# --- Step 2: attempt to settlement line -------------------------------------------


def link_settlement_lines(attempts: Sequence[Attempt], lines: Sequence[Line]) -> list[Link]:
    known = {attempt.key for attempt in attempts}
    return [
        Link(
            "payment_attempt",
            f"{line.provider}:{line.attempt_id}",
            "settlement_line",
            line.key,
            "payment_id",
            EXACT,
            line.gross_minor,
        )
        for line in lines
        if line.line_type == "capture" and f"{line.provider}:{line.attempt_id}" in known
    ]


def link_refunds(refunds: Sequence[Refund], lines: Sequence[Line]) -> list[Link]:
    """A refund to the settlement line that took it out of a payout."""
    waiting: dict[tuple[str, str, int], list[Refund]] = defaultdict(list)
    for refund in sorted(refunds, key=lambda r: (r.refunded_on, r.refund_id)):
        waiting[refund.provider, refund.attempt_id, refund.amount_minor].append(refund)
    links = []
    for line in sorted(lines, key=lambda line: line.key):
        if line.line_type != "refund" or line.attempt_id is None:
            continue
        candidates = waiting.get((line.provider, line.attempt_id, -line.gross_minor))
        if candidates:
            refund = candidates.pop(0)
            links.append(
                Link(
                    "refund",
                    refund.key,
                    "settlement_line",
                    line.key,
                    "refund",
                    EXACT,
                    refund.amount_minor,
                )
            )
    return links


def add_working_days(day: date, working_days: int) -> date:
    while working_days > 0:
        day += timedelta(days=1)
        if day.weekday() < SATURDAY:
            working_days -= 1
    return day


def settlement_due(paid_on: date, provider: str, rules: MatchRules) -> date | None:
    """The last day a payment can settle without being late: the provider's
    cycle plus the grace period. None for a provider with no known cycle."""
    cycle = rules.cycle_days.get(provider)
    if cycle is None:
        return None
    return add_working_days(paid_on + timedelta(days=cycle), rules.grace_working_days)


def find_unsettled(
    attempts: Sequence[Attempt], lines: Sequence[Line], as_of: date, rules: MatchRules
) -> list[Item]:
    """Succeeded payments in no settlement after the cycle and the grace period."""
    settled = {
        f"{line.provider}:{line.attempt_id}" for line in lines if line.line_type == "capture"
    }
    items = []
    for attempt in attempts:
        if attempt.status != SUCCEEDED or attempt.key in settled:
            continue
        due = settlement_due(attempt.paid_on, attempt.provider, rules)
        if due is None or as_of <= due:
            continue
        records = [("orders", attempt.order_id)] if attempt.order_id else []
        records.append(("attempts", attempt.attempt_id))
        items.append(
            Item(attempt.attempt_id, attempt.amount_minor, attempt.paid_on, tuple(records))
        )
    return items


# --- Step 3: settlement to bank credit ---------------------------------------------


@dataclass
class BankMatch:
    links: list[Link] = field(default_factory=list)
    # Credits that came in below the settlement carrying the same UTR, by provider.
    short_by_provider: dict[str, list[Item]] = field(default_factory=dict)
    # Settlements with no credit once the window has passed, and credits no settlement explains.
    unmatched_settlements: list[str] = field(default_factory=list)
    unmatched_credits: list[str] = field(default_factory=list)

    @property
    def short(self) -> list[Item]:
        return [item for items in self.short_by_provider.values() for item in items]


def _bank_link(settlement: Settlement, credit: BankCredit, rule: str, confidence: Decimal) -> Link:
    return Link(
        "settlement",
        settlement.key,
        "bank_credit",
        credit.credit_id,
        rule,
        confidence,
        settlement.net_minor,
    )


def match_bank(
    settlements: Sequence[Settlement],
    credits: Sequence[BankCredit],
    line_counts: Mapping[str, int],
    as_of: date,
    rules: MatchRules,
    rejected: frozenset[tuple[str, str]] = frozenset(),
) -> BankMatch:
    """Link each settlement to the bank credit that paid it.

    `line_counts` is the number of lines in each settlement, by settlement
    key: it sets the tolerance for a group. `rejected` holds the pairs a
    person has undone, which are not made again.
    """
    result = BankMatch()
    window = timedelta(days=rules.bank_window_days)
    open_settlements = sorted(
        settlements, key=lambda s: (s.settled_on, s.provider, s.settlement_id)
    )
    open_credits = sorted(credits, key=lambda c: (c.credited_on, c.credit_id))

    def allowed(settlement: Settlement, credit: BankCredit) -> bool:
        return (settlement.key, credit.credit_id) not in rejected

    def in_window(settlement: Settlement, credit: BankCredit) -> bool:
        return settlement.settled_on <= credit.credited_on <= settlement.settled_on + window

    # Exact reference.
    by_utr: dict[str, BankCredit] = {}
    for with_utr in open_credits:
        if with_utr.utr:
            by_utr.setdefault(with_utr.utr, with_utr)
    used: set[str] = set()
    remaining: list[Settlement] = []
    for settlement in open_settlements:
        credit: BankCredit | None = by_utr.get(settlement.utr) if settlement.utr else None
        if credit is None or credit.credit_id in used or not allowed(settlement, credit):
            remaining.append(settlement)
            continue
        used.add(credit.credit_id)
        result.links.append(_bank_link(settlement, credit, "utr", EXACT))
        if credit.amount_minor < settlement.net_minor:
            result.short_by_provider.setdefault(settlement.provider, []).append(
                Item(
                    settlement.settlement_id,
                    settlement.net_minor - credit.amount_minor,
                    settlement.settled_on,
                    (("settlements", settlement.settlement_id), ("bank_credits", credit.credit_id)),
                )
            )
    open_settlements = remaining
    open_credits = [c for c in open_credits if c.credit_id not in used]

    # The same amount, landing inside the window. No tolerance for a single match.
    remaining = []
    for settlement in open_settlements:
        credit = next(
            (
                c
                for c in open_credits
                if c.amount_minor == settlement.net_minor
                and in_window(settlement, c)
                and allowed(settlement, c)
            ),
            None,
        )
        if credit is None:
            remaining.append(settlement)
            continue
        open_credits.remove(credit)
        result.links.append(_bank_link(settlement, credit, "amount_date", BY_AMOUNT_AND_DATE))
    open_settlements = remaining

    # Several settlements from one provider paid as one credit.
    for bulk in list(open_credits):
        members = _group_for(bulk, open_settlements, line_counts, rules, in_window, allowed)
        if not members:
            continue
        open_credits.remove(bulk)
        for settlement in members:
            open_settlements.remove(settlement)
            result.links.append(_bank_link(settlement, bulk, "group_sum", BY_GROUP_SUM))

    result.unmatched_settlements = [
        settlement.key for settlement in open_settlements if as_of > settlement.settled_on + window
    ]
    result.unmatched_credits = [credit.credit_id for credit in open_credits]
    return result


def _group_for(
    credit: BankCredit,
    settlements: Sequence[Settlement],
    line_counts: Mapping[str, int],
    rules: MatchRules,
    in_window: Pairing,
    allowed: Pairing,
) -> tuple[Settlement, ...]:
    """The group of two or more settlements, all from one provider, that comes
    closest to the credit and is inside the tolerance. Empty when there is none."""
    by_provider: dict[str, list[Settlement]] = defaultdict(list)
    for settlement in settlements:
        if in_window(settlement, credit) and allowed(settlement, credit):
            by_provider[settlement.provider].append(settlement)

    best: tuple[int, int, tuple[Settlement, ...]] | None = None
    for provider in sorted(by_provider):
        # The settlements nearest the credit, should there be too many to try them all.
        candidates = sorted(by_provider[provider], key=lambda s: s.settled_on, reverse=True)[
            : rules.max_group_candidates
        ][::-1]
        for size in range(2, len(candidates) + 1):
            for members in combinations(candidates, size):
                off = abs(sum(s.net_minor for s in members) - credit.amount_minor)
                lines = sum(line_counts.get(s.key, 0) for s in members)
                if off > rules.tolerance(lines):
                    continue
                if best is None or (off, size) < best[:2]:
                    best = (off, size, members)
    return best[2] if best else ()


# --- Recoveries: claimed money arriving in the bank ---------------------------------


@dataclass(frozen=True, slots=True)
class Claimed:
    """A finding somebody has claimed, and the day the claim was drafted."""

    finding_id: str
    amount_minor: int
    claimed_on: date


def match_recoveries(
    claimed: Sequence[Claimed],
    credits: Sequence[BankCredit],
    taken: frozenset[str] = frozenset(),
) -> list[Link]:
    """Link each claimed finding to a bank credit for exactly the claimed amount,
    landing on or after the day of the claim.

    `credits` are the ones no settlement explains; `taken` are credits already
    counted as a recovery. A credit pays for one claim, the oldest first.
    """
    free = sorted(
        (credit for credit in credits if credit.credit_id not in taken),
        key=lambda credit: (credit.credited_on, credit.credit_id),
    )
    links = []
    for claim in sorted(claimed, key=lambda c: (c.claimed_on, c.finding_id)):
        paid = next(
            (
                credit
                for credit in free
                if credit.amount_minor == claim.amount_minor
                and credit.credited_on >= claim.claimed_on
            ),
            None,
        )
        if paid is None:
            continue
        free.remove(paid)
        links.append(
            Link(
                "finding",
                claim.finding_id,
                "bank_credit",
                paid.credit_id,
                "claim_amount",
                BY_AMOUNT_AND_DATE,
                claim.amount_minor,
            )
        )
    return links


# --- Step 4: payout to lines ---------------------------------------------------------


def find_short_payouts(
    settlements: Sequence[Settlement], lines: Sequence[Line], rules: MatchRules
) -> list[Item]:
    """Payouts lower than the sum of their lines.

    A refund or chargeback line is negative, so one that comes out of a later
    payout lowers that payout's lines and its total together: nothing is short.
    """
    owed: dict[str, int] = defaultdict(int)
    count: dict[str, int] = defaultdict(int)
    for line in lines:
        owed[line.settlement_key] += line.net_minor
        count[line.settlement_key] += 1
    items = []
    for settlement in settlements:
        if settlement.key not in owed:
            continue  # no lines to compare the payout with
        short = owed[settlement.key] - settlement.net_minor
        if short > rules.tolerance(count[settlement.key]):
            items.append(
                Item(
                    settlement.settlement_id,
                    short,
                    settlement.settled_on,
                    (("settlements", settlement.settlement_id),),
                )
            )
    return items


# --- All four steps ---------------------------------------------------------------------


@dataclass
class MatchResult:
    links: list[Link]
    findings: list[Detected]
    unmatched_settlements: list[str]
    unmatched_credits: list[str]


def _by_provider[T: (Attempt, Settlement)](records: Sequence[T]) -> dict[str, list[T]]:
    found: dict[str, list[T]] = defaultdict(list)
    for record in records:
        found[record.provider].append(record)
    return dict(sorted(found.items()))


def three_way_match(
    *,
    orders: Sequence[Order],
    attempts: Sequence[Attempt],
    refunds: Sequence[Refund],
    settlements: Sequence[Settlement],
    lines: Sequence[Line],
    credits: Sequence[BankCredit],
    as_of: date,
    rules: MatchRules | None = None,
    rejected: frozenset[tuple[str, str]] = frozenset(),
) -> MatchResult:
    rules = rules or MatchRules()
    line_counts: dict[str, int] = defaultdict(int)
    for line in lines:
        line_counts[line.settlement_key] += 1
    bank = match_bank(settlements, credits, line_counts, as_of, rules, rejected)

    findings: list[Detected] = []
    for provider, own in _by_provider(attempts).items():
        findings += group(
            "paid_auto_refunded",
            provider,
            CAUSE_AUTO_REFUNDED,
            find_auto_refunded(orders, own, refunds, rules),
            RULES_VERSION,
        )
        findings += group(
            "captured_not_settled",
            provider,
            CAUSE_NOT_SETTLED,
            find_unsettled(own, lines, as_of, rules),
            RULES_VERSION,
        )
    for provider, paid in _by_provider(settlements).items():
        findings += group(
            "settled_short",
            provider,
            CAUSE_PAYOUT_BELOW_LINES,
            find_short_payouts(paid, lines, rules),
            RULES_VERSION,
        )
        findings += group(
            "settled_short",
            provider,
            CAUSE_CREDIT_BELOW_PAYOUT,
            bank.short_by_provider.get(provider, []),
            RULES_VERSION,
        )
    return MatchResult(
        links=[
            *link_orders(orders, attempts),
            *link_settlement_lines(attempts, lines),
            *link_refunds(refunds, lines),
            *bank.links,
        ],
        findings=findings,
        unmatched_settlements=bank.unmatched_settlements,
        unmatched_credits=bank.unmatched_credits,
    )
