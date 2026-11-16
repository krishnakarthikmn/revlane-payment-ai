"""`make audit`: run the matching and fee engines for the demo tenant."""

import argparse
from datetime import date

from sqlalchemy import select

from revlane.audit.run import AuditReport, run_audit
from revlane.config import get_settings
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.money import to_decimal_string


def rupees(amount_minor: int) -> str:
    return f"₹{to_decimal_string(amount_minor)}"


def show(report: AuditReport, findings: list[m.Finding]) -> None:
    links = ", ".join(f"{count:,} by {rule}" for rule, count in report.links.items())
    print(f"Audit as of {report.as_of.isoformat()}")
    print(f"  links: {links or 'none'}; new this run: {report.links_added:,}")
    print(f"  links made without a reference, for review: {report.review_links}")
    print(
        f"  findings: {report.findings_created} new, {report.findings_updated} updated, "
        f"{report.findings_moved} moved"
    )
    print(f"  fee lines checked: {report.fee_lines_checked:,}")
    for reason, count in report.fee_lines_unchecked.items():
        print(f"    not checked ({reason}): {count:,}")
    if report.unmatched_settlements:
        print(f"  settlements with no bank credit: {len(report.unmatched_settlements)}")
    print(f"  bank credits no settlement explains: {len(report.unmatched_credits)}")
    print("Findings")
    for finding in findings:
        records = len(finding.evidence.get("items", []))
        print(
            f"  {finding.type} · {finding.provider} · {finding.period_start:%b %Y} · "
            f"{finding.state}: {rupees(finding.amount_minor)} over {records:,} records"
        )
    print("Waiting to be claimed")
    for family, amount in report.totals_minor.items():
        print(f"  {family}: {rupees(amount)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m revlane.audit", description=__doc__)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="treat this date as today (default: today in India)",
    )
    args = parser.parse_args(argv)

    tenant_id = get_settings().demo_tenant_id
    report = run_audit(tenant_id, as_of=args.as_of)
    with tenant_session(tenant_id) as session:
        findings = list(
            session.scalars(select(m.Finding).order_by(m.Finding.type, m.Finding.period_start))
        )
    show(report, findings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
