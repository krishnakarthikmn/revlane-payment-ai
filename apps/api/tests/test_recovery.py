"""The backend half of the Milestone 7 flow: audit, approve the settlement
query, a later bank file brings the money, and the finding is recovered."""

from datetime import datetime

import pytest
from sqlalchemy import select

from revlane.audit.run import run_audit
from revlane.connectors.base import IST
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.harness import approvals, queue
from revlane.ingest.pipeline import ingest_text
from revlane.llm.stub import StubClient
from tests.merchant import AS_OF, load_merchant

pytestmark = pytest.mark.usefixtures("database")

HEADER = "Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance\n"


def test_a_later_bank_file_with_the_claimed_money_moves_the_finding_to_recovered(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    merchant = load_merchant(tmp_path_factory.mktemp("recovery"), "recovery")
    tenant_id = merchant.tenant_id
    queue.run_now(tenant_id, "leakage_audit", StubClient(), as_of=AS_OF)
    queue.run_now(tenant_id, "reconciliation", StubClient(), as_of=AS_OF)
    with tenant_session(tenant_id) as session:
        user = m.AppUser(tenant_id=tenant_id, email="p@example.com", name="P", role="approver")
        session.add(user)
        short = session.scalar(select(m.Finding).where(m.Finding.type == "settled_short"))
        assert short is not None and short.amount_minor == 12_000_000
        proposal = session.scalar(
            select(m.Proposal).where(
                m.Proposal.playbook == "reconciliation",
                m.Proposal.finding_ids.contains([short.id]),
            )
        )
        assert proposal is not None
        session.flush()
        approvals.decide(session, tenant_id, proposal.id, user.id, "approved")

    # Nothing has arrived yet: another run leaves the claim standing.
    waiting = run_audit(tenant_id, as_of=AS_OF)
    assert waiting.findings_recovered == 0

    today = datetime.now(tz=IST).strftime("%d/%m/%y")
    statement = (
        HEADER + f"{today},NEFT CR-HDFC0000240-TRIPBAZAAR MARKETPLACE-SHORTFALL,UTRLATER1,{today},,"
        "120000.00,9999999.00\n"
        + f"{today},NEFT CR-SBIN0000691-SOMEONE ELSE,UTRLATER2,{today},,119999.00,9999999.00\n"
    )
    report = ingest_text(tenant_id, "bank", "hdfc_later.csv", statement.splitlines(keepends=True))
    assert report.added["bank_credit"] == 2

    after = run_audit(tenant_id, as_of=datetime.now(tz=IST).date())

    assert after.findings_recovered == 1
    with tenant_session(tenant_id) as session:
        finding = session.get(m.Finding, short.id)
        claim = session.scalar(select(m.Claim).where(m.Claim.proposal_id == proposal.id))
        events = [
            (e.from_state, e.to_state, e.actor, e.reason)
            for e in session.scalars(
                select(m.FindingEvent)
                .where(m.FindingEvent.finding_id == short.id)
                .order_by(m.FindingEvent.occurred_at, m.FindingEvent.id)
            )
        ]
        link = session.scalar(select(m.MatchLink).where(m.MatchLink.left_type == "finding"))
    assert finding is not None and finding.state == "recovered"
    assert claim is not None and claim.status == "paid"
    assert events[-1] == (
        "claimed",
        "recovered",
        "engine",
        "A bank credit for the claimed amount has arrived.",
    )
    assert link is not None and (link.left_id, link.rule) == (str(short.id), "claim_amount")
    # Running again changes nothing, and the shortfall is not reported a second time.
    again = run_audit(tenant_id, as_of=datetime.now(tz=IST).date())
    assert (again.findings_recovered, again.findings_created, again.findings_moved) == (0, 0, 0)
