"""Rule 6: no action without an approval. A test tries, and fails.

Also the two-person flag, who may approve, and what an approval produces: a
drafted claim (a PDF and an email text), with nothing sent.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from revlane.audit import store
from revlane.bootstrap import ensure_tenant
from revlane.db import models as m
from revlane.db.session import admin_session, tenant_session
from revlane.engine import findings as fd
from revlane.harness import actions, approvals, claims, pdf
from revlane.storage import get_storage

pytestmark = pytest.mark.usefixtures("database")

TODAY = date(2026, 11, 16)


@dataclass
class World:
    tenant_id: UUID
    priya: UUID
    arjun: UUID
    viewer: UUID

    def proposal(self, action_type: str, kind: str, provider: str, *amounts: int) -> UUID:
        """A finding of the given kind, and a pending proposal for it."""
        cause = uuid4().hex[:8]
        items = [
            fd.Item(
                f"{provider}-{cause}-{n}",
                amount,
                date(2026, 10, 12),
                (("settlement_lines", f"{provider}-{cause}-{n}"), ("orders", f"BK-{n}")),
            )
            for n, amount in enumerate(amounts)
        ]
        [detected] = fd.group(kind, provider, cause, items, "1")
        with tenant_session(self.tenant_id) as session:
            store.apply(session, self.tenant_id, [fd.Create(detected, 1)])
            finding_id = session.scalar(
                select(m.Finding.id).where(m.Finding.finding_key == detected.key)
            )
            proposal = m.Proposal(
                tenant_id=self.tenant_id,
                finding_ids=[finding_id],
                playbook="fee_audit",
                playbook_version=1,
                action_type=action_type,
                idempotency_key=cause,
            )
            session.add(proposal)
            session.flush()
            return proposal.id

    def decide(
        self, proposal_id: UUID, who: UUID, decision: str = "approved", reason: str | None = None
    ) -> approvals.Decision:
        with tenant_session(self.tenant_id) as session:
            return approvals.decide(
                session, self.tenant_id, proposal_id, who, decision, today=TODAY, reason=reason
            )

    def count(self, model: type[Any], proposal_id: UUID) -> int:
        with tenant_session(self.tenant_id) as session:
            return (
                session.scalar(
                    select(func.count()).select_from(model).where(model.proposal_id == proposal_id)
                )
                or 0
            )

    def finding(self, proposal_id: UUID) -> m.Finding:
        with tenant_session(self.tenant_id) as session:
            proposal = session.get(m.Proposal, proposal_id)
            assert proposal is not None
            finding = session.get(m.Finding, proposal.finding_ids[0])
            assert finding is not None
            return finding

    def status(self, proposal_id: UUID) -> str:
        with tenant_session(self.tenant_id) as session:
            proposal = session.get(m.Proposal, proposal_id)
            assert proposal is not None
            return proposal.status


def new_world(name: str, **policy: Any) -> World:  # noqa: ANN401
    tenant_id = uuid4()
    ensure_tenant(tenant_id, name)
    people = {}
    with tenant_session(tenant_id) as session:
        session.execute(update(m.Tenant).values(policy=policy))
        for person, role in (("priya", "approver"), ("arjun", "admin"), ("viewer", "viewer")):
            user = m.AppUser(
                tenant_id=tenant_id, email=f"{person}@example.com", name=person, role=role
            )
            session.add(user)
            session.flush()
            people[person] = user.id
    return World(tenant_id, **people)


@pytest.fixture(scope="module")
def world(database: None) -> World:
    return new_world("approvals")


@pytest.fixture(scope="module")
def two_person(database: None) -> World:
    return new_world("two-person", approvals={"two_person": True})


# --- No action without an approval --------------------------------------------------------


@pytest.mark.parametrize("table", ["action", "claim"])
def test_the_database_refuses_an_action_or_a_claim_for_an_unapproved_proposal(
    world: World, table: str
) -> None:
    proposal_id = world.proposal("draft_fee_claim", "fee_above_contract", "cashfree", 500_000)
    row = (
        m.Action(tenant_id=world.tenant_id, proposal_id=proposal_id, type="draft_fee_claim")
        if table == "action"
        else m.Claim(
            tenant_id=world.tenant_id, proposal_id=proposal_id, provider="cashfree", amount_minor=1
        )
    )

    # As the application, and as the schema owner: neither gets past the trigger.
    with pytest.raises(DBAPIError) as refused, tenant_session(world.tenant_id) as session:
        session.add(row)
        session.flush()
    with pytest.raises(DBAPIError) as refused_owner, admin_session() as session:
        session.add(row)
        session.flush()
    assert f"No {table} without approval" in str(refused.value)
    assert f"No {table} without approval" in str(refused_owner.value)
    assert world.count(m.Action, proposal_id) == world.count(m.Claim, proposal_id) == 0


def test_setting_a_proposal_to_approved_by_hand_does_not_get_past_the_gate(world: World) -> None:
    proposal_id = world.proposal("draft_fee_claim", "fee_above_contract", "cashfree", 500_000)

    with tenant_session(world.tenant_id) as session:
        proposal = session.get(m.Proposal, proposal_id)
        assert proposal is not None
        proposal.status = "approved"  # no approval row behind it
        session.flush()
        with pytest.raises(actions.ApprovalRequired):
            actions.execute(session, world.tenant_id, proposal, by="nobody", today=TODAY)
    with (
        pytest.raises(actions.ApprovalRequired),
        tenant_session(world.tenant_id) as session,
    ):
        pending = session.get(
            m.Proposal, world.proposal("draft_fee_claim", "fee_above_contract", "payu", 1)
        )
        assert pending is not None
        actions.execute(session, world.tenant_id, pending, by="nobody", today=TODAY)

    assert world.count(m.Action, proposal_id) == 0
    assert world.finding(proposal_id).state == "recoverable"


def test_a_rejected_proposal_can_never_be_carried_out(world: World) -> None:
    proposal_id = world.proposal("draft_fee_claim", "fee_above_contract", "cashfree", 500_000)

    rejected = world.decide(proposal_id, world.priya, "rejected", reason="Already raised by phone")

    assert (rejected.proposal.status, rejected.action) == ("rejected", None)
    with pytest.raises(approvals.AlreadyDecided, match="already rejected"):
        world.decide(proposal_id, world.arjun)
    with pytest.raises(DBAPIError), tenant_session(world.tenant_id) as session:
        session.add(
            m.Action(tenant_id=world.tenant_id, proposal_id=proposal_id, type="draft_fee_claim")
        )
        session.flush()
    assert world.finding(proposal_id).state == "recoverable"


# --- An approval, and what it produces -----------------------------------------------------


def test_an_approval_drafts_the_claim_and_sends_nothing(world: World) -> None:
    proposal_id = world.proposal(
        "draft_fee_claim", "fee_above_contract", "cashfree", 9_904, 20_445, 15_973
    )

    decision = world.decide(proposal_id, world.priya)

    assert (decision.proposal.status, decision.approvals, decision.needed) == ("approved", 1, 1)
    assert decision.action is not None and decision.action.type == "draft_fee_claim"
    with tenant_session(world.tenant_id) as session:
        [claim] = session.scalars(select(m.Claim).where(m.Claim.proposal_id == proposal_id))
        events = list(
            session.scalars(
                select(m.FindingEvent)
                .where(m.FindingEvent.finding_id == world.finding(proposal_id).id)
                .order_by(m.FindingEvent.occurred_at, m.FindingEvent.id)
            )
        )
        logged = session.execute(
            select(m.AuditLog.kind, m.AuditLog.actor).where(
                m.AuditLog.ref == f"proposal:{proposal_id}"
            )
        ).all()
    assert (claim.provider, claim.amount_minor, claim.status) == ("cashfree", 46_322, "drafted")
    assert decision.action.result == {
        "claims": [str(claim.id)],
        "documents": [f"claims/{claim.id}.pdf", f"claims/{claim.id}.txt"],
        "skipped_findings": [],
    }
    # The finding is now claimed, by the person who approved.
    assert world.finding(proposal_id).state == "claimed"
    assert (events[-1].from_state, events[-1].to_state, events[-1].actor) == (
        "recoverable",
        "claimed",
        f"user:{world.priya}",
    )
    assert sorted(logged) == [
        ("action", f"user:{world.priya}"),
        ("approval", f"user:{world.priya}"),
    ]

    document = get_storage().get(world.tenant_id, f"{claim.document_ref}.pdf")
    email = get_storage().get(world.tenant_id, f"{claim.document_ref}.txt").decode()
    assert document.startswith(b"%PDF-1.4") and document.rstrip().endswith(b"%%EOF")
    assert b"Amount claimed: INR 463.22" in document
    assert email.startswith(
        "Subject: Fees charged above the contract rate, October 2026: INR 463.22"
    )
    assert "To: Cashfree merchant support" in email
    assert email.count("cashfree-") == 3
    assert "204.45" in email


def test_approving_cannot_carry_a_proposal_out_twice(world: World) -> None:
    proposal_id = world.proposal("draft_fee_claim", "fee_not_in_contract", "razorpay", 38_204)
    first = world.decide(proposal_id, world.priya)

    with pytest.raises(approvals.AlreadyDecided, match="already approved"):
        world.decide(proposal_id, world.arjun)
    with tenant_session(world.tenant_id) as session:
        proposal = session.get(m.Proposal, proposal_id)
        assert proposal is not None
        again = actions.execute(session, world.tenant_id, proposal, by="x", today=TODAY)

    assert first.action is not None and again.id == first.action.id
    assert world.count(m.Action, proposal_id) == world.count(m.Claim, proposal_id) == 1


@pytest.mark.parametrize("who", ["viewer", "stranger", "other_tenant"])
def test_only_an_approver_or_an_admin_of_the_tenant_can_decide(
    world: World, two_person: World, who: str
) -> None:
    proposal_id = world.proposal("draft_fee_claim", "fee_above_contract", "cashfree", 500_000)
    person = {"viewer": world.viewer, "stranger": uuid4(), "other_tenant": two_person.priya}[who]

    with pytest.raises(approvals.NotAllowed):
        world.decide(proposal_id, person)

    assert world.status(proposal_id) == "pending"
    assert world.count(m.Approval, proposal_id) == 0


def test_a_decision_on_a_proposal_that_does_not_exist_is_refused(
    world: World, two_person: World
) -> None:
    elsewhere = two_person.proposal("draft_fee_claim", "fee_above_contract", "cashfree", 5)

    for missing in (uuid4(), elsewhere):
        with pytest.raises(approvals.ProposalNotFound):
            world.decide(missing, world.priya)
    with pytest.raises(ValueError, match="one of approved, rejected"):
        world.decide(elsewhere, world.priya, "maybe")


def test_a_finding_that_moved_on_before_the_approval_is_left_alone(world: World) -> None:
    proposal_id = world.proposal("draft_fee_claim", "fee_above_contract", "payu", 700_000)
    with tenant_session(world.tenant_id) as session:
        store.move_finding(
            session,
            world.tenant_id,
            world.finding(proposal_id).id,
            "written_off",
            actor="user",
            reason="Too small to chase",
        )

    decision = world.decide(proposal_id, world.priya)

    assert decision.action is not None
    assert decision.action.result["claims"] == []
    assert decision.action.result["skipped_findings"] == [str(world.finding(proposal_id).id)]
    assert world.finding(proposal_id).state == "written_off"


# --- Two-person approval ---------------------------------------------------------------------


def test_with_two_person_approval_one_approval_is_not_enough(two_person: World) -> None:
    proposal_id = two_person.proposal("draft_fee_claim", "fee_above_contract", "cashfree", 500_000)

    first = two_person.decide(proposal_id, two_person.priya)

    assert (first.proposal.status, first.approvals, first.needed, first.action) == (
        "pending",
        1,
        2,
        None,
    )
    assert two_person.count(m.Action, proposal_id) == 0
    # The same person again is not a second person.
    with pytest.raises(approvals.AlreadyDecided, match="needs a second person"):
        two_person.decide(proposal_id, two_person.priya)
    # And the database agrees that one of two is not approval.
    with pytest.raises(DBAPIError) as refused, tenant_session(two_person.tenant_id) as session:
        session.add(
            m.Action(
                tenant_id=two_person.tenant_id, proposal_id=proposal_id, type="draft_fee_claim"
            )
        )
        session.flush()
    assert "the proposal has 1 of 2 approvals" in str(refused.value)

    second = two_person.decide(proposal_id, two_person.arjun)

    assert (second.proposal.status, second.approvals) == ("approved", 2)
    assert second.action is not None
    assert two_person.finding(proposal_id).state == "claimed"


def test_with_two_person_approval_one_rejection_ends_it(two_person: World) -> None:
    proposal_id = two_person.proposal("draft_fee_claim", "fee_above_contract", "cashfree", 500_000)
    two_person.decide(proposal_id, two_person.priya)

    rejected = two_person.decide(proposal_id, two_person.arjun, "rejected")

    assert (rejected.proposal.status, rejected.action) == ("rejected", None)
    assert two_person.count(m.Action, proposal_id) == 0


# --- The other documents ------------------------------------------------------------------------


def test_a_settlement_query_is_a_claim_and_a_reoffer_list_is_not(world: World) -> None:
    query = world.proposal("draft_settlement_query", "settled_short", "marketplace", 3_500_000)
    reoffer = world.proposal(
        "draft_reoffer_list", "paid_auto_refunded", "razorpay", 385_200, 222_800
    )
    report = world.proposal("draft_leakage_report", "captured_not_settled", "payu", 319_400)

    for proposal_id in (query, reoffer, report):
        world.decide(proposal_id, world.arjun)

    assert [world.count(m.Claim, p) for p in (query, reoffer, report)] == [1, 0, 0]
    assert [world.finding(p).state for p in (query, reoffer, report)] == [
        "claimed",
        "recoverable",
        "recoverable",
    ]
    listing = get_storage().get(world.tenant_id, f"reoffers/{reoffer}.csv").decode()
    assert listing.splitlines()[0] == "order_id,payment_id,amount_inr"
    assert listing.splitlines()[1].startswith("BK-0,razorpay-")
    assert listing.splitlines()[1].endswith(",3852.00")
    assert len(listing.splitlines()) == 3
    summary = get_storage().get(world.tenant_id, f"reports/{report}.pdf")
    assert b"Leakage report" in summary and b"3,194.00" in summary


# --- Claim drafts ---------------------------------------------------------------------------------


def finding_with(lines: int, kind: str = "fee_above_contract") -> m.Finding:
    items = [
        {"id": f"771200984/PAYMENT:{511400000000 + n}", "amount_minor": 16_000}
        for n in range(lines)
    ]
    return m.Finding(
        type=kind,
        provider="cashfree",
        amount_minor=16_000 * lines,
        state="recoverable",
        evidence={"items": items},
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 31),
        rule_version="1",
    )


def test_a_claim_lists_every_record_and_its_amount() -> None:
    draft = claims.build(finding_with(1_000), "Acme Travel", TODAY)

    document = claims.render_pdf(draft)
    email = claims.render_email(draft)

    assert (draft.amount_minor, draft.period, draft.to) == (16_000_000, "October 2026", "Cashfree")
    assert document.count(b"/Type /Page ") == 21
    assert document.count(b"771200984/PAYMENT:") == 1_000
    assert b"Amount claimed: INR 1,60,000.00" in document
    assert b"On 1,000 records, the" in document
    assert b"Not sent to anyone until the merchant sends it" in document
    assert "INR 1,60,000.00" in email.splitlines()[0]
    assert email.count("771200984/PAYMENT:") == 25
    assert "... and 975 more, all listed in the attached PDF." in email
    assert "From: Acme Travel" in email


@pytest.mark.parametrize(
    ("kind", "words"),
    [
        ("tax_on_refunded_fee", "the tax on that fee was not"),
        ("fee_not_in_contract", "a type our contract does not allow"),
        ("settled_short", "paid for less than the sum of its own lines"),
        ("captured_not_settled", "has not appeared in any settlement"),
    ],
)
def test_each_kind_of_finding_has_its_own_wording(kind: str, words: str) -> None:
    email = claims.render_email(claims.build(finding_with(2, kind), "Acme Travel", TODAY))

    assert words in email
    assert "INR 320.00" in email


def test_no_claim_is_drafted_for_a_finding_that_is_not_a_claim_on_a_provider() -> None:
    with pytest.raises(ValueError, match="No claim can be drafted"):
        claims.build(finding_with(1, "paid_auto_refunded"), "Acme Travel", TODAY)


def test_the_pdf_writer_wraps_escapes_and_stays_readable() -> None:
    document = pdf.render([pdf.Line("Total (net) ₹1,60,000 \\ due"), pdf.Line("Next", "bold", 16)])

    assert document.startswith(b"%PDF-1.4")
    assert b"(Total \\(net\\) INR 1,60,000 \\\\ due) Tj" in document
    assert b"(Page 1 of 1) Tj" in document
    # The cross-reference table points at each object.
    start = int(document.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    assert document[start : start + 4] == b"xref"
    for line in document[start:].split(b"\n")[3:10]:
        if line.endswith(b" n "):
            assert document[int(line[:10]) :].split(b" ")[1] == b"0"
    assert pdf.wrap("one two three", 7) == ["one two", "three"]
    assert pdf.wrap("x" * 10, 4) == ["xxxx", "xxxx", "xx"]
    assert pdf.wrap("") == [""]
