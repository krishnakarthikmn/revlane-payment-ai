"""What the synthetic merchant looks like. See docs/synthetic-merchant.md."""

from dataclasses import dataclass
from datetime import date

from revlane.money import bps_of

DEFAULT_SEED = 42
PERIOD_DAYS = 90
ATTEMPTS_PER_DAY = 4_000
MARKETPLACE_ORDERS_PER_DAY = 40
TAX_BPS = 1_800

PROVIDERS = (("razorpay", 52), ("payu", 33), ("cashfree", 15))
METHODS = (("upi", 55), ("card", 35), ("netbanking", 8), ("wallet", 2))
CARD_NETWORKS = (("visa", 45), ("mastercard", 35), ("rupay", 15), ("amex", 5))
BANKS = (("HDFC", 28), ("ICICI", 22), ("SBI", 20), ("AXIS", 14), ("KOTAK", 9), ("YES", 7))
UPI_APPS = (("gpay", 40), ("phonepe", 38), ("paytm", 15), ("bhim", 7))
WALLETS = ("paytm", "mobikwik", "amazonpay")
# Booking amounts in whole rupees: (low, high, weight). The mean is about 10,500.
AMOUNT_BANDS = ((1_500, 5_000, 35), (5_000, 12_000, 42), (12_000, 30_000, 20), (30_000, 80_000, 3))
# Hour of day, weighted towards the afternoon and evening.
HOURS = tuple(
    (hour, weight)
    for hour, weight in zip(
        range(7, 24), (2, 4, 6, 7, 8, 8, 8, 9, 9, 8, 7, 7, 8, 9, 8, 5, 3), strict=True
    )
)

OUTCOMES = (("succeeded", 86), ("failed", 9), ("abandoned", 5))
FAILURE_REASONS = {
    "upi": (("technical_timeout", 40), ("insufficient_funds", 30), ("bank_declined", 30)),
    "card": (("auth_failed", 45), ("bank_declined", 35), ("risk_blocked", 20)),
    "netbanking": (("auth_abandoned", 50), ("bank_declined", 50)),
    "wallet": (("insufficient_funds", 70), ("technical_timeout", 30)),
}
MERCHANT_REFUND_PER_MILLE = 15

# Contract rates in basis points. "card" covers every network except Amex.
CONTRACT_RATES: dict[str, dict[str, int]] = {
    "razorpay": {"upi": 0, "card": 192, "amex": 250, "netbanking": 150, "wallet": 180},
    "payu": {"upi": 0, "card": 186, "amex": 250, "netbanking": 150, "wallet": 180},
    "cashfree": {"upi": 0, "card": 197, "amex": 200, "netbanking": 150, "wallet": 180},
}
MARKETPLACE_COMMISSION_BPS = 1_200


def contract_rate(provider: str, method: str, network: str | None) -> int:
    key = "amex" if method == "card" and network == "amex" else method
    return CONTRACT_RATES[provider][key]


# --- Planted leaks. Amounts are fixed by the prototype's screens. -------------

LAKH = 100_000 * 100  # in paise

R1_COUNT, R1_TOTAL = 40, 130_000 * 100
R2_SHORTFALLS = (35_000 * 100, 40_000 * 100, 45_000 * 100)  # oldest first; 1,20,000 in all
R3_COUNT, R3_TOTAL = 22, 60_000 * 100

F1_COUNT, F1_TOTAL = 1_000, 160_000 * 100
F1_CHARGED_BPS = 215  # against a 2.00% contract
F2_COUNT, F2_TOTAL = 800, 60_000 * 100
F3_COUNT, F3_TOTAL = 112, 40_000 * 100
F3_FEE_TYPE = "instant_settlement"

RECONCILIATION_TOTAL = R1_TOTAL + sum(R2_SHORTFALLS) + R3_TOTAL
FEE_AUDIT_TOTAL = F1_TOTAL + F2_TOTAL + F3_TOTAL
FEE_AUDIT_LINES = F1_COUNT + F2_COUNT + F3_COUNT

T3_COUNT, T4_COUNT, T5_COUNT = 10, 8, 5
T7_STATUSES = (
    ("razorpay", "under_review"),
    ("razorpay", "under_review"),
    ("payu", "onHold"),
    ("payu", "onHold"),
    ("cashfree", "INCOMPLETE"),
)


def with_tax(fee_minor: int) -> int:
    return fee_minor + bps_of(fee_minor, TAX_BPS)


def f1_overcharge(gross_minor: int) -> int:
    """What Cashfree took above contract on one Amex payment, fee and tax together."""
    charged = with_tax(bps_of(gross_minor, F1_CHARGED_BPS))
    agreed = with_tax(bps_of(gross_minor, CONTRACT_RATES["cashfree"]["amex"]))
    return charged - agreed


def f2_tax_kept(gross_minor: int) -> int:
    """The tax PayU kept on the fee of one refunded card payment."""
    return bps_of(bps_of(gross_minor, CONTRACT_RATES["payu"]["card"]), TAX_BPS)


@dataclass(frozen=True)
class Calendar:
    """The 90 days ending on the generation date, and the month the fee audit covers."""

    as_of: date
    days: tuple[date, ...]
    fee_month_first: int  # index into `days`
    fee_month_last: int

    @property
    def last(self) -> int:
        return len(self.days) - 1

    @property
    def fee_month(self) -> str:
        return self.days[self.fee_month_first].strftime("%Y-%m")


def calendar_for(as_of: date) -> Calendar:
    days = tuple(
        date.fromordinal(as_of.toordinal() - offset) for offset in range(PERIOD_DAYS - 1, -1, -1)
    )
    # The fee audit covers the last full calendar month before the generation date.
    month_last = date(as_of.year, as_of.month, 1).toordinal() - 1
    last = date.fromordinal(month_last)
    first = date(last.year, last.month, 1)
    return Calendar(as_of, days, days.index(first), days.index(last))
