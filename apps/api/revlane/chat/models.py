"""How a question becomes a plan, and results become an answer.

A chat model does two things and nothing else: it turns a question into a
plan made of query specs and tool calls (never SQL), and it writes an answer
whose figures are references to the results. The stub does both with fixed
rules, so tests and `make eval` run without a key.
"""

import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from revlane.chat.semantic import Layer
from revlane.chat.spec import Filter, QuerySpec

# The harness tools Ask Revlane may call for "why" questions. All read.
CHAT_TOOLS = ("rupee_impact", "findings_for", "fee_differences", "evidence_for", "contract_for")
PLAYBOOKS = ("leakage_audit", "reconciliation", "fee_audit")


class QueryStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["query"] = "query"
    spec: QuerySpec


class ToolStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["tool"] = "tool"
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    """What to look up to answer a question, or why it cannot be answered."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["answer", "refuse"]
    steps: list[QueryStep | ToolStep] = Field(default_factory=list, max_length=3)
    # For a refusal: what the question needs that is not defined, in a few words.
    missing: str | None = Field(default=None, max_length=80)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Refers to results as {{q1.totals.lost_minor}}; carries no figure of its own.
    text: str = Field(min_length=1, max_length=1_500)
    # An agent to offer to run next. Running it goes through the approval flow.
    next_step: Literal["leakage_audit", "reconciliation", "fee_audit"] | None = None


class ChatModel(Protocol):
    name: str

    def understand(self, question: str, layer: Layer) -> dict[str, Any]:
        """The plan for a question, as raw data to be validated."""

    def answer(self, question: str, plan: Plan, results: dict[str, Any]) -> dict[str, Any]:
        """The answer, as raw data to be validated and grounded."""


# --- The stub ---------------------------------------------------------------------

# The first phrase found decides the metric; more specific phrases come first.
METRIC_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("payment_success_rate", ("success rate", "succeed", "success")),
    ("match_rate", ("match rate", "matched", "reconciled to the bank")),
    (
        "fee_effective_rate",
        ("effective rate", "effective fee", "fee rate", "paying in fees", "cheap"),
    ),
    (
        "fee_overcharge_minor",
        ("overcharg", "above contract", "outside the contract", "audit our fees"),
    ),
    ("unsettled_minor", ("unsettled", "not settled", "not yet settled", "awaiting settlement")),
    ("refund_amount_minor", ("refund",)),
    (
        "recoverable_minor",
        ("recoverable", "still recover", "can we recover", "can still be recovered"),
    ),
    ("recovered_minor", ("recovered", "got back", "won back")),
    ("lost_minor", ("lost", "lose", "leak", "slipped")),
    ("payment_volume_minor", ("volume", "process", "collected", "payments worth", "took in")),
)
# Things people ask about that no metric covers. The question is refused by name.
UNDEFINED: tuple[tuple[str, str], ...] = (
    ("churn", "customer churn"),
    ("lifetime value", "customer lifetime value"),
    ("conversion", "checkout conversion"),
    ("margin", "profit margin"),
    ("profit", "profit"),
    ("average order", "average order value"),
    ("dispute", "disputes"),
    ("chargeback", "chargebacks"),
    ("weather", "the weather"),
    ("salary", "salaries"),
    ("how many", "a count of records"),
    ("number of", "a count of records"),
)
DIMENSION_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "provider",
        (
            "by provider",
            "by gateway",
            "per gateway",
            "per provider",
            "each gateway",
            "each provider",
            "which gateway",
            "which provider",
            "across gateways",
        ),
    ),
    (
        "method",
        (
            "by method",
            "by payment method",
            "per method",
            "each method",
            "which method",
            "which payment method",
        ),
    ),
    ("instrument_bank", ("by bank", "per bank", "each bank", "which bank")),
    ("card_network", ("by card network", "by network", "which card network", "each card network")),
    ("upi_app", ("by upi app", "which upi app", "each upi app", "by app")),
    ("hour", ("by hour", "hour of day", "each hour", "which hour")),
    ("day", ("by day", "daily", "each day", "per day")),
    ("month", ("by month", "monthly", "each month", "per month")),
    ("leak", ("by leak", "by type", "by kind", "which leak", "each leak", "what kind")),
    ("cause", ("by cause", "which cause", "each cause")),
    ("state", ("by state", "by status")),
    ("family", ("by family", "reconciliation and fee", "reconciliation or fee", "by agent")),
    ("refund_type", ("by refund type", "merchant or automatic", "automatic or merchant")),
)
FILTER_WORDS: dict[str, tuple[tuple[str, str], ...]] = {
    "provider": (
        ("razorpay", "razorpay"),
        ("payu", "payu"),
        ("cashfree", "cashfree"),
        ("marketplace", "marketplace"),
    ),
    "method": (
        ("upi", "upi"),
        ("card", "card"),
        ("netbanking", "netbanking"),
        ("wallet", "wallet"),
    ),
    "card_network": (
        ("amex", "amex"),
        ("visa", "visa"),
        ("mastercard", "mastercard"),
        ("rupay", "rupay"),
    ),
    "instrument_bank": (
        ("hdfc", "HDFC"),
        ("icici", "ICICI"),
        ("sbi", "SBI"),
        ("axis", "AXIS"),
        ("kotak", "KOTAK"),
    ),
}
PERIOD_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("last_seven_days", ("last week", "past week", "this week", "last seven days", "last 7 days")),
    ("last_thirty_days", ("last thirty days", "last 30 days", "past thirty days")),
    ("last_ninety_days", ("last ninety days", "last 90 days", "last quarter", "past quarter")),
    ("last_month", ("last month", "previous month")),
    ("this_month", ("this month", "month to date")),
)
PERIOD_WORDS = {
    "last_seven_days": "in the last seven days",
    "last_thirty_days": "in the last thirty days",
    "last_ninety_days": "in the last ninety days",
    "last_month": "last month",
    "this_month": "so far this month",
    "all_time": "across everything on record",
}
METRIC_WORDS = {
    "payment_volume_minor": "Payment volume",
    "payment_success_rate": "Payment success rate",
    "unsettled_minor": "Payments not yet settled",
    "lost_minor": "Money lost to leaks",
    "recoverable_minor": "Money that can still be recovered",
    "recovered_minor": "Money recovered",
    "fee_overcharge_minor": "Fees charged outside the contracts",
    "fee_effective_rate": "The effective fee rate",
    "match_rate": "The share of settlements matched to the bank",
    "refund_amount_minor": "Refunds",
}
DIMENSION_WORDS = {
    "provider": "provider",
    "method": "payment method",
    "instrument_bank": "bank",
    "card_network": "card network",
    "upi_app": "UPI app",
    "hour": "hour of the day",
    "day": "day",
    "month": "month",
    "leak": "kind of leak",
    "cause": "cause",
    "state": "state",
    "family": "audit",
    "refund_type": "kind of refund",
}
NEXT_STEP = {
    "lost_minor": "leakage_audit",
    "recoverable_minor": "leakage_audit",
    "fee_overcharge_minor": "fee_audit",
    "fee_effective_rate": "fee_audit",
    "unsettled_minor": "reconciliation",
}
WORD = re.compile(r"[a-z]+")


def _has(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


class StubChat:
    """Fixed rules in place of a model. Deterministic, and it knows no SQL."""

    name = "stub"

    def understand(self, question: str, layer: Layer) -> dict[str, Any]:
        text = " ".join(question.lower().replace("?", " ").split())
        metrics = [name for name, phrases in METRIC_PHRASES if _has(text, phrases)]
        undefined = next((name for phrase, name in UNDEFINED if phrase in text), None)
        if undefined or not metrics:
            return {"kind": "refuse", "missing": undefined or "what that question asks for"}

        # "Why" about leaks is a question for the engines' own tools.
        if text.startswith(("why", "explain")) and metrics[0] in NEXT_STEP:
            family = "fee_audit" if metrics[0] == "fee_overcharge_minor" else None
            arguments = {"state": "recoverable"} | ({"family": family} if family else {})
            return {
                "kind": "answer",
                "steps": [{"kind": "tool", "name": "findings_for", "arguments": arguments}],
            }

        first = layer.metrics[metrics[0]]
        together = [m for m in metrics if layer.metrics[m].source == first.source][:2]
        dimensions = [name for name, phrases in DIMENSION_PHRASES if _has(text, phrases)][:2]
        words = set(WORD.findall(text))
        filters = []
        for dimension, pairs in FILTER_WORDS.items():
            values = [value for word, value in pairs if word in words or f"{word}s" in words]
            # A provider named in a question split by provider is not a filter.
            if values and dimension not in dimensions:
                filters.append({"dimension": dimension, "values": values})
        if "where did we lose" in text or "where are we losing" in text:
            dimensions = dimensions or ["family"]
        period = next((name for name, phrases in PERIOD_PHRASES if _has(text, phrases)), "all_time")
        lowest = _has(text, ("lowest", "least", "worst", "smallest", "weakest", "cheapest"))
        spec = {
            "metrics": together,
            "dimensions": dimensions,
            "filters": filters,
            "period": period,
            "order": "smallest_first" if lowest else "largest_first",
            "rows": "all",
        }
        return {"kind": "answer", "steps": [{"kind": "query", "spec": spec}]}

    def answer(self, question: str, plan: Plan, results: dict[str, Any]) -> dict[str, Any]:
        step = plan.steps[0]
        if isinstance(step, ToolStep):
            found = results["t1"]
            if not found.get("finding_count"):
                return {"text": "There are no open findings to explain."}
            first = found["findings"][0]
            return {
                "text": (
                    "The engines have {{t1.finding_count}} open findings worth {{t1.total_minor}}. "
                    f"The largest is {first['type'].replace('_', ' ')} at "
                    "{{t1.findings.0.provider}}: "
                    "{{t1.findings.0.amount_minor}} over {{t1.findings.0.record_count}} records "
                    "in {{t1.findings.0.month}}."
                ),
                "next_step": "fee_audit"
                if first["type"].startswith(("fee", "tax"))
                else "reconciliation",
            }
        spec, result = step.spec, results["q1"]
        metric, key = spec.metrics[0], result["metrics"][0]
        subject = METRIC_WORDS[metric] + _filter_words(spec.filters)
        when = PERIOD_WORDS[spec.period]
        if result["row_count"] == 0 or result["totals"][key] is None:
            return {"text": f"There is nothing on record for that {when}."}
        if not spec.dimensions:
            text = f"{subject} {when}: {{{{q1.totals.{key}}}}}."
            if len(result["metrics"]) > 1:
                second = METRIC_WORDS[spec.metrics[1]].lower()
                text += f" Alongside it, {second}: {{{{q1.totals.{result['metrics'][1]}}}}}."
        else:
            split = " and ".join(DIMENSION_WORDS[name] for name in spec.dimensions)
            name = spec.dimensions[0]
            rank = "lowest" if spec.order == "smallest_first" else "highest"
            text = (
                f"{subject} by {split}, {when}: {{{{q1.top.{name}}}}} is {rank} at "
                f"{{{{q1.top.{key}}}}}. Overall it is {{{{q1.totals.{key}}}}}."
            )
        return {"text": text, "next_step": NEXT_STEP.get(metric)}


def _filter_words(filters: list[Filter]) -> str:
    if not filters:
        return ""
    return " for " + " and ".join(", ".join(f.values) for f in filters)
