"""Claim drafts: a PDF and an email text built from a finding's evidence.

Every word and every figure here is written by code from stored records. No
model text goes into a claim. Nothing is sent: the merchant downloads the
draft and sends it.
"""

from dataclasses import dataclass
from datetime import date

from revlane.db import models as m
from revlane.harness import pdf
from revlane.money import format_inr_exact, group_indian, to_decimal_string

PROVIDER_NAMES = {
    "razorpay": "Razorpay",
    "payu": "PayU",
    "cashfree": "Cashfree",
    "marketplace": "the marketplace",
}
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
# Subject, what happened, what is asked for, and what each row of the table is.
WORDING: dict[str, tuple[str, str, str, str]] = {
    "fee_above_contract": (
        "Fees charged above the contract rate",
        "the fee and tax on each settlement line listed are higher than our contract gives",
        "Please refund the difference, or tell us which term of the contract the charge rests on.",
        "Settlement line",
    ),
    "tax_on_refunded_fee": (
        "Tax kept on refunded fees",
        "the fee on each refunded payment listed was returned, but the tax on that fee was not, "
        "although our contract returns the tax with the fee",
        "Please refund the tax kept on these lines.",
        "Settlement line",
    ),
    "fee_not_in_contract": (
        "Fees not provided for in the contract",
        "each settlement line listed carries a fee of a type our contract does not allow",
        "Please reverse these fees.",
        "Settlement line",
    ),
    "settled_short": (
        "Payouts lower than their lines",
        "each payout listed was paid for less than the sum of its own lines",
        "Please pay the shortfall, or send the adjustment that explains it.",
        "Payout",
    ),
    "captured_not_settled": (
        "Captured payments not yet settled",
        "each payment listed was captured and has not appeared in any settlement, "
        "past the settlement cycle",
        "Please settle these payments, or tell us where they stand.",
        "Payment",
    ),
}
EMAIL_ROWS = 25


@dataclass(frozen=True, slots=True)
class ClaimDraft:
    merchant: str
    provider: str
    subject: str
    period: str
    amount_minor: int
    happened: str
    request: str
    row_label: str
    rows: tuple[tuple[str, int], ...]  # record id, amount in paise
    drafted_on: date

    @property
    def to(self) -> str:
        return PROVIDER_NAMES.get(self.provider, self.provider)


def month_label(day: date | None) -> str:
    return f"{MONTHS[day.month - 1]} {day.year}" if day else "the period reviewed"


def build(finding: m.Finding, merchant: str, drafted_on: date) -> ClaimDraft:
    if finding.type not in WORDING:
        raise ValueError(f"No claim can be drafted for a {finding.type} finding.")
    subject, happened, request, row_label = WORDING[finding.type]
    rows = tuple(
        (str(item["id"]), int(item["amount_minor"])) for item in finding.evidence.get("items", [])
    )
    return ClaimDraft(
        merchant=merchant,
        provider=finding.provider or "",
        subject=subject,
        period=month_label(finding.period_start),
        amount_minor=finding.amount_minor,
        happened=happened,
        request=request,
        row_label=row_label,
        rows=rows,
        drafted_on=drafted_on,
    )


def _opening(draft: ClaimDraft) -> list[str]:
    count = group_indian(len(draft.rows))
    return [
        f"We have reviewed our {draft.period} records with you against our own. "
        f"On {count} records, {draft.happened}.",
        f"The total is INR {format_inr_exact(draft.amount_minor)}. {draft.request}",
        "Every record and its amount is listed below so that you can check each one.",
    ]


def render_email(draft: ClaimDraft) -> str:
    """The claim as text to paste into an email. The person sends it; Revlane does not."""
    lines = [
        f"Subject: {draft.subject}, {draft.period}: INR {format_inr_exact(draft.amount_minor)}",
        "",
        f"To: {draft.to} merchant support",
        f"From: {draft.merchant}",
        "",
        "Hello,",
        "",
    ]
    for paragraph in _opening(draft):
        lines += [paragraph, ""]
    lines.append(f"{draft.row_label}, amount (INR)")
    lines += [
        f"{record}, {to_decimal_string(amount)}" for record, amount in draft.rows[:EMAIL_ROWS]
    ]
    if len(draft.rows) > EMAIL_ROWS:
        rest = group_indian(len(draft.rows) - EMAIL_ROWS)
        lines.append(f"... and {rest} more, all listed in the attached PDF.")
    lines += ["", "Regards,", draft.merchant, ""]
    return "\n".join(lines)


def render_pdf(draft: ClaimDraft) -> bytes:
    lines = [
        pdf.Line(draft.subject, "bold", pdf.SIZES["title"]),
        pdf.Line(""),
        pdf.Line(f"From: {draft.merchant}"),
        pdf.Line(f"To: {draft.to} merchant support"),
        pdf.Line(f"Period: {draft.period}"),
        pdf.Line(f"Drafted: {draft.drafted_on.isoformat()}"),
        pdf.Line(f"Amount claimed: INR {format_inr_exact(draft.amount_minor)}", "bold"),
        pdf.Line(""),
    ]
    for paragraph in _opening(draft):
        lines += [pdf.Line(text) for text in pdf.wrap(paragraph)]
        lines.append(pdf.Line(""))
    lines.append(pdf.Line(f"{draft.row_label:<64}{'Amount (INR)':>18}", "mono", pdf.SIZES["mono"]))
    for record, amount in draft.rows:
        first, *rest = pdf.wrap(record, 62)
        lines.append(
            pdf.Line(f"{first:<64}{format_inr_exact(amount):>18}", "mono", pdf.SIZES["mono"])
        )
        lines += [pdf.Line(part, "mono", pdf.SIZES["mono"]) for part in rest]
    lines.append(
        pdf.Line(
            f"{'Total':<64}{format_inr_exact(draft.amount_minor):>18}", "mono", pdf.SIZES["mono"]
        )
    )
    lines += [
        pdf.Line(""),
        pdf.Line("Drafted by Revlane for review. Not sent to anyone until the merchant sends it."),
    ]
    return pdf.render(lines)


def render_reoffer_csv(findings: list[m.Finding]) -> str:
    """Bookings that were paid and refunded automatically, to offer again."""
    rows = ["order_id,payment_id,amount_inr"]
    for finding in findings:
        orders = [str(order) for order in finding.evidence.get("orders", [])]
        for index, item in enumerate(finding.evidence.get("items", [])):
            order_id = orders[index] if index < len(orders) else ""
            rows.append(f"{order_id},{item['id']},{to_decimal_string(int(item['amount_minor']))}")
    return "\n".join(rows) + "\n"


def render_report_pdf(merchant: str, as_of: date, findings: list[m.Finding]) -> bytes:
    """The leakage report: every open finding, by leak and provider."""
    total = sum(finding.amount_minor for finding in findings)
    lines = [
        pdf.Line("Leakage report", "bold", pdf.SIZES["title"]),
        pdf.Line(""),
        pdf.Line(f"{merchant}, as of {as_of.isoformat()}"),
        pdf.Line(f"Open findings: {len(findings)}, INR {format_inr_exact(total)} in all", "bold"),
        pdf.Line(""),
        pdf.Line(
            f"{'Leak':<28}{'Provider':<14}{'Period':<16}{'Records':>10}{'Amount (INR)':>18}",
            "mono",
            pdf.SIZES["mono"],
        ),
    ]
    for finding in sorted(findings, key=lambda f: (-f.amount_minor, f.type)):
        records = group_indian(len(finding.evidence.get("items", [])))
        lines.append(
            pdf.Line(
                f"{finding.type:<28}{finding.provider or '':<14}"
                f"{month_label(finding.period_start):<16}{records:>10}"
                f"{format_inr_exact(finding.amount_minor):>18}",
                "mono",
                pdf.SIZES["mono"],
            )
        )
    lines += [pdf.Line(""), pdf.Line("Drafted by Revlane for review. Nothing has been sent.")]
    return pdf.render(lines)
