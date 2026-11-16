"""Status derivation. Rule 9: status is derived from `attempt_event`.

`derive` is a pure function of an attempt's events and the clock. The
`attempt_status` table only caches its answers and can be rebuilt at any time.
Change a rule here, raise `RULES_VERSION`, and rebuild.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from revlane.connectors import statuses as s

RULES_VERSION = "1"
# Statuses a provider reports once the payment has come to rest.
FINAL = frozenset({s.SUCCEEDED, s.FAILED, s.ABANDONED, s.AUTO_REFUNDED})


@dataclass(frozen=True, slots=True)
class StatusRules:
    # A checkout nobody finished is abandoned after this long.
    stale_after: timedelta = timedelta(minutes=30)
    # Razorpay refunds a payment that is authorised and not captured in this time.
    capture_window: timedelta = timedelta(days=3)

    @classmethod
    def from_policy(cls, policy: dict[str, Any]) -> "StatusRules":
        """A tenant's own periods, from `tenant.policy["status"]`, in minutes."""
        own = policy.get("status") or {}
        default = cls()
        return cls(
            stale_after=_minutes(own.get("stale_after_minutes"), default.stale_after),
            capture_window=_minutes(own.get("capture_window_minutes"), default.capture_window),
        )


def _minutes(value: object, default: timedelta) -> timedelta:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return timedelta(minutes=value)
    return default


@dataclass(frozen=True, slots=True)
class Event:
    provider_status: str
    occurred_at: datetime
    provider_stage: str | None = None
    provider_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class Derived:
    status: str
    failure_reason: str | None
    # The provider's word in the event that decided the status.
    provider_status: str
    last_event_at: datetime
    # When a clock rule will change the status with no new event, if one will.
    changes_at: datetime | None = None


def derive(
    provider: str, events: Sequence[Event], now: datetime, rules: StatusRules | None = None
) -> Derived:
    """The canonical status of one attempt.

    A final report decides: the latest one that says the payment succeeded,
    failed, was abandoned or was refunded. A report that the payment is still
    in progress never undoes a final one, whatever its time, because a
    snapshot carries the payment's time and not the time of its status. With
    no final report, the latest report decides. Of two reports with the same
    time, the one given later wins.

    Then the clock rules apply: a checkout left untouched becomes abandoned,
    and an authorisation never captured becomes an automatic refund.
    """
    if not events:
        raise ValueError("An attempt with no events has no status")
    rules = rules or StatusRules()
    ordered = sorted(enumerate(events), key=lambda pair: (pair[1].occurred_at, pair[0]))
    final = [
        event
        for _, event in ordered
        if s.canonical_status(provider, event.provider_status, event.provider_stage) in FINAL
    ]
    latest = final[-1] if final else ordered[-1][1]
    status = s.canonical_status(provider, latest.provider_status, latest.provider_stage)
    changes_at = None

    clock = s.CLOCK_RULES.get((provider, latest.provider_status))
    if clock is not None:
        rule, becomes = clock
        period = rules.stale_after if rule == s.STALE else rules.capture_window
        due = latest.occurred_at + period
        if now >= due:
            status = becomes
        else:
            changes_at = due

    reason = None
    if status == s.FAILED:
        reason = s.failure_reason(provider, latest.provider_error_code)
    return Derived(status, reason, latest.provider_status, latest.occurred_at, changes_at)
