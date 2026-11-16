"""Contract tests: every status in the mapping table, for each provider.

The table is docs/technical-design.md, section 4. A status added to or
removed from a provider's map fails here until this file is changed too.
"""

from datetime import UTC, datetime, timedelta

import pytest

from revlane.connectors import statuses as s
from revlane.db.models import ATTEMPT_STATUSES
from revlane.engine.status import RULES_VERSION, Event, StatusRules, derive

T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
SOON = T0 + timedelta(minutes=5)
LATER = T0 + timedelta(days=10)

# (provider, provider status, stage, status soon after, status ten days later)
TABLE = [
    ("payu", "captured", None, "succeeded", "succeeded"),
    ("payu", "auth", None, "authorized", "authorized"),
    ("payu", "failed", None, "failed", "failed"),
    ("payu", "dropped", None, "failed", "failed"),
    ("payu", "pending", None, "pending", "pending"),
    ("payu", "in progress", None, "pending", "pending"),
    ("payu", "initiated", None, "pending", "abandoned"),
    ("payu", "bounced", None, "abandoned", "abandoned"),
    ("payu", "userCancelled", None, "abandoned", "abandoned"),
    ("payu", "autoRefund", None, "auto_refunded", "auto_refunded"),
    ("razorpay", "captured", None, "succeeded", "succeeded"),
    ("razorpay", "authorized", None, "authorized", "auto_refunded"),
    ("razorpay", "failed", None, "failed", "failed"),
    ("razorpay", "created", None, "pending", "abandoned"),
    ("razorpay", "refunded", "captured", "succeeded", "succeeded"),
    ("razorpay", "refunded", "not_captured", "auto_refunded", "auto_refunded"),
    ("cashfree", "SUCCESS", None, "succeeded", "succeeded"),
    ("cashfree", "FAILED", None, "failed", "failed"),
    ("cashfree", "PENDING", None, "pending", "pending"),
    ("cashfree", "NOT_ATTEMPTED", None, "pending", "pending"),
    ("cashfree", "USER_DROPPED", None, "abandoned", "abandoned"),
    ("cashfree", "CANCELLED", None, "auto_refunded", "auto_refunded"),
    ("cashfree", "FLAGGED", None, "held_for_review", "held_for_review"),
]
IDS = [f"{row[0]}:{row[1]}:{row[2] or '-'}" for row in TABLE]


@pytest.mark.parametrize(("provider", "word", "stage", "soon", "later"), TABLE, ids=IDS)
def test_every_status_in_the_mapping_table(
    provider: str, word: str, stage: str | None, soon: str, later: str
) -> None:
    events = [Event(word, T0, stage)]

    assert derive(provider, events, SOON).status == soon
    assert derive(provider, events, LATER).status == later


def test_the_table_above_covers_every_mapped_status_and_nothing_else() -> None:
    pinned = {(provider, word) for provider, word, *_ in TABLE}
    mapped = {(provider, word) for provider, words in s.STATUS_MAP.items() for word in words}

    assert pinned == mapped
    assert set(s.STATUS_MAP) == {"payu", "razorpay", "cashfree"}


def test_every_canonical_status_is_one_the_database_allows() -> None:
    produced = {status for words in s.STATUS_MAP.values() for status in words.values()}
    produced |= {becomes for _, becomes in s.CLOCK_RULES.values()} | {s.UNKNOWN}

    assert produced == set(ATTEMPT_STATUSES)


@pytest.mark.parametrize(
    ("provider", "word"),
    [
        ("payu", "onHold"),
        ("payu", "Captured"),  # the words are case-sensitive
        ("razorpay", "under_review"),
        ("cashfree", "INCOMPLETE"),
        ("cashfree", "VOID"),  # documented by Cashfree, absent from the design's table
        ("marketplace", "captured"),
    ],
)
def test_anything_else_is_unknown(provider: str, word: str) -> None:
    assert derive(provider, [Event(word, T0)], LATER).status == "unknown"


# --- Clock rules ---------------------------------------------------------------


def test_a_checkout_is_abandoned_after_thirty_minutes_by_default() -> None:
    events = [Event("created", T0)]

    before = derive("razorpay", events, T0 + timedelta(minutes=29, seconds=59))
    after = derive("razorpay", events, T0 + timedelta(minutes=30))

    assert (before.status, before.changes_at) == ("pending", T0 + timedelta(minutes=30))
    assert (after.status, after.changes_at) == ("abandoned", None)


def test_an_uncaptured_razorpay_authorisation_is_refunded_after_three_days() -> None:
    events = [Event("authorized", T0)]

    before = derive("razorpay", events, T0 + timedelta(days=2, hours=23))
    after = derive("razorpay", events, T0 + timedelta(days=3))

    assert (before.status, before.changes_at) == ("authorized", T0 + timedelta(days=3))
    assert after.status == "auto_refunded"


def test_a_tenant_can_set_its_own_periods() -> None:
    rules = StatusRules.from_policy(
        {"status": {"stale_after_minutes": 10, "capture_window_minutes": 60}}
    )

    assert derive("payu", [Event("initiated", T0)], T0 + timedelta(minutes=10), rules).status == (
        "abandoned"
    )
    assert derive("razorpay", [Event("authorized", T0)], T0 + timedelta(hours=1), rules).status == (
        "auto_refunded"
    )
    assert StatusRules.from_policy({}) == StatusRules()
    assert StatusRules.from_policy({"status": {"stale_after_minutes": -5}}) == StatusRules()


def test_a_status_with_no_clock_rule_never_changes_on_its_own() -> None:
    result = derive("payu", [Event("pending", T0)], T0 + timedelta(days=365))

    assert (result.status, result.changes_at) == ("pending", None)


# --- History -------------------------------------------------------------------


def test_the_latest_report_decides_whatever_order_the_events_arrive_in() -> None:
    pending = Event("pending", T0)
    failed = Event("failed", T0 + timedelta(days=2), "failure", "E303")

    for events in ([pending, failed], [failed, pending]):
        result = derive("payu", events, LATER)
        assert result.status == "failed"
        assert result.failure_reason == "bank_declined"
        assert result.provider_status == "failed"
        assert result.last_event_at == T0 + timedelta(days=2)


def test_a_late_reversal_of_a_success_changes_the_status() -> None:
    events = [Event("SUCCESS", T0), Event("CANCELLED", T0 + timedelta(hours=6))]

    assert derive("cashfree", events, LATER).status == "auto_refunded"


def test_a_report_still_in_progress_never_undoes_a_final_one() -> None:
    # A snapshot carries the payment's creation time, so the webhook looks later.
    events = [Event("captured", T0), Event("authorized", T0 + timedelta(seconds=2))]

    assert derive("razorpay", events, LATER).status == "succeeded"


def test_of_two_reports_at_the_same_time_the_later_given_wins() -> None:
    assert derive("payu", [Event("failed", T0), Event("captured", T0)], LATER).status == "succeeded"


def test_an_attempt_with_no_events_has_no_status() -> None:
    with pytest.raises(ValueError, match="no events"):
        derive("payu", [], LATER)


# --- Failure reasons -----------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "code", "reason"),
    [
        ("razorpay", "gateway_timeout", "technical_timeout"),
        ("razorpay", "insufficient_funds", "insufficient_funds"),
        ("razorpay", "payment_declined", "bank_declined"),
        ("razorpay", "authentication_failed", "auth_failed"),
        ("razorpay", "payment_cancelled", "auth_abandoned"),
        ("razorpay", "payment_risk_check_failed", "risk_blocked"),
        ("payu", "E308", "technical_timeout"),
        ("payu", "E312", "insufficient_funds"),
        ("payu", "E303", "bank_declined"),
        ("payu", "E311", "auth_failed"),
        ("payu", "E1605", "auth_abandoned"),
        ("payu", "E505", "risk_blocked"),
        ("payu", "E1603", "user_cancelled"),
        ("cashfree", "bank_declined", "bank_declined"),
        ("cashfree", "risk_blocked", "risk_blocked"),
        ("payu", "E999", "unknown"),
        ("razorpay", None, "unknown"),
    ],
)
def test_failure_reasons(provider: str, code: str | None, reason: str) -> None:
    word = {"razorpay": "failed", "payu": "failed", "cashfree": "FAILED"}[provider]

    assert derive(provider, [Event(word, T0, None, code)], LATER).failure_reason == reason


def test_every_mapped_reason_is_in_the_canonical_list() -> None:
    mapped = {reason for codes in s.REASON_MAP.values() for reason in codes.values()}

    assert mapped <= set(s.FAILURE_REASONS)
    assert set(s.FAILURE_REASONS) == {
        "bank_declined",
        "insufficient_funds",
        "auth_failed",
        "auth_abandoned",
        "technical_timeout",
        "risk_blocked",
        "user_cancelled",
        "unknown",
    }


def test_only_a_failed_attempt_has_a_failure_reason() -> None:
    assert derive("payu", [Event("captured", T0, None, "E303")], LATER).failure_reason is None


def test_the_rules_carry_a_version() -> None:
    assert RULES_VERSION == "1"
