"""The canonical status mapping: docs/technical-design.md, section 4.

Each provider's status words, checked against its documentation on
16 November 2026 and pinned by `tests/test_status_contract.py`:

- PayU: https://docs.payu.in/docs/payment-state-explanations
- Razorpay: https://razorpay.com/docs/payments/payments.md
- Cashfree: https://www.cashfree.com/docs/api-reference/payments/enums.md

Anything not listed here maps to `unknown` and raises an alert.
"""

SUCCEEDED = "succeeded"
AUTHORIZED = "authorized"
FAILED = "failed"
PENDING = "pending"
ABANDONED = "abandoned"
AUTO_REFUNDED = "auto_refunded"
HELD_FOR_REVIEW = "held_for_review"
UNKNOWN = "unknown"

# Clock rules: a status that changes with time and no new event.
STALE = "stale"  # nothing happened for the stale period (default 30 minutes)
CAPTURE_WINDOW = "capture_window"  # authorised and not captured in time

# Razorpay reports `refunded` both for a captured payment the merchant refunded
# and for one it refunded itself because it was never captured. The event's
# stage tells them apart.
NOT_CAPTURED = "not_captured"
CAPTURED = "captured"

STATUS_MAP: dict[str, dict[str, str]] = {
    # Keyed on `unmappedstatus`.
    "payu": {
        "captured": SUCCEEDED,
        "auth": AUTHORIZED,
        "failed": FAILED,
        "dropped": FAILED,
        "pending": PENDING,
        "in progress": PENDING,
        "initiated": PENDING,
        "bounced": ABANDONED,
        "userCancelled": ABANDONED,
        "autoRefund": AUTO_REFUNDED,
    },
    "razorpay": {
        "captured": SUCCEEDED,
        "authorized": AUTHORIZED,
        "failed": FAILED,
        "created": PENDING,
        # Not in the design's table. See `canonical_status` for the uncaptured case.
        "refunded": SUCCEEDED,
    },
    # Keyed on `payment_status`.
    "cashfree": {
        "SUCCESS": SUCCEEDED,
        "FAILED": FAILED,
        "PENDING": PENDING,
        "NOT_ATTEMPTED": PENDING,
        "USER_DROPPED": ABANDONED,
        # Cashfree: reversed automatically because success arrived after the time limit.
        "CANCELLED": AUTO_REFUNDED,
        "FLAGGED": HELD_FOR_REVIEW,
    },
}

# (provider, provider status) -> (clock rule, what the status becomes).
CLOCK_RULES: dict[tuple[str, str], tuple[str, str]] = {
    ("payu", "initiated"): (STALE, ABANDONED),
    ("razorpay", "created"): (STALE, ABANDONED),
    ("razorpay", "authorized"): (CAPTURE_WINDOW, AUTO_REFUNDED),
}


def canonical_status(provider: str, provider_status: str, stage: str | None = None) -> str:
    """The canonical status of one event, before any clock rule."""
    if provider == "razorpay" and provider_status == "refunded" and stage == NOT_CAPTURED:
        return AUTO_REFUNDED
    return STATUS_MAP.get(provider, {}).get(provider_status, UNKNOWN)


# --- Failure reasons, mapped from each provider's error code ---------------------

BANK_DECLINED = "bank_declined"
INSUFFICIENT_FUNDS = "insufficient_funds"
AUTH_FAILED = "auth_failed"
AUTH_ABANDONED = "auth_abandoned"
TECHNICAL_TIMEOUT = "technical_timeout"
RISK_BLOCKED = "risk_blocked"
USER_CANCELLED = "user_cancelled"
REASON_UNKNOWN = "unknown"

FAILURE_REASONS = (
    BANK_DECLINED,
    INSUFFICIENT_FUNDS,
    AUTH_FAILED,
    AUTH_ABANDONED,
    TECHNICAL_TIMEOUT,
    RISK_BLOCKED,
    USER_CANCELLED,
    REASON_UNKNOWN,
)

REASON_MAP: dict[str, dict[str, str]] = {
    # Keyed on `error_reason`.
    "razorpay": {
        "gateway_timeout": TECHNICAL_TIMEOUT,
        "insufficient_funds": INSUFFICIENT_FUNDS,
        "payment_declined": BANK_DECLINED,
        "authentication_failed": AUTH_FAILED,
        "payment_cancelled": AUTH_ABANDONED,
        "payment_risk_check_failed": RISK_BLOCKED,
    },
    # Keyed on `error`.
    "payu": {
        "E308": TECHNICAL_TIMEOUT,
        "E312": INSUFFICIENT_FUNDS,
        "E303": BANK_DECLINED,
        "E311": AUTH_FAILED,
        "E1605": AUTH_ABANDONED,
        "E505": RISK_BLOCKED,
        "E1603": USER_CANCELLED,
    },
    # Keyed on `error_details.error_code`.
    "cashfree": {reason: reason for reason in FAILURE_REASONS},
}


def failure_reason(provider: str, error_code: str | None) -> str:
    if error_code is None:
        return REASON_UNKNOWN
    return REASON_MAP.get(provider, {}).get(error_code, REASON_UNKNOWN)
