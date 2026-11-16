"""Money is integer minor units (paise). Never floats.

These helpers are shared by the synthetic merchant and the engines, so the
generator and the code that audits its output round in exactly the same way.
"""

from decimal import Decimal, InvalidOperation

MINOR_PER_UNIT = 100
BPS = 10_000


def bps_of(amount_minor: int, rate_bps: int) -> int:
    """`rate_bps` basis points of an amount, rounded half away from zero."""
    scaled = abs(amount_minor) * rate_bps
    rounded = (scaled + BPS // 2) // BPS
    return rounded if amount_minor >= 0 else -rounded


def to_decimal_string(amount_minor: int) -> str:
    """Paise as a rupee string with two decimals, the way provider files write it."""
    sign = "-" if amount_minor < 0 else ""
    whole, paise = divmod(abs(amount_minor), MINOR_PER_UNIT)
    return f"{sign}{whole}.{paise:02d}"


def from_decimal_string(value: str) -> int:
    """A provider's rupee string as paise. Refuses anything finer than a paisa."""
    try:
        minor = Decimal(value.strip()) * MINOR_PER_UNIT
    except InvalidOperation as error:
        raise ValueError(f"Not an amount: {value!r}") from error
    if minor != minor.to_integral_value():
        raise ValueError(f"Finer than one paisa: {value!r}")
    return int(minor)


LAKH_MINOR = 100_000 * MINOR_PER_UNIT
CRORE_MINOR = 100 * LAKH_MINOR


def group_indian(value: int) -> str:
    """Digits grouped the Indian way: 1,23,45,678."""
    digits = str(abs(value))
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ("-" if value < 0 else "") + ",".join([*groups, tail])


def _one_decimal(amount_minor: int, unit_minor: int) -> str:
    tenths = (amount_minor * 10 + unit_minor // 2) // unit_minor
    return f"{tenths // 10}.{tenths % 10}"


def format_inr(amount_minor: int) -> str:
    """Paise for display, as the console shows them: ₹46,200 under a lakh,
    ₹2.6L from a lakh, ₹4.2 Cr from a crore."""
    sign = "−" if amount_minor < 0 else ""
    amount = abs(amount_minor)
    if amount >= CRORE_MINOR:
        return f"{sign}₹{_one_decimal(amount, CRORE_MINOR)} Cr"
    if amount >= LAKH_MINOR:
        return f"{sign}₹{_one_decimal(amount, LAKH_MINOR)}L"
    whole, paise = divmod(amount, MINOR_PER_UNIT)
    return f"{sign}₹{group_indian(whole)}" + (f".{paise:02d}" if paise else "")


def format_inr_exact(amount_minor: int) -> str:
    """Paise in full, to the paisa, for a claim: 1,60,000.00."""
    whole, paise = divmod(abs(amount_minor), MINOR_PER_UNIT)
    return f"{'-' if amount_minor < 0 else ''}{group_indian(whole)}.{paise:02d}"
