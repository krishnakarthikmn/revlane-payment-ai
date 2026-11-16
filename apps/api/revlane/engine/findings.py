"""Findings: how residuals are grouped, the states a finding moves through,
and how a new engine run meets the findings already stored.

All pure. The caller reads the stored findings, calls `reconcile`, and
writes the actions it returns.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

LOST, RECOVERABLE, CLAIMED, RECOVERED, WRITTEN_OFF = (
    "lost",
    "recoverable",
    "claimed",
    "recovered",
    "written_off",
)
# States in which nobody has acted yet: the engine keeps these up to date.
OPEN = frozenset({LOST, RECOVERABLE})

INITIAL_STATE = {
    "paid_auto_refunded": RECOVERABLE,
    "settled_short": RECOVERABLE,
    "captured_not_settled": RECOVERABLE,
    "fee_above_contract": RECOVERABLE,
    "tax_on_refunded_fee": RECOVERABLE,
    "fee_not_in_contract": RECOVERABLE,
}

# Money is recovered only through a claim, and what has reached the bank stays there.
TRANSITIONS: dict[str, frozenset[str]] = {
    RECOVERABLE: frozenset({CLAIMED, LOST, WRITTEN_OFF}),
    LOST: frozenset({RECOVERABLE, WRITTEN_OFF}),
    CLAIMED: frozenset({RECOVERED, RECOVERABLE, WRITTEN_OFF}),
    WRITTEN_OFF: frozenset({RECOVERABLE}),
    RECOVERED: frozenset(),
}

NO_LONGER_FOUND = "The records now match: the engine no longer finds this."
MONEY_ARRIVED = "The engine no longer finds this after the claim: the money has arrived."
CLAIM_PAID = "A bank credit for the claimed amount has arrived."


class InvalidTransition(ValueError):
    pass


def check_transition(from_state: str, to_state: str) -> None:
    if to_state not in TRANSITIONS.get(from_state, frozenset()):
        raise InvalidTransition(f"A finding cannot move from {from_state} to {to_state}.")


@dataclass(frozen=True, slots=True)
class Item:
    """One record a finding rests on, and the money it accounts for."""

    id: str
    amount_minor: int
    on: date  # decides which month's finding the item belongs to
    # Every record id behind the item, as (kind, id): ("attempts", "pay_1").
    records: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Detected:
    """A finding as an engine run sees it, before it is stored."""

    type: str
    provider: str
    cause: str
    period_start: date
    period_end: date
    items: tuple[Item, ...]
    rule_version: str

    @property
    def amount_minor(self) -> int:
        return sum(item.amount_minor for item in self.items)

    @property
    def key(self) -> str:
        """What makes this the same finding from one run to the next."""
        return f"{self.type}:{self.provider}:{self.cause}:{self.period_start:%Y-%m}"

    def evidence(self) -> dict[str, list[object]]:
        """Record ids by kind, then each item with its amount."""
        by_kind: dict[str, list[object]] = {}
        for item in self.items:
            for kind, record_id in item.records:
                by_kind.setdefault(kind, []).append(record_id)
        by_kind["items"] = [
            {"id": item.id, "amount_minor": item.amount_minor} for item in self.items
        ]
        return by_kind


def month_of(day: date) -> tuple[date, date]:
    first = day.replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return first, next_month - timedelta(days=1)


def group(
    type: str, provider: str, cause: str, items: Sequence[Item], rule_version: str
) -> list[Detected]:
    """One claim-ready finding per calendar month, oldest first."""
    by_month: dict[tuple[date, date], list[Item]] = defaultdict(list)
    for item in items:
        by_month[month_of(item.on)].append(item)
    return [
        Detected(
            type,
            provider,
            cause,
            first,
            last,
            tuple(sorted(by_month[first, last], key=lambda item: (item.on, item.id))),
            rule_version,
        )
        for first, last in sorted(by_month)
    ]


# --- A new run meets what is stored ----------------------------------------------


@dataclass(frozen=True, slots=True)
class Existing:
    """A stored finding, as far as `reconcile` needs to know it."""

    id: str
    key: str
    sequence: int
    state: str
    amount_minor: int
    item_ids: frozenset[str]
    # False for a finding the engine closed itself because the leak went away:
    # if the leak comes back, it is reported again.
    covers: bool = True


@dataclass(frozen=True, slots=True)
class Create:
    detected: Detected
    sequence: int


@dataclass(frozen=True, slots=True)
class Update:
    finding_id: str
    detected: Detected


@dataclass(frozen=True, slots=True)
class Move:
    finding_id: str
    from_state: str
    to_state: str
    reason: str


Action = Create | Update | Move


def reconcile(existing: Sequence[Existing], detected: Sequence[Detected]) -> list[Action]:
    """What to write so the stored findings agree with this run.

    - An open finding follows the engine: it is updated, or written off when
      the leak is no longer there.
    - Records in a finding somebody has acted on (claimed, recovered, or
      written off by a person) are never reported a second time. Anything new
      under the same key becomes a follow-on finding.
    - A claimed finding the engine no longer finds at all has been recovered.
    """
    stored: dict[str, list[Existing]] = defaultdict(list)
    for finding in existing:
        stored[finding.key].append(finding)
    found = {finding.key: finding for finding in detected}

    actions: list[Action] = []
    for key in sorted(stored.keys() | found.keys()):
        now = found.get(key)
        now_ids = {item.id for item in now.items} if now else set()
        acted_on = [f for f in stored[key] if f.state not in OPEN]
        covered: set[str] = set()
        for finding in acted_on:
            if finding.covers:
                covered |= finding.item_ids
            if finding.state == CLAIMED and not finding.item_ids & now_ids:
                actions.append(Move(finding.id, CLAIMED, RECOVERED, MONEY_ARRIVED))

        remaining = (
            replace(now, items=tuple(item for item in now.items if item.id not in covered))
            if now
            else None
        )
        open_finding = next((f for f in stored[key] if f.state in OPEN), None)
        if open_finding is not None:
            if remaining is None or not remaining.items:
                actions.append(
                    Move(open_finding.id, open_finding.state, WRITTEN_OFF, NO_LONGER_FOUND)
                )
            elif (
                open_finding.item_ids != {item.id for item in remaining.items}
                or open_finding.amount_minor != remaining.amount_minor
            ):
                actions.append(Update(open_finding.id, remaining))
        elif remaining is not None and remaining.items:
            sequence = max((f.sequence for f in stored[key]), default=0) + 1
            actions.append(Create(remaining, sequence))
    return actions
