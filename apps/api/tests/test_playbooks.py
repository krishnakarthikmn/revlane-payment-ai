"""Playbooks are checked against what the harness can actually do."""

import pytest

from revlane.harness import playbook as pb
from revlane.harness.tools import REGISTRY

GOOD = """
playbook: fee_audit
version: 1
trigger:
  - schedule: "0 6 * * *"
read_tools: [fee_differences, contract_for, settlement_lines]
actions:
  - type: draft_fee_claim
    approval: required
    limits: {min_claim_minor: 100000, max_claims_per_run: 20}
output_schema: FindingAndProposal_v1
escalate_if: [contract_missing, confidence < 0.8]
"""


def test_the_playbook_from_the_technical_design_loads() -> None:
    book = pb.parse(GOOD)

    assert (book.name, book.version) == ("fee_audit", 1)
    assert book.tools == {"fee_differences", "contract_for", "settlement_lines"}
    rule = book.action("draft_fee_claim")
    assert rule is not None
    assert (rule.limits.min_claim_minor, rule.limits.max_claims_per_run) == (100_000, 20)
    assert book.action("send_money") is None
    assert book.escalates_on_missing_contract
    assert book.confidence_floor_bps == 8_000


def test_the_three_first_version_playbooks_are_there() -> None:
    books = pb.load_all()

    assert set(books) == {"leakage_audit", "reconciliation", "fee_audit"}
    for book in books.values():
        assert book.actions, book.name
        assert {rule.approval for rule in book.actions} == {"required"}
        assert book.tools <= set(REGISTRY)
    with pytest.raises(pb.PlaybookError, match="No playbook called payroll"):
        pb.get("payroll")


def test_the_first_version_tools_are_the_ones_in_the_technical_design() -> None:
    assert set(REGISTRY) == {
        "three_way_match",
        "findings_for",
        "settlement_lines",
        "bank_credits",
        "fee_differences",
        "contract_for",
        "rupee_impact",
        "evidence_for",
    }
    for tool in REGISTRY.values():
        assert tool.spec.description, tool.name
        assert tool.spec.input_schema["additionalProperties"] is False, tool.name


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (("approval: required", "approval: none"), "actions.0.approval"),
        (("approval: required", "approval: auto"), "actions.0.approval"),
        (("type: draft_fee_claim", "type: send_money"), "no action called send_money"),
        (("contract_for,", "send_email,"), "no tool called send_email"),
        (("FindingAndProposal_v1", "Anything_v9"), "no output schema called Anything_v9"),
        (("confidence < 0.8", "mood is low"), "cannot read the escalation rule"),
        (("version: 1", "version: 0"), "version"),
        (("max_claims_per_run: 20", "max_claims_per_run: 20, max_rupees: 5"), "max_rupees"),
        (("playbook: fee_audit", "playbook: Fee Audit"), "playbook"),
    ],
)
def test_a_playbook_the_harness_cannot_honour_is_refused(
    change: tuple[str, str], problem: str
) -> None:
    with pytest.raises(pb.PlaybookError, match=problem):
        pb.parse(GOOD.replace(*change))


def test_text_that_is_not_a_playbook_is_refused() -> None:
    for text in ("- just\n- a list", "playbook: [unclosed", ""):
        with pytest.raises(pb.PlaybookError):
            pb.parse(text)
