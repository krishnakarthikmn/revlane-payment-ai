"""Puts one row in every table for a tenant, so tests can check each table."""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from revlane.db import models as m

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
TODAY = date(2026, 11, 16)


def seed_tenant(session: Session, name: str) -> UUID:
    """Run inside an admin session. Returns the new tenant's id."""
    tenant_id = uuid4()
    t = {"tenant_id": tenant_id}
    session.add(m.Tenant(id=tenant_id, name=name))
    session.flush()

    user = m.AppUser(**t, id=uuid4(), email=f"{name}@example.com", name=name, role="approver")
    account = m.ProviderAccount(**t, id=uuid4(), provider="razorpay", account_ref="acc_1")
    finding = m.Finding(
        **t,
        id=uuid4(),
        type="settled_short",
        provider="marketplace",
        amount_minor=3_500_000,
        state="recoverable",
        rule_version="v1",
    )
    proposal = m.Proposal(
        **t,
        id=uuid4(),
        finding_ids=[finding.id],
        playbook="reconciliation",
        playbook_version=1,
        action_type="draft_settlement_query",
        idempotency_key="k1",
    )
    session.add_all([user, account, finding, proposal])
    session.flush()

    session.add_all(
        [
            m.AgentSetting(**t, playbook="reconciliation", autonomy_level=1),
            m.VaultSecret(**t, name="razorpay", ciphertext=b"not-a-real-ciphertext"),
            m.FeeContract(
                **t,
                provider_account_id=account.id,
                method="card",
                rate_bps=192,
                allowed_fee_types=["payment"],
                effective_from=TODAY,
            ),
            m.Order(
                **t,
                merchant_order_id="BK-1",
                amount_minor=1_050_000,
                status="confirmed",
                placed_at=NOW,
            ),
            m.PaymentAttempt(
                **t,
                provider="razorpay",
                attempt_id="pay_1",
                provider_account_id=account.id,
                merchant_order_id="BK-1",
                amount_minor=1_050_000,
                first_seen_at=NOW,
            ),
            m.AttemptEvent(
                **t,
                provider="razorpay",
                attempt_id="pay_1",
                provider_status="captured",
                event_key="captured||2026-11-16T09:00:00+00:00",
                occurred_at=NOW,
                source="file",
            ),
            m.Alert(
                **t,
                kind="unmapped_status",
                provider="razorpay",
                subject="under_review",
                record_count=1,
                first_seen_at=NOW,
                last_seen_at=NOW,
            ),
            m.Refund(
                **t,
                provider="razorpay",
                attempt_id="pay_1",
                refund_id="rfnd_1",
                type="merchant",
                amount_minor=1_050_000,
                status="processed",
                occurred_at=NOW,
            ),
            m.Settlement(
                **t,
                provider="razorpay",
                settlement_id="setl_1",
                gross_minor=1_050_000,
                fee_minor=20_160,
                tax_minor=3_629,
                net_minor=1_026_211,
                utr="UTR1",
                settled_on=TODAY,
            ),
            m.SettlementLine(
                **t,
                provider="razorpay",
                settlement_id="setl_1",
                attempt_id="pay_1",
                line_ref="payment:pay_1",
                line_type="capture",
                gross_minor=1_050_000,
                fee_minor=20_160,
                tax_minor=3_629,
                net_minor=1_026_211,
            ),
            m.BankCredit(
                **t,
                account_ref="hdfc-1",
                line_hash="h1",
                utr="UTR1",
                amount_minor=1_026_211,
                credited_on=TODAY,
            ),
            m.Dispute(
                **t, provider="razorpay", dispute_id="disp_1", amount_minor=4_620_000, status="open"
            ),
            m.Mandate(
                **t, provider="razorpay", mandate_id="mnd_1", amount_minor=99_900, status="active"
            ),
            m.MatchLink(
                **t,
                left_type="settlement",
                left_id="setl_1",
                right_type="bank_credit",
                right_id="UTR1",
                rule="utr",
            ),
            m.FindingEvent(**t, finding_id=finding.id, to_state="recoverable", actor="engine"),
            m.Approval(**t, proposal_id=proposal.id, approver_id=user.id, decision="approved"),
            m.AuditLog(**t, actor="engine", kind="tool_call", payload_hash="0" * 64),
        ]
    )
    session.flush()
    # Only once the approval is stored will the database take what follows from it.
    session.add_all(
        [
            m.Action(**t, proposal_id=proposal.id, type="draft_settlement_query"),
            m.Claim(**t, proposal_id=proposal.id, provider="marketplace", amount_minor=3_500_000),
            m.AgentRun(**t, playbook="reconciliation", playbook_version=1, as_of=TODAY),
        ]
    )
    session.add(
        m.AttemptStatus(
            **t,
            provider="razorpay",
            attempt_id="pay_1",
            status="succeeded",
            provider_status="captured",
            last_event_at=NOW,
            rule_version="1",
            derived_at=NOW,
        )
    )
    session.flush()
    return tenant_id
