"""The run loop. See docs/technical-design.md, section 7.

1. Assemble context: rules and playbook, tenant policy, past decisions, the
   trigger and a compact data summary.
2. Call the model with only the tools the playbook allows.
3. For each tool call, check the allow-list, the arguments and the tenant
   scope. Tools only read; anything to be done becomes a proposal.
4. Log every call and result, and checkpoint after each tool call.
5. Validate the output against its schema, then grounding, then policy.
6. Persist, or escalate.

Every step commits on its own, so a run that stops can be picked up where it
was: tool calls already answered are not made again.
"""

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from revlane.config import get_settings
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.harness import audit, grounding, prompts, proposals, schema, tools
from revlane.harness.playbook import Playbook, PlaybookError
from revlane.harness.playbook import get as get_playbook
from revlane.llm.client import (
    Exchange,
    ModelClient,
    ModelError,
    ModelRequest,
    ModelResponse,
    ToolCall,
)

# The budget: model turns and tool calls one run may use before it is escalated.
MAX_TURNS = 12
MAX_TOOL_CALLS = 60
MAX_ATTEMPTS = 3
OBSERVE = 0
RECENT_REASONS = 5

OVER_BUDGET = "The run used its whole budget without reaching an answer."
DISABLED_EVERYWHERE = "Agents are switched off for every tenant."
DISABLED_FOR_TENANT = "Agents are switched off for this tenant."
DISABLED_FOR_PLAYBOOK = "This agent is switched off."


# One line of JSON per step of a run, all carrying the run's id as the trace id.
trace = logging.getLogger("revlane.trace")


def _span(
    kind: str, name: str, began: float, usage: dict[str, int] | None = None
) -> dict[str, Any]:
    """One timed step of a run: a model turn or a tool call."""
    span: dict[str, Any] = {
        "kind": kind,
        "name": name,
        "ms": int((time.perf_counter() - began) * 1000),
        "at": datetime.now(tz=UTC).isoformat(),
    }
    return span | ({"usage": usage} if usage else {})


def actor(playbook: str) -> str:
    return f"agent:{playbook}"


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _run(session: Session, tenant_id: UUID, run_id: UUID) -> m.AgentRun:
    run = session.scalar(
        select(m.AgentRun)
        .where(m.AgentRun.tenant_id == tenant_id, m.AgentRun.id == run_id)
        .with_for_update()
    )
    if run is None:
        raise LookupError(f"No run {run_id}.")
    return run


def _finish(run: m.AgentRun, status: str, error: str | None = None) -> None:
    run.status = status
    run.error = error
    run.finished_at = _now()
    run.locked_by = None
    run.locked_at = None


def switched_off(session: Session, tenant_id: UUID, playbook: str) -> str | None:
    """Why the agent may not run, if a kill switch is thrown: global, tenant or agent."""
    if not get_settings().agents_enabled:
        return DISABLED_EVERYWHERE
    policy = session.scalar(select(m.Tenant.policy).where(m.Tenant.id == tenant_id)) or {}
    if (policy.get("agents") or {}).get("enabled") is False:
        return DISABLED_FOR_TENANT
    enabled = session.scalar(
        select(m.AgentSetting.enabled).where(
            m.AgentSetting.tenant_id == tenant_id, m.AgentSetting.playbook == playbook
        )
    )
    return DISABLED_FOR_PLAYBOOK if enabled is False else None


def autonomy_level(session: Session, tenant_id: UUID, playbook: str) -> int:
    level = session.scalar(
        select(m.AgentSetting.autonomy_level).where(
            m.AgentSetting.tenant_id == tenant_id, m.AgentSetting.playbook == playbook
        )
    )
    # The first version implements Observe and Suggest only.
    return 1 if level is None else min(level, 1)


def assemble_context(
    session: Session, tenant_id: UUID, playbook: Playbook, run: m.AgentRun
) -> dict[str, Any]:
    """What the model is given besides the rules: nothing personal, no credentials."""
    policy = session.scalar(select(m.Tenant.policy).where(m.Tenant.id == tenant_id)) or {}
    p, a = m.Proposal, m.Approval
    decisions = dict(
        session.execute(
            select(p.status, func.count())
            .where(p.tenant_id == tenant_id, p.playbook == playbook.name)
            .group_by(p.status)
        ).all()
    )
    reasons = session.scalars(
        select(a.reason)
        .join(p, (p.tenant_id == a.tenant_id) & (p.id == a.proposal_id))
        .where(
            a.tenant_id == tenant_id,
            p.playbook == playbook.name,
            a.decision == "rejected",
            a.reason.is_not(None),
        )
        .order_by(a.decided_at.desc())
        .limit(RECENT_REASONS)
    )
    f = m.Finding
    by_state = {
        state: {"finding_count": count, "amount_minor": int(amount or 0)}
        for state, count, amount in session.execute(
            select(f.state, func.count(), func.sum(f.amount_minor))
            .where(f.tenant_id == tenant_id)
            .group_by(f.state)
        )
    }
    return {
        "trigger": run.trigger,
        "as_of": run.as_of.isoformat(),
        "autonomy_level": autonomy_level(session, tenant_id, playbook.name),
        "limits": {rule.type: rule.limits.model_dump() for rule in playbook.actions},
        "two_person_approval": bool((policy.get("approvals") or {}).get("two_person")),
        "past_decisions": {
            "approved_count": int(decisions.get("approved", 0)),
            "rejected_count": int(decisions.get("rejected", 0)),
            "pending_count": int(decisions.get("pending", 0)),
            # A person's own words: data, like any other text from outside.
            "recent_rejection_reasons": [tools.untrusted(reason) for reason in reasons],
        },
        "findings_by_state": by_state,
    }


def _exchanges(checkpoint: dict[str, Any]) -> tuple[Exchange, ...]:
    return tuple(
        Exchange(
            ref=item["ref"],
            call=ToolCall(item["name"], item["arguments"], item.get("call_id", "")),
            result=item["result"],
            turn=item["turn"],
        )
        for item in checkpoint.get("exchanges", [])
    )


def _results(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return {item["ref"]: item["result"] for item in checkpoint.get("exchanges", [])}


def _answer_tool_calls(
    tenant_id: UUID, run_id: UUID, playbook: Playbook, response: ModelResponse, turn: int
) -> None:
    """Run each call the model asked for, one transaction per call."""
    for call in response.tool_calls:
        with tenant_session(tenant_id) as session:
            run = _run(session, tenant_id, run_id)
            checkpoint = dict(run.checkpoint)
            exchanges = list(checkpoint.get("exchanges", []))
            ref = f"t{len(exchanges) + 1}"
            began = time.perf_counter()
            try:
                context = tools.ToolContext(session, tenant_id, run.as_of)
                result = tools.call(context, call.name, call.arguments, playbook.tools)
            except tools.ToolError as error:
                result = {"error": str(error)}
                checkpoint["refused_calls"] = [*checkpoint.get("refused_calls", []), call.name]
            exchanges.append(
                {
                    "ref": ref,
                    "name": call.name,
                    "arguments": audit.plain(call.arguments),
                    "call_id": call.call_id,
                    "turn": turn,
                    "result": result,
                }
            )
            checkpoint["exchanges"] = exchanges
            span = _span("tool", ref, began) | {"tool": call.name, "refused": "error" in result}
            checkpoint["trace"] = [*checkpoint.get("trace", []), span]
            trace.info(json.dumps({"trace_id": str(run_id), "playbook": playbook.name} | span))
            run.checkpoint = checkpoint
            audit.record(
                session,
                tenant_id,
                actor=actor(playbook.name),
                kind="tool_call",
                payload={"name": call.name, "arguments": call.arguments, "result": result},
                ref=f"run:{run_id}:{ref}",
            )


def _findings_named(
    session: Session, tenant_id: UUID, raw: dict[str, Any]
) -> dict[str, schema.FindingFacts]:
    """The tenant's findings among the ids the output names. Row-level security
    and the tenant filter both apply: another tenant's finding is simply not there."""
    named: set[UUID] = set()
    for proposal in raw.get("proposals") or []:
        ids = proposal.get("finding_ids") if isinstance(proposal, dict) else None
        for finding_id in ids if isinstance(ids, list) else []:
            try:
                named.add(UUID(str(finding_id)))
            except ValueError:
                continue
    if not named:
        return {}
    f = m.Finding
    return {
        str(row.id): schema.FindingFacts(row.type, row.state, row.amount_minor)
        for row in session.scalars(select(f).where(f.tenant_id == tenant_id, f.id.in_(named)))
    }


def _escalation(
    playbook: Playbook, output: schema.FindingAndProposalV1, results: dict[str, Any]
) -> str | None:
    if playbook.escalates_on_missing_contract and any(
        isinstance(result, dict) and result.get("contract_missing") is True
        for result in results.values()
    ):
        return "A fee could not be checked because no contract covers it. Add the contract."
    floor = playbook.confidence_floor_bps
    if floor is not None and schema.CONFIDENCE_BPS[output.confidence] < floor:
        return f"The agent's confidence was {output.confidence}: a person should look."
    return None


def _persist(
    session: Session,
    tenant_id: UUID,
    run: m.AgentRun,
    playbook: Playbook,
    output: schema.FindingAndProposalV1,
    results: dict[str, Any],
) -> None:
    texts = [output.headline, *output.points]
    stored: dict[str, Any] = output.model_dump() | {
        "rendered": {
            "headline": grounding.render(output.headline, results),
            "points": [grounding.render(point, results) for point in output.points],
        },
        "values": grounding.values(texts, results),
        "proposal_ids": [],
    }
    escalation = _escalation(playbook, output, results)
    level = autonomy_level(session, tenant_id, playbook.name)
    if escalation is None and level > OBSERVE:
        for proposed in output.proposals:
            proposal_id, _ = proposals.create(
                session, tenant_id, playbook, run.id, proposed, results
            )
            stored["proposal_ids"].append(str(proposal_id))
    run.output = stored
    _finish(run, "escalated" if escalation else "succeeded", escalation)


def execute(tenant_id: UUID, run_id: UUID, model: ModelClient) -> m.AgentRun:
    """Carry a claimed run through to the end, from wherever it had got to."""
    with tenant_session(tenant_id) as session:
        run = _run(session, tenant_id, run_id)
        if run.status != "running":
            return run
        try:
            playbook = get_playbook(run.playbook)
        except PlaybookError as error:
            _finish(run, "failed", str(error))
            return run
        off = switched_off(session, tenant_id, playbook.name)
        if off:
            _finish(run, "skipped", off)
            return run
        run.model = model.name
        if "context" not in run.checkpoint:
            run.checkpoint = dict(run.checkpoint) | {
                "context": assemble_context(session, tenant_id, playbook, run),
                "exchanges": [],
                "turns": 0,
            }
        checkpoint = dict(run.checkpoint)

    system = prompts.system(playbook)
    specs = tuple(tools.REGISTRY[name].spec for name in playbook.read_tools)
    output_schema = schema.SCHEMAS[playbook.output_schema].model_json_schema()
    while True:
        over = (
            checkpoint["turns"] >= MAX_TURNS
            or len(checkpoint.get("exchanges", [])) >= MAX_TOOL_CALLS
        )
        if over:
            return _close(tenant_id, run_id, "escalated", OVER_BUDGET)
        request = ModelRequest(
            playbook=playbook.name,
            system=system,
            context=checkpoint["context"],
            tools=specs,
            output_schema=output_schema,
            transcript=_exchanges(checkpoint),
            feedback=checkpoint.get("feedback"),
            provider_turns={
                int(turn): content for turn, content in checkpoint.get("provider_turns", {}).items()
            },
        )
        began = time.perf_counter()
        try:
            response = model.complete(request)
        except ModelError as error:
            return _model_failed(tenant_id, run_id, str(error))
        turn = checkpoint["turns"] + 1
        span = _span("model", f"turn{turn}", began, response.usage)
        with tenant_session(tenant_id) as session:
            run = _run(session, tenant_id, run_id)
            stored = dict(run.checkpoint) | {"turns": turn}
            stored["trace"] = [*stored.get("trace", []), span]
            used = dict(stored.get("usage") or {})
            for name, count in response.usage.items():
                used[name] = int(used.get(name, 0)) + int(count)
            stored["usage"] = used
            trace.info(json.dumps({"trace_id": str(run_id), "playbook": playbook.name} | span))
            if response.provider_turn is not None:
                stored["provider_turns"] = dict(stored.get("provider_turns", {})) | {
                    str(turn): response.provider_turn
                }
            run.checkpoint = stored
            audit.record(
                session,
                tenant_id,
                actor=actor(playbook.name),
                kind="model_call",
                payload={
                    "transcript": [exchange.ref for exchange in request.transcript],
                    "feedback": request.feedback,
                    "tool_calls": [[call.name, call.arguments] for call in response.tool_calls],
                    "output": response.output,
                },
                ref=f"run:{run_id}:turn{turn}",
            )

        if response.tool_calls:
            _answer_tool_calls(tenant_id, run_id, playbook, response, turn)
        else:
            done = _take_output(tenant_id, run_id, playbook, response.output or {})
            if done is not None:
                return done
        with tenant_session(tenant_id) as session:
            checkpoint = dict(_run(session, tenant_id, run_id).checkpoint)


def _take_output(
    tenant_id: UUID, run_id: UUID, playbook: Playbook, raw: dict[str, Any]
) -> m.AgentRun | None:
    """Validate and persist. None means the model is to try once more."""
    with tenant_session(tenant_id) as session:
        run = _run(session, tenant_id, run_id)
        results = _results(run.checkpoint)
        try:
            output = schema.validate(
                raw,
                playbook=playbook,
                results=results,
                findings=_findings_named(session, tenant_id, raw),
            )
        except schema.OutputInvalid as error:
            refused = [*run.checkpoint.get("refused_outputs", []), error.problems]
            if len(refused) > 1:
                # Invalid output is retried once, and no more.
                run.checkpoint = dict(run.checkpoint) | {"refused_outputs": refused}
                _finish(run, "failed", "The output was refused twice: " + "; ".join(error.problems))
                return run
            run.checkpoint = dict(run.checkpoint) | {
                "refused_outputs": refused,
                "feedback": "Your output was refused: " + "; ".join(error.problems),
            }
            return None
        _persist(session, tenant_id, run, playbook, output, results)
        return run


def _close(tenant_id: UUID, run_id: UUID, status: str, error: str) -> m.AgentRun:
    with tenant_session(tenant_id) as session:
        run = _run(session, tenant_id, run_id)
        _finish(run, status, error)
        return run


def _model_failed(tenant_id: UUID, run_id: UUID, error: str) -> m.AgentRun:
    """Back to the queue, checkpoint intact, until the attempts run out."""
    with tenant_session(tenant_id) as session:
        run = _run(session, tenant_id, run_id)
        if run.attempts >= MAX_ATTEMPTS:
            _finish(run, "failed", error)
        else:
            run.status = "queued"
            run.error = error
            run.locked_by = None
            run.locked_at = None
        return run
