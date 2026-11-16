"""The fee check, on fixtures small enough to check by hand. Amounts are paise.

The worked example throughout: a ₹10,000.00 card payment (1,000,000 paise).
At 2.00% the fee is ₹200.00 and GST at 18% is ₹36.00: ₹236.00 in all.
At 2.15% the fee is ₹215.00 and GST ₹38.70: ₹253.70. The difference is ₹17.70.
"""

from datetime import date

import pytest

from revlane.engine import fees
from revlane.engine.records import Attempt, Contract, Line, Refund, Settlement


def OCT(day: int) -> date:
    return date(2026, 10, day)


GROSS = 1_000_000
OCT_5 = date(2026, 10, 5)


def contract(
    provider: str = "cashfree",
    method: str = "card",
    network: str | None = None,
    *,
    card_type: str | None = None,
    rate_bps: int = 200,
    fixed: int = 0,
    tax_on_refunded_fee: bool = False,
    allowed: tuple[str, ...] = ("payment",),
    effective_from: date = date(2026, 1, 1),
    effective_to: date | None = None,
) -> Contract:
    return Contract(
        provider,
        method,
        network,
        card_type,
        rate_bps,
        fixed,
        1_800,
        tax_on_refunded_fee,
        frozenset(allowed),
        effective_from,
        effective_to,
    )


def card(attempt_id: str = "a1", network: str = "amex", paid_on: date = OCT_5) -> Attempt:
    return Attempt(
        "cashfree", attempt_id, "BK-1", GROSS, "succeeded", paid_on, "card", network, "credit"
    )


def capture(fee: int, tax: int, attempt_id: str = "a1", gross: int = GROSS) -> Line:
    return Line(
        "cashfree",
        "S1",
        f"PAYMENT:{attempt_id}",
        attempt_id,
        "capture",
        gross,
        fee,
        tax,
        gross - fee - tax,
        "payment",
    )


def refund(fee: int, tax: int, attempt_id: str = "a1") -> Line:
    return Line(
        "cashfree",
        "S1",
        "REFUND:r1",
        attempt_id,
        "refund",
        -GROSS,
        fee,
        tax,
        -GROSS - fee - tax,
        "payment",
    )


S1 = Settlement("cashfree", "S1", 0, None, OCT(6))


def check(
    lines: list[Line],
    contracts: list[Contract],
    attempts: list[Attempt] | None = None,
    refunds: list[Refund] | None = None,
    settlements: list[Settlement] | None = None,
) -> fees.FeeResult:
    return fees.check_fees(
        lines=lines,
        settlements=settlements or [S1],
        attempts=[card()] if attempts is None else attempts,
        refunds=refunds or [],
        contracts=contracts,
    )


# --- The expected fee ---------------------------------------------------------------


def test_the_expected_fee_is_rate_times_gross_plus_fixed_fee_plus_tax() -> None:
    assert fees.expected_fee(contract(rate_bps=200), GROSS) == (20_000, 3_600)
    # ₹3.00 fixed on top: fee ₹203.00, GST ₹36.54.
    assert fees.expected_fee(contract(rate_bps=200, fixed=300), GROSS) == (20_300, 3_654)
    # 1.86% of ₹3,333.00 is ₹61.9938: rounds to ₹61.99; GST ₹11.1582 rounds to ₹11.16.
    assert fees.expected_fee(contract(rate_bps=186), 333_300) == (6_199, 1_116)


def test_the_contract_in_force_on_the_transaction_date_is_used() -> None:
    old = contract(rate_bps=250, effective_to=date(2026, 9, 30))
    new = contract(rate_bps=200, effective_from=OCT(1))

    def pick(on: date) -> Contract | None:
        return fees.contract_for([old, new], "cashfree", "card", "visa", "credit", on)

    assert pick(date(2026, 9, 30)) is old
    assert pick(OCT(1)) is new
    assert fees.contract_for([old], "cashfree", "card", "visa", "credit", OCT(1)) is None
    assert fees.contract_for([new], "payu", "card", "visa", "credit", OCT(1)) is None
    assert fees.contract_for([new], "cashfree", "upi", None, None, OCT(1)) is None


def test_the_most_specific_contract_row_wins() -> None:
    any_card = contract(rate_bps=197)
    amex = contract(network="amex", rate_bps=200)
    amex_credit = contract(network="amex", card_type="credit", rate_bps=210)
    rows = [any_card, amex, amex_credit]

    def pick(network: str, card_type: str) -> Contract | None:
        return fees.contract_for(rows, "cashfree", "card", network, card_type, OCT(1))

    assert pick("visa", "credit") is any_card
    assert pick("amex", "debit") is amex
    assert pick("amex", "credit") is amex_credit


# --- Charged above contract ------------------------------------------------------------


def test_a_fee_at_the_contract_rate_is_no_difference() -> None:
    result = check([capture(20_000, 3_600)], [contract(network="amex")])

    assert result.differences == []
    assert (result.checked, result.unchecked) == (1, {})


def test_a_rate_above_contract_is_the_fee_and_tax_charged_over_the_fee_and_tax_agreed() -> None:
    result = check([capture(21_500, 3_870)], [contract(network="amex")])

    [difference] = result.differences
    assert difference.type == "fee_above_contract"
    assert difference.amount_minor == 1_770
    assert (difference.charged_minor, difference.expected_minor) == (25_370, 23_600)
    assert (difference.line_id, difference.attempt_id) == ("S1/PAYMENT:a1", "a1")
    assert difference.cause == "rate_above_contract:card:amex"
    assert difference.on == OCT(6)


def test_the_tolerance_is_one_rupee_or_five_basis_points_whichever_is_larger() -> None:
    amex = [contract(network="amex")]
    # On ₹10,000 five basis points is ₹5.00, which is larger than ₹1.
    assert check([capture(20_500, 3_600)], amex).differences == []
    assert [d.amount_minor for d in check([capture(20_501, 3_600)], amex).differences] == [501]
    # On ₹1,500 five basis points is ₹0.75, so ₹1 applies. Fee ₹30.00, GST ₹5.40.
    small = Attempt("cashfree", "a1", "BK-1", 150_000, "succeeded", OCT(5), "card", "amex", None)
    assert check([capture(3_100, 540, gross=150_000)], amex, attempts=[small]).differences == []
    assert [
        d.amount_minor
        for d in check([capture(3_101, 540, gross=150_000)], amex, attempts=[small]).differences
    ] == [101]


def test_a_fee_below_contract_is_not_a_finding() -> None:
    assert check([capture(15_000, 2_700)], [contract(network="amex")]).differences == []


def test_a_line_with_no_contract_or_no_payment_is_counted_as_unchecked() -> None:
    result = check(
        [capture(20_000, 3_600), capture(20_000, 3_600, attempt_id="gone")],
        [contract(method="upi")],
    )

    assert result.differences == []
    assert result.checked == 0
    assert result.unchecked == {"contract_missing": 1, "payment_missing": 1}


# --- Tax kept on a refunded fee ----------------------------------------------------------


def test_tax_kept_on_a_refunded_fee_is_a_finding_when_the_contract_says_it_comes_back() -> None:
    # The fee of ₹200.00 comes back; the ₹36.00 of GST on it does not.
    result = check(
        [refund(-20_000, 0)],
        [contract(network="amex")],
        refunds=[Refund("cashfree", "r1", "a1", "merchant", GROSS, OCT(5))],
    )

    [difference] = result.differences
    assert difference.type == "tax_on_refunded_fee"
    assert difference.amount_minor == 3_600
    assert difference.cause == "tax_kept_on_refunded_fee"
    assert (difference.line_id, difference.attempt_id, difference.refund_id) == (
        "S1/REFUND:r1",
        "a1",
        "r1",
    )


@pytest.mark.parametrize(
    ("fee", "tax", "keeps_tax"),
    [
        (-20_000, -3_600, False),  # fee and tax both returned
        (-20_000, 0, True),  # the contract lets the provider keep the tax
        (0, 0, False),  # nothing returned: no fee came back, so no tax is owed on it
    ],
)
def test_a_refund_that_follows_the_contract_is_no_difference(
    fee: int, tax: int, keeps_tax: bool
) -> None:
    result = check([refund(fee, tax)], [contract(network="amex", tax_on_refunded_fee=keeps_tax)])

    assert result.differences == []


# --- A fee the contract does not allow -----------------------------------------------------


def test_a_fee_type_the_contract_does_not_list_is_a_finding_for_fee_plus_tax() -> None:
    adjustment = Line(
        "cashfree", "S1", "adj_1", None, "fee_adjustment", 0, 32_376, 5_828, -38_204, "instant"
    )
    allowed = Line("cashfree", "S1", "adj_2", None, "fee_adjustment", 0, 100, 18, -118, "payment")

    result = check([adjustment, allowed], [contract(), contract(method="upi", rate_bps=0)])

    [difference] = result.differences
    assert difference.type == "fee_not_in_contract"
    assert difference.amount_minor == 38_204
    assert difference.cause == "fee_type:instant"
    assert (difference.line_id, difference.attempt_id) == ("S1/adj_1", None)


def test_a_marketplace_commission_is_checked_against_the_commission_contract() -> None:
    payout = Settlement("marketplace", "MKT-1", 0, None, OCT(6))
    commission = contract("marketplace", "commission", rate_bps=1_200, allowed=("commission",))

    def line(fee: int, tax: int) -> Line:
        return Line(
            "marketplace", "MKT-1", "BK-1", None, "capture", GROSS, fee, tax, 0, "commission"
        )

    # 12% of ₹10,000 is ₹1,200.00; GST ₹216.00.
    agreed = check([line(120_000, 21_600)], [commission], settlements=[payout])
    over = check([line(130_000, 23_400)], [commission], settlements=[payout])

    assert (agreed.checked, agreed.differences) == (1, [])
    assert [(d.type, d.amount_minor) for d in over.differences] == [("fee_above_contract", 11_800)]


# --- Claim-ready findings ---------------------------------------------------------------------


def test_differences_are_grouped_by_provider_cause_and_month_keeping_every_line() -> None:
    attempts = [card("a1"), card("a2"), card("a3", paid_on=date(2026, 9, 30))]
    september = Settlement("cashfree", "S0", 0, None, date(2026, 9, 30))
    lines = [
        capture(21_500, 3_870, "a1"),
        capture(21_500, 3_870, "a2"),
        Line("cashfree", "S0", "PAYMENT:a3", "a3", "capture", GROSS, 21_500, 3_870, 0, "payment"),
    ]

    result = check(
        lines, [contract(network="amex")], attempts=attempts, settlements=[S1, september]
    )

    by_month = {finding.period_start: finding for finding in result.findings}
    assert set(by_month) == {OCT(1), date(2026, 9, 1)}
    october = by_month[OCT(1)]
    assert (october.type, october.provider) == ("fee_above_contract", "cashfree")
    assert october.period_end == OCT(31)
    assert october.amount_minor == 3_540
    assert october.rule_version == fees.RULES_VERSION
    assert october.evidence() == {
        "settlement_lines": ["S1/PAYMENT:a1", "S1/PAYMENT:a2"],
        "attempts": ["a1", "a2"],
        "items": [
            {"id": "S1/PAYMENT:a1", "amount_minor": 1_770},
            {"id": "S1/PAYMENT:a2", "amount_minor": 1_770},
        ],
    }
    assert october.key == "fee_above_contract:cashfree:rate_above_contract:card:amex:2026-10"


def test_rules_can_be_set_from_the_tenant_policy() -> None:
    rules = fees.FeeRules.from_policy({"tolerance_minor": 500, "tolerance_bps": 10})

    assert (rules.tolerance_minor, rules.tolerance_bps) == (500, 10)
    assert fees.FeeRules.from_policy({}) == fees.FeeRules()
