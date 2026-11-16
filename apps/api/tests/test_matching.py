"""The three-way match, on fixtures small enough to check by hand.

Every amount below is in paise. Dates are in November 2026: the 13th is a
Friday, the 14th and 15th the weekend, the 16th a Monday.
"""

from datetime import date
from decimal import Decimal

from revlane.engine import matching as mt
from revlane.engine.records import (
    Attempt,
    BankCredit,
    Line,
    Link,
    Order,
    Refund,
    Settlement,
)


def NOV(day: int) -> date:
    return date(2026, 11, day)


NOV_10 = date(2026, 11, 10)


def order(order_id: str, status: str = "confirmed", amount: int = 500_000) -> Order:
    return Order(order_id, amount, status, NOV(10))


def attempt(
    attempt_id: str,
    order_id: str | None = "BK-1",
    *,
    provider: str = "razorpay",
    status: str = "succeeded",
    amount: int = 500_000,
    paid_on: date = NOV_10,
) -> Attempt:
    return Attempt(provider, attempt_id, order_id, amount, status, paid_on, "upi", None, None)


def capture(
    attempt_id: str, settlement_id: str = "S1", *, provider: str = "razorpay", gross: int = 500_000
) -> Line:
    return Line(
        provider, settlement_id, attempt_id, attempt_id, "capture", gross, 0, 0, gross, "payment"
    )


def refund_line(
    ref: str, attempt_id: str, settlement_id: str, *, provider: str = "razorpay", gross: int
) -> Line:
    return Line(provider, settlement_id, ref, attempt_id, "refund", gross, 0, 0, gross, "payment")


def settlement(
    settlement_id: str,
    net: int,
    settled_on: date,
    *,
    provider: str = "razorpay",
    utr: str | None = None,
) -> Settlement:
    return Settlement(provider, settlement_id, net, utr, settled_on)


def credit(credit_id: str, amount: int, credited_on: date, utr: str | None = None) -> BankCredit:
    return BankCredit(credit_id, utr, amount, credited_on)


def pairs(links: list[Link]) -> set[tuple[str, str, str]]:
    return {(link.left_id, link.right_id, link.rule) for link in links}


# --- Step 1: order to attempt ---------------------------------------------------


def test_an_attempt_links_to_its_order_on_the_merchant_order_id() -> None:
    links = mt.link_orders([order("BK-1")], [attempt("pay_1"), attempt("pay_2", "BK-404")])

    assert pairs(links) == {("BK-1", "razorpay:pay_1", "order_id")}
    assert (links[0].left_type, links[0].right_type) == ("order", "payment_attempt")
    assert (links[0].amount_minor, links[0].confidence) == (500_000, Decimal(1))


def test_a_payment_refunded_by_the_provider_on_an_unconfirmed_order_is_a_finding() -> None:
    orders = [order("BK-1", "unconfirmed"), order("BK-2", "cancelled"), order("BK-3")]
    attempts = [
        attempt("pay_1", "BK-1", status="auto_refunded", amount=325_000),
        attempt("pay_2", "BK-2", status="auto_refunded", amount=100_000),
        # The order went through anyway: nothing was lost.
        attempt("pay_3", "BK-3", status="auto_refunded", amount=900_000),
        # A refund the merchant made is not a leak.
        attempt("pay_4", "BK-2", status="succeeded", amount=100_000),
    ]
    refunds = [
        Refund("razorpay", "rfnd_1", "pay_1", "automatic", 325_000, NOV(10)),
        Refund("razorpay", "rfnd_4", "pay_4", "merchant", 100_000, NOV(11)),
    ]

    items = mt.find_auto_refunded(orders, attempts, refunds, mt.MatchRules())

    assert [(item.id, item.amount_minor) for item in items] == [
        ("pay_1", 325_000),
        ("pay_2", 100_000),
    ]
    assert items[0].records == (
        ("orders", "BK-1"),
        ("attempts", "pay_1"),
        ("refunds", "rfnd_1"),
    )
    assert items[1].records == (("orders", "BK-2"), ("attempts", "pay_2"))


def test_a_failed_attempt_then_a_successful_retry_is_one_order_and_no_finding() -> None:
    orders = [order("BK-1")]
    attempts = [attempt("pay_1", status="failed"), attempt("pay_2")]

    result = mt.three_way_match(
        orders=orders,
        attempts=attempts,
        refunds=[],
        settlements=[settlement("S1", 500_000, NOV(11), utr="U1")],
        lines=[capture("pay_2")],
        credits=[credit("c1", 500_000, NOV(11), "U1")],
        as_of=NOV(16),
    )

    assert result.findings == []
    assert {link.right_id for link in result.links if link.left_type == "order"} == {
        "razorpay:pay_1",
        "razorpay:pay_2",
    }


# --- Step 2: attempt to settlement line -------------------------------------------


def test_a_settlement_is_due_after_the_cycle_and_two_working_days() -> None:
    rules = mt.MatchRules()

    # Paid Tuesday the 10th: cycle ends Wednesday, grace runs Thursday and Friday.
    assert mt.settlement_due(NOV(10), "payu", rules) == NOV(13)
    # Paid Friday the 13th: cycle ends Saturday, grace skips the weekend.
    assert mt.settlement_due(NOV(13), "payu", rules) == NOV(17)
    # A provider with no known cycle has no due date.
    assert mt.settlement_due(NOV(10), "marketplace", rules) is None


def test_a_captured_payment_with_no_settlement_line_after_the_grace_period_is_a_finding() -> None:
    attempts = [
        attempt("a_settled", provider="payu"),
        attempt("a_overdue", provider="payu", amount=319_400),
        attempt("a_recent", provider="payu", paid_on=NOV(13)),
        attempt("a_failed", provider="payu", status="failed"),
        attempt("a_refunded", provider="payu", status="auto_refunded"),
    ]
    lines = [capture("a_settled", provider="payu")]

    links = mt.link_settlement_lines(attempts, lines)
    items = mt.find_unsettled(attempts, lines, NOV(16), mt.MatchRules())

    assert pairs(links) == {("payu:a_settled", "payu:S1/a_settled", "payment_id")}
    assert [(item.id, item.amount_minor, item.on) for item in items] == [
        ("a_overdue", 319_400, NOV(10))
    ]
    assert items[0].records == (("orders", "BK-1"), ("attempts", "a_overdue"))
    # On the due date itself the payment is not yet late.
    assert mt.find_unsettled(attempts, lines, NOV(13), mt.MatchRules()) == []


def test_a_refund_line_does_not_count_as_the_payment_settling() -> None:
    attempts = [attempt("a_1", provider="payu")]
    lines = [refund_line("R:1", "a_1", "S1", provider="payu", gross=-500_000)]

    assert [item.id for item in mt.find_unsettled(attempts, lines, NOV(16), mt.MatchRules())] == [
        "a_1"
    ]


# --- Step 3: settlement to bank credit ---------------------------------------------


def test_a_settlement_links_to_the_credit_with_its_utr() -> None:
    result = mt.match_bank(
        [settlement("S1", 700_000, NOV(10), utr="UTR1")],
        [credit("c1", 700_000, NOV(10), "UTR1"), credit("c2", 700_000, NOV(10), "UTR9")],
        {},
        NOV(16),
        mt.MatchRules(),
    )

    assert pairs(result.links) == {("razorpay:S1", "c1", "utr")}
    assert result.links[0].confidence == Decimal(1)
    assert result.unmatched_credits == ["c2"]
    assert result.short == []


def test_without_a_utr_the_amount_and_a_date_inside_the_window_decide() -> None:
    saturday = settlement("S1", 812_345, NOV(14))  # reaches the bank on Monday
    result = mt.match_bank(
        [saturday, settlement("S2", 812_345, NOV(9))],
        [credit("c1", 812_345, NOV(16))],
        {},
        NOV(16),
        mt.MatchRules(),
    )

    # S2 is seven days before the credit: outside the three-day window.
    assert pairs(result.links) == {("razorpay:S1", "c1", "amount_date")}
    assert result.links[0].confidence < Decimal(1)
    assert result.unmatched_settlements == ["razorpay:S2"]


def test_a_credit_before_its_settlement_or_a_paisa_off_does_not_match() -> None:
    result = mt.match_bank(
        [settlement("S1", 500_000, NOV(10)), settlement("S2", 300_000, NOV(10))],
        [credit("early", 500_000, NOV(9)), credit("off", 300_001, NOV(10))],
        {},
        NOV(16),
        mt.MatchRules(),
    )

    assert result.links == []
    assert result.unmatched_settlements == ["razorpay:S1", "razorpay:S2"]


def test_one_credit_covering_three_settlements_is_linked_as_a_group() -> None:
    three = [
        settlement("P1", 100_000, NOV(8), provider="payu"),
        settlement("P2", 250_000, NOV(9), provider="payu"),
        settlement("P3", 75_050, NOV(10), provider="payu"),
    ]
    other = settlement("R1", 425_050, NOV(2))  # same sum, but far outside the window
    result = mt.match_bank(
        [*three, other], [credit("bulk", 425_050, NOV(10))], {}, NOV(16), mt.MatchRules()
    )

    assert pairs(result.links) == {
        ("payu:P1", "bulk", "group_sum"),
        ("payu:P2", "bulk", "group_sum"),
        ("payu:P3", "bulk", "group_sum"),
    }
    assert sorted(link.amount_minor or 0 for link in result.links) == [75_050, 100_000, 250_000]
    assert result.unmatched_credits == []
    assert result.unmatched_settlements == ["razorpay:R1"]


def test_a_group_never_mixes_providers() -> None:
    result = mt.match_bank(
        [settlement("S1", 100_000, NOV(9)), settlement("P1", 200_000, NOV(9), provider="payu")],
        [credit("c1", 300_000, NOV(10))],
        {},
        NOV(16),
        mt.MatchRules(),
    )

    assert result.links == []


def test_a_group_may_be_off_by_a_rupee_per_thousand_lines_and_no_more() -> None:
    two = [settlement("S1", 100_000, NOV(9)), settlement("S2", 200_000, NOV(10))]
    few_lines = {"razorpay:S1": 400, "razorpay:S2": 600}  # 1,000 lines: one rupee
    many_lines = {"razorpay:S1": 400, "razorpay:S2": 601}  # 1,001 lines: two rupees
    rules = mt.MatchRules()

    def linked(amount: int, line_counts: dict[str, int]) -> int:
        result = mt.match_bank(two, [credit("c1", amount, NOV(10))], line_counts, NOV(16), rules)
        return len(result.links)

    assert linked(300_100, few_lines) == 2
    assert linked(300_101, few_lines) == 0
    assert linked(299_800, many_lines) == 2
    assert linked(299_799, many_lines) == 0


def test_a_settlement_still_inside_the_window_is_not_reported_as_unmatched() -> None:
    result = mt.match_bank([settlement("S1", 100, NOV(14))], [], {}, NOV(16), mt.MatchRules())

    assert result.unmatched_settlements == []
    assert mt.match_bank(
        [settlement("S1", 100, NOV(12))], [], {}, NOV(16), mt.MatchRules()
    ).unmatched_settlements == ["razorpay:S1"]


def test_a_credit_below_the_settlement_it_carries_the_utr_of_is_settled_short() -> None:
    result = mt.match_bank(
        [settlement("S1", 700_000, NOV(10), utr="UTR1")],
        [credit("c1", 650_000, NOV(10), "UTR1")],
        {},
        NOV(16),
        mt.MatchRules(),
    )

    assert pairs(result.links) == {("razorpay:S1", "c1", "utr")}
    assert [(item.id, item.amount_minor) for item in result.short] == [("S1", 50_000)]


def test_a_link_a_person_undid_is_not_made_again() -> None:
    result = mt.match_bank(
        [settlement("S1", 812_345, NOV(14))],
        [credit("c1", 812_345, NOV(16))],
        {},
        NOV(16),
        mt.MatchRules(),
        rejected=frozenset({("razorpay:S1", "c1")}),
    )

    assert result.links == []


# --- Step 4: payout to lines ---------------------------------------------------------


def test_a_payout_below_the_sum_of_its_lines_is_settled_short() -> None:
    payouts = [
        settlement("MKT-1", 6_500_000, NOV(8), provider="marketplace"),
        settlement("MKT-2", 3_000_000, NOV(15), provider="marketplace"),
    ]
    lines = [
        capture("BK-1", "MKT-1", provider="marketplace", gross=6_000_000),
        capture("BK-2", "MKT-1", provider="marketplace", gross=4_000_000),
        capture("BK-3", "MKT-2", provider="marketplace", gross=3_000_000),
    ]

    items = mt.find_short_payouts(payouts, lines, mt.MatchRules())

    assert [(item.id, item.amount_minor, item.on) for item in items] == [
        ("MKT-1", 3_500_000, NOV(8))
    ]
    assert items[0].records == (("settlements", "MKT-1"),)


def test_a_refund_netted_against_a_later_payout_is_matched_and_nothing_is_short() -> None:
    # The booking is paid out in S1; its refund comes out of S2 three days later.
    attempts = [
        attempt("cf_1", provider="cashfree"),
        attempt("cf_2", provider="cashfree", amount=800_000),
    ]
    refunds = [Refund("cashfree", "refund_1", "cf_1", "merchant", 500_000, NOV(12))]
    lines = [
        capture("cf_1", "S1", provider="cashfree"),
        capture("cf_2", "S2", provider="cashfree", gross=800_000),
        refund_line("REFUND:refund_1", "cf_1", "S2", provider="cashfree", gross=-500_000),
    ]
    settlements = [
        settlement("S1", 500_000, NOV(11), provider="cashfree", utr="U1"),
        settlement("S2", 300_000, NOV(14), provider="cashfree", utr="U2"),
    ]

    result = mt.three_way_match(
        orders=[order("BK-1", "cancelled")],
        attempts=attempts,
        refunds=refunds,
        settlements=settlements,
        lines=lines,
        credits=[credit("c1", 500_000, NOV(11), "U1"), credit("c2", 300_000, NOV(14), "U2")],
        as_of=NOV(16),
    )

    assert result.findings == []
    assert ("cashfree:refund_1", "cashfree:S2/REFUND:refund_1", "refund") in pairs(result.links)


def test_a_payout_may_differ_from_its_lines_by_the_tolerance() -> None:
    payout = settlement("MKT-1", 999_900, NOV(8), provider="marketplace")
    lines = [capture("BK-1", "MKT-1", provider="marketplace", gross=1_000_000)]

    assert mt.find_short_payouts([payout], lines, mt.MatchRules()) == []
    short = mt.find_short_payouts(
        [settlement("MKT-1", 999_899, NOV(8), provider="marketplace")], lines, mt.MatchRules()
    )
    assert [item.amount_minor for item in short] == [101]


# --- Residuals become findings ----------------------------------------------------------


def test_residuals_are_grouped_by_type_provider_and_month() -> None:
    result = mt.three_way_match(
        orders=[order("BK-1", "unconfirmed"), order("BK-2", "unconfirmed")],
        attempts=[
            attempt("pay_1", "BK-1", status="auto_refunded", amount=100_000),
            attempt("pay_2", "BK-2", status="auto_refunded", amount=50_000, paid_on=NOV(12)),
            attempt("pu_1", "BK-9", provider="payu", amount=70_000),
            attempt("pu_old", "BK-8", provider="payu", amount=30_000, paid_on=date(2026, 10, 20)),
        ],
        refunds=[],
        settlements=[],
        lines=[],
        credits=[],
        as_of=NOV(16),
    )

    found = {(f.type, f.provider, f.period_start): f for f in result.findings}
    assert set(found) == {
        ("paid_auto_refunded", "razorpay", NOV(1)),
        ("captured_not_settled", "payu", NOV(1)),
        ("captured_not_settled", "payu", date(2026, 10, 1)),
    }
    refunded = found["paid_auto_refunded", "razorpay", NOV(1)]
    assert refunded.amount_minor == 150_000
    assert refunded.period_end == NOV(30)
    assert refunded.cause == "auto_refunded_order_not_confirmed"
    assert refunded.rule_version == mt.RULES_VERSION
    assert refunded.evidence() == {
        "orders": ["BK-1", "BK-2"],
        "attempts": ["pay_1", "pay_2"],
        "items": [
            {"id": "pay_1", "amount_minor": 100_000},
            {"id": "pay_2", "amount_minor": 50_000},
        ],
    }


def test_rules_can_be_set_from_the_tenant_policy() -> None:
    rules = mt.MatchRules.from_policy(
        {"grace_working_days": 5, "bank_window_days": 1, "cycle_days": {"payu": 2}}
    )

    assert (rules.grace_working_days, rules.bank_window_days) == (5, 1)
    assert rules.cycle_days["payu"] == 2
    assert rules.cycle_days["razorpay"] == 1
    assert mt.MatchRules.from_policy({}) == mt.MatchRules()


# --- Recoveries ---------------------------------------------------------------------------


def test_a_claimed_amount_arriving_in_the_bank_is_a_recovery() -> None:
    claims = [
        mt.Claimed("f-old", 3_500_000, NOV(10)),
        mt.Claimed("f-new", 3_500_000, NOV(12)),
        mt.Claimed("f-other", 120_000, NOV(10)),
    ]
    credits = [
        credit("before-the-claim", 120_000, NOV(9)),
        credit("a-rupee-off", 120_100, NOV(13)),
        credit("paid-1", 3_500_000, NOV(13)),
        credit("paid-2", 3_500_000, NOV(14)),
        credit("counted-already", 120_000, NOV(15)),
    ]

    links = mt.match_recoveries(claims, credits, frozenset({"counted-already"}))

    # The oldest claim takes the first credit; one credit pays for one claim.
    assert pairs(links) == {
        ("f-old", "paid-1", "claim_amount"),
        ("f-new", "paid-2", "claim_amount"),
    }
    assert {(link.left_type, link.right_type) for link in links} == {("finding", "bank_credit")}
    assert all(link.confidence < 1 for link in links)
    assert mt.match_recoveries([], credits) == []
