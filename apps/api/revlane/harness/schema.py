"""The shape an agent's output must have, and the checks it must pass.

Three checks, in order: the schema; grounding (no figure of its own, every
reference leads to a tool result of this run); and policy (only the actions
the playbook allows, only findings this run actually looked at, inside the
playbook's limits). A failure of any of them refuses the whole output.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from revlane.engine.findings import RECOVERABLE
from revlane.harness import grounding
from revlane.harness.playbook import Playbook

# The model says how sure it is in a word; code turns the word into a figure.
CONFIDENCE_BPS = {"high": 9_000, "medium": 7_000, "low": 4_000}


class OutputInvalid(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class ProposalOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str
    finding_ids: list[str] = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=2_000)


class FindingAndProposalV1(BaseModel):
    """Text fields refer to tool results as {{t1.total_minor}} and contain no figures."""

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=500)
    points: list[str] = Field(default_factory=list, max_length=30)
    # The key of the largest leak, as a tool returned it.
    top_cause: str | None = None
    confidence: Literal["high", "medium", "low"] = "high"
    proposals: list[ProposalOut] = Field(default_factory=list, max_length=20)

    def texts(self) -> list[str]:
        own = [self.headline, *self.points]
        return own + [text for p in self.proposals for text in (p.title, p.rationale)]


SCHEMAS: dict[str, type[FindingAndProposalV1]] = {"FindingAndProposal_v1": FindingAndProposalV1}


@dataclass(frozen=True, slots=True)
class FindingFacts:
    """What the policy check needs to know about a finding a proposal names."""

    type: str
    state: str
    amount_minor: int


def _collect(value: object, keys: tuple[str, ...], found: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, inner in value.items():
            if key in keys and isinstance(inner, str):
                found.add(inner)
            else:
                _collect(inner, keys, found)
    elif isinstance(value, list):
        for inner in value:
            _collect(inner, keys, found)


def seen(results: Mapping[str, Any], *keys: str) -> set[str]:
    """Every string a tool returned under one of the keys, anywhere in this run's results."""
    found: set[str] = set()
    _collect(dict(results), keys, found)
    return found


def validate(
    raw: Mapping[str, Any],
    *,
    playbook: Playbook,
    results: Mapping[str, Any],
    findings: Mapping[str, FindingFacts],
) -> FindingAndProposalV1:
    """The output, once it has passed every check. Raises `OutputInvalid` otherwise.

    `findings` holds the tenant's findings by id: a proposal naming an id that
    is not there (another tenant's, or an invented one) is refused.
    """
    try:
        output = SCHEMAS[playbook.output_schema].model_validate(dict(raw))
    except ValidationError as error:
        raise OutputInvalid(
            [
                f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
                for issue in error.errors()
            ]
        ) from error

    problems: list[str] = []
    for text in output.texts():
        problems += grounding.check_text(text, results)

    if output.top_cause is not None and output.top_cause not in seen(results, "key", "top_key"):
        problems.append("top_cause is not a leak a tool returned")

    looked_at = seen(results, "id")
    per_action: dict[str, int] = {}
    claimed: set[tuple[str, str]] = set()
    for proposal in output.proposals:
        rule = playbook.action(proposal.action_type)
        if rule is None:
            problems.append(f"the action {proposal.action_type} is not allowed for this playbook")
            continue
        per_action[rule.type] = per_action.get(rule.type, 0) + 1
        if per_action[rule.type] == rule.limits.max_claims_per_run + 1:
            problems.append(f"more than {rule.limits.max_claims_per_run} {rule.type} proposals")
        if len(proposal.finding_ids) > rule.limits.max_findings_per_proposal:
            problems.append(f"too many findings in one {rule.type} proposal")
        for finding_id in proposal.finding_ids:
            facts = findings.get(_canonical(finding_id))
            if facts is None or finding_id not in looked_at:
                problems.append(f"{finding_id} is not a finding this run looked at")
                continue
            if (rule.type, finding_id) in claimed:
                problems.append(f"{finding_id} is in two {rule.type} proposals")
            claimed.add((rule.type, finding_id))
            if facts.state != RECOVERABLE:
                problems.append(f"{finding_id} is {facts.state}, not recoverable")
            if rule.applies_to and facts.type not in rule.applies_to:
                problems.append(f"{rule.type} is not for a {facts.type} finding")
            if facts.amount_minor < rule.limits.min_claim_minor:
                problems.append(f"{finding_id} is below the smallest claim worth making")
    if problems:
        raise OutputInvalid(problems)
    return output


def _canonical(finding_id: str) -> str:
    try:
        return str(UUID(finding_id))
    except ValueError:
        return finding_id
