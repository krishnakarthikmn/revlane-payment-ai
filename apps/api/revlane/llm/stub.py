"""The deterministic stub: a fixed, schema-valid answer for each playbook,
keyed by the tool results it is shown. No network, no key, no randomness.

It behaves the way the playbooks ask a model to: it calls the playbook's
tools, then writes a summary whose every figure is a reference to a tool
result, and proposes only what the playbook allows.
"""

from collections.abc import Callable
from typing import Any

from revlane.llm.client import Exchange, ModelError, ModelRequest, ModelResponse, ToolCall

PROVIDERS = {
    "razorpay": "Razorpay",
    "payu": "PayU",
    "cashfree": "Cashfree",
    "marketplace": "the marketplace",
}
LEAKS = {
    "paid_auto_refunded": "Payments refunded automatically on bookings that never confirmed",
    "settled_short": "Payouts lower than the sum of their lines",
    "captured_not_settled": "Captured payments still in no settlement",
    "fee_above_contract": "Fees charged above the contract rate",
    "tax_on_refunded_fee": "Tax kept on fees that were refunded",
    "fee_not_in_contract": "Fees the contract does not allow",
}
QUERY_TYPES = ("settled_short", "captured_not_settled")


def _where(row: dict[str, Any]) -> str:
    return PROVIDERS.get(str(row.get("provider")), "the provider")


def _results(transcript: tuple[Exchange, ...], name: str) -> list[Exchange]:
    return [exchange for exchange in transcript if exchange.call.name == name]


def _limit(request: ModelRequest, action_type: str, name: str) -> int:
    limits = request.context.get("limits", {}).get(action_type, {})
    return int(limits.get(name, 0))


def _worth_claiming(request: ModelRequest, action_type: str, finding: dict[str, Any]) -> bool:
    return int(finding["amount_minor"]) >= _limit(request, action_type, "min_claim_minor")


def leakage_audit(request: ModelRequest) -> ModelResponse:
    transcript = request.transcript
    if not transcript:
        return ModelResponse(tool_calls=(ToolCall("three_way_match", {}),))
    if not _results(transcript, "rupee_impact"):
        return ModelResponse(
            tool_calls=(
                ToolCall("rupee_impact", {"group_by": "leak"}),
                ToolCall("findings_for", {"state": "recoverable"}),
            )
        )
    match = _results(transcript, "three_way_match")[0]
    impact = _results(transcript, "rupee_impact")[0]
    open_findings = _results(transcript, "findings_for")[0]
    groups = impact.result.get("groups", [])
    if not groups:
        return ModelResponse(
            output={
                "headline": f"No leaks were found in the records as of {{{{{match.ref}.as_of}}}}.",
                "points": [],
                "top_cause": None,
                "confidence": "high",
                "proposals": [],
            }
        )
    i = impact.ref
    points = [
        f"{LEAKS.get(group['type'], 'A leak')} at {_where(group)}: "
        f"{{{{{i}.groups.{n}.lost_minor}}}} over {{{{{i}.groups.{n}.record_count}}}} records."
        for n, group in enumerate(groups)
    ]
    findings = open_findings.result.get("findings", [])
    cap = _limit(request, "draft_leakage_report", "max_findings_per_proposal") or len(findings)
    proposals = []
    if findings:
        o = open_findings.ref
        proposals.append(
            {
                "action_type": "draft_leakage_report",
                "finding_ids": [finding["id"] for finding in findings[:cap]],
                "title": f"Leakage report as of {{{{{match.ref}.as_of}}}}",
                "rationale": (
                    f"Puts the {{{{{o}.finding_count}}}} open findings, worth "
                    f"{{{{{o}.total_minor}}}}, in one report for the finance team."
                ),
            }
        )
    return ModelResponse(
        output={
            "headline": (
                f"{{{{{i}.lost_minor}}}} leaked across {{{{{i}.group_count}}}} leaks. "
                f"{{{{{i}.recoverable_minor}}}} can still be recovered and "
                f"{{{{{i}.recovered_minor}}}} has reached the bank."
            ),
            "points": points,
            "top_cause": impact.result.get("top_key"),
            "confidence": "high",
            "proposals": proposals,
        }
    )


def reconciliation(request: ModelRequest) -> ModelResponse:
    transcript = request.transcript
    if not transcript:
        return ModelResponse(
            tool_calls=(
                ToolCall("findings_for", {"family": "reconciliation", "state": "recoverable"}),
            )
        )
    listed = _results(transcript, "findings_for")[0]
    findings = listed.result.get("findings", [])
    if findings and not _results(transcript, "evidence_for"):
        calls = [ToolCall("evidence_for", {"finding_id": f["id"], "limit": 5}) for f in findings]
        calls += [
            ToolCall("bank_credits", {"finding_id": f["id"]})
            for f in findings
            if f["type"] == "settled_short"
        ]
        return ModelResponse(tool_calls=tuple(calls))
    t = listed.ref
    if not findings:
        return ModelResponse(
            output={
                "headline": "Every payment, settlement and bank credit is accounted for.",
                "points": [],
                "confidence": "high",
                "proposals": [],
            }
        )
    points, proposals = [], []
    for n, finding in enumerate(findings):
        amount, records = (
            f"{{{{{t}.findings.{n}.amount_minor}}}}",
            f"{{{{{t}.findings.{n}.record_count}}}}",
        )
        month = f"{{{{{t}.findings.{n}.month}}}}"
        points.append(
            f"{LEAKS.get(finding['type'], 'A leak')} at {_where(finding)}: {amount} over "
            f"{records} records in {month}."
        )
        if finding["type"] in QUERY_TYPES:
            if _worth_claiming(request, "draft_settlement_query", finding):
                proposals.append(
                    {
                        "action_type": "draft_settlement_query",
                        "finding_ids": [finding["id"]],
                        "title": f"Ask {_where(finding)} about {amount} not settled in full",
                        "rationale": (
                            f"{records} records from {month} show {amount} owed. A query "
                            "with the record ids asks the provider to settle or explain."
                        ),
                    }
                )
        elif finding["type"] == "paid_auto_refunded":
            proposals.append(
                {
                    "action_type": "draft_reoffer_list",
                    "finding_ids": [finding["id"]],
                    "title": f"List {records} refunded bookings to offer again",
                    "rationale": (
                        f"{records} shoppers paid {amount} and were refunded because the "
                        "booking never confirmed. A list lets the team offer it to them again."
                    ),
                }
            )
    return ModelResponse(
        output={
            "headline": (
                f"{{{{{t}.total_minor}}}} was paid but has not reached the bank, in "
                f"{{{{{t}.finding_count}}}} findings."
            ),
            "points": points,
            "confidence": "high",
            "proposals": proposals,
        }
    )


def fee_audit(request: ModelRequest) -> ModelResponse:
    transcript = request.transcript
    if not transcript:
        return ModelResponse(
            tool_calls=(ToolCall("findings_for", {"family": "fee_audit", "state": "recoverable"}),)
        )
    listed = _results(transcript, "findings_for")[0]
    findings = listed.result.get("findings", [])
    differences = _results(transcript, "fee_differences")
    if findings and not differences:
        return ModelResponse(
            tool_calls=tuple(
                ToolCall("fee_differences", {"finding_id": f["id"], "limit": 5}) for f in findings
            )
        )
    t = listed.ref
    if not findings:
        return ModelResponse(
            output={
                "headline": "Every fee checked is inside the contracts.",
                "points": [],
                "confidence": "high",
                "proposals": [],
            }
        )
    by_finding = {str(d.call.arguments.get("finding_id")): d for d in differences}
    points, proposals = [], []
    for n, finding in enumerate(findings):
        amount, month = f"{{{{{t}.findings.{n}.amount_minor}}}}", f"{{{{{t}.findings.{n}.month}}}}"
        detail = by_finding.get(finding["id"])
        lines = (
            f"{{{{{detail.ref}.line_count}}}}"
            if detail
            else f"{{{{{t}.findings.{n}.record_count}}}}"
        )
        points.append(
            f"{LEAKS.get(finding['type'], 'A fee difference')} at {_where(finding)}: {amount} "
            f"over {lines} settlement lines in {month}."
        )
        if _worth_claiming(request, "draft_fee_claim", finding):
            proposals.append(
                {
                    "action_type": "draft_fee_claim",
                    "finding_ids": [finding["id"]],
                    "title": f"Claim {amount} back from {_where(finding)}",
                    "rationale": (
                        f"{lines} settlement lines in {month} carry {amount} more than the "
                        "contract gives. The claim lists every line."
                    ),
                }
            )
    return ModelResponse(
        output={
            "headline": (
                f"{{{{{t}.total_minor}}}} in fees was charged outside the contracts, across "
                f"{{{{{t}.finding_count}}}} findings."
            ),
            "points": points,
            "confidence": "high",
            "proposals": proposals,
        }
    )


SCRIPTS: dict[str, Callable[[ModelRequest], ModelResponse]] = {
    "leakage_audit": leakage_audit,
    "reconciliation": reconciliation,
    "fee_audit": fee_audit,
}


class StubClient:
    name = "stub"

    def complete(self, request: ModelRequest) -> ModelResponse:
        script = SCRIPTS.get(request.playbook)
        if script is None:
            raise ModelError(f"The stub has no answer for the playbook {request.playbook}.")
        return script(request)
