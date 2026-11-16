"""Ask Revlane: a question in, a stream of events out.

1. Understand: a model turns the question into a plan of query specs and tool
   calls. It never writes SQL.
2. Compile: code turns each spec into a statement over the read-only views.
3. Analyse: for "why" questions, the same tools the agents use.
4. Answer: text whose figures are references to the results, a table or
   chart, and the ids of the queries behind it.
5. Act: the answer may offer an agent to run, which goes through approval.

A question that needs a metric the semantic layer does not define is refused,
and the refusal names what is missing.
"""

import re
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from revlane.chat import execute
from revlane.chat.compiler import SpecError, compile_spec
from revlane.chat.models import (
    CHAT_TOOLS,
    DIMENSION_WORDS,
    METRIC_WORDS,
    Answer,
    ChatModel,
    Plan,
    QueryStep,
    StubChat,
)
from revlane.chat.semantic import Layer, load
from revlane.config import Settings, get_settings
from revlane.connectors.base import IST
from revlane.db.session import tenant_session
from revlane.harness import audit, grounding, tools
from revlane.llm.client import ModelError

Event = dict[str, Any]
PLAIN_WORDS = re.compile(r"^[A-Za-z][A-Za-z ,'-]{0,78}$")
NOT_UNDERSTOOD = (
    "I could not turn that into a question I can answer. Try naming what you want to see."
)
SEE_TABLE = "Here is what the records show. The figures are in the table."
MODEL_DOWN = "The model could not be reached, so I cannot answer just now."


def get_chat_model(settings: Settings | None = None) -> ChatModel:
    settings = settings or get_settings()
    if settings.llm_mode == "stub":
        return StubChat()
    if settings.llm_provider != "anthropic":
        raise ModelError(f"No model provider called {settings.llm_provider}.")
    from revlane.chat.live import AnthropicChat

    return AnthropicChat(api_key=settings.llm_api_key, model=settings.llm_model)


def event(kind: str, /, **data: Any) -> Event:  # noqa: ANN401
    return {"event": kind, "data": data}


def refusal_for(missing: str | None, layer: Layer) -> str:
    """Names what is missing, in code's own words, and says what can be asked."""
    named = (missing or "").strip()
    if not PLAIN_WORDS.match(named) or grounding.bare_figures(named):
        named = "what that question asks for"
    known = ", ".join(METRIC_WORDS[name].lower() for name in layer.metrics)
    return (
        f"I can't answer that: no metric is defined for {named}. "
        f"I can answer questions about {known}."
    )


def _cell(key: str, value: Any) -> str:  # noqa: ANN401
    if value is None:
        return "—"
    try:
        return grounding.format_value(key, value)
    except grounding.GroundingError:
        return "—"


def _label(key: str) -> str:
    name = key.removesuffix("_bps")
    return METRIC_WORDS.get(name) or DIMENSION_WORDS.get(key, key).capitalize()


def table_for(result: dict[str, Any]) -> dict[str, Any]:
    keys = [*result["dimensions"], *result["metrics"]]
    return {
        "query_id": result["query_id"],
        "columns": [{"key": key, "label": _label(key)} for key in keys],
        "rows": [
            {"values": row, "display": {key: _cell(key, row[key]) for key in keys}}
            for row in result["rows"]
        ],
        "totals": {key: _cell(key, value) for key, value in result["totals"].items()},
    }


def ask(
    tenant_id: UUID, question: str, model: ChatModel, *, as_of: date | None = None
) -> Iterator[Event]:
    """Answer one question. Yields events as each step finishes."""
    as_of = as_of or datetime.now(tz=IST).date()
    layer = load()
    chat_id = uuid4()
    log: list[tuple[str, str, object]] = []  # kind, ref, payload

    def finish(*events: Event) -> Iterator[Event]:
        with tenant_session(tenant_id) as session:
            for kind, ref, payload in log:
                audit.record(session, tenant_id, actor="chat", kind=kind, payload=payload, ref=ref)
        yield from events
        yield event("done", chat_id=str(chat_id))

    yield event("step", name="understand", detail="Reading the question")
    try:
        raw = model.understand(question, layer)
        log.append(
            ("model_call", f"chat:{chat_id}:understand", {"question": question, "plan": raw})
        )
        plan = Plan.model_validate(raw)
    except ModelError:
        yield from finish(event("error", message=MODEL_DOWN))
        return
    except ValidationError:
        # Anything outside the plan's shape, a field holding SQL included, ends here.
        yield from finish(event("refusal", text=NOT_UNDERSTOOD))
        return
    if plan.kind == "refuse" or not plan.steps:
        yield from finish(event("refusal", text=refusal_for(plan.missing, layer)))
        return

    results: dict[str, Any] = {}
    sources: list[str] = []
    for step in plan.steps:
        if isinstance(step, QueryStep):
            query_id = f"q{sum(1 for key in results if key.startswith('q')) + 1}"
            yield event("step", name="compile", detail="Building the query", query_id=query_id)
            try:
                compiled = compile_spec(step.spec, layer, tenant_id, as_of)
            except SpecError as error:
                yield from finish(event("refusal", text=f"I can't answer that: {error}"))
                return
            yield event("query", query_id=query_id, spec=step.spec.model_dump())
            yield event("step", name="run", detail="Reading the records", query_id=query_id)
            try:
                result = execute.run(tenant_id, compiled, query_id)
            except execute.TooExpensive as error:
                yield from finish(event("refusal", text=str(error)))
                return
            results[query_id] = result
            sources.append(f"{chat_id}:{query_id}")
            log.append(("tool_call", f"chat:{chat_id}:{query_id}", step.spec.model_dump()))
            yield event("table", **table_for(result))
            if (
                len(result["dimensions"]) == 1
                and len(result["metrics"]) == 1
                and result["row_count"] > 1
            ):
                yield event(
                    "chart",
                    query_id=query_id,
                    type="bar",
                    label_key=result["dimensions"][0],
                    value_key=result["metrics"][0],
                )
        else:
            ref = f"t{sum(1 for key in results if key.startswith('t')) + 1}"
            yield event("step", name="analyse", detail=f"Asking {step.name}", tool=step.name)
            try:
                with tenant_session(tenant_id) as session:
                    context = tools.ToolContext(session, tenant_id, as_of)
                    results[ref] = tools.call(
                        context, step.name, step.arguments, frozenset(CHAT_TOOLS)
                    )
            except tools.ToolError as error:
                yield from finish(event("refusal", text=f"I can't answer that: {error}"))
                return
            sources.append(f"{chat_id}:{ref}")
            log.append(("tool_call", f"chat:{chat_id}:{ref}", {"name": step.name}))

    yield event("step", name="answer", detail="Writing the answer")
    template, next_step = SEE_TABLE, None
    try:
        raw_answer = model.answer(question, plan, results)
        log.append(("model_call", f"chat:{chat_id}:answer", raw_answer))
        written = Answer.model_validate(raw_answer)
        # Rule 2: an answer with a figure of its own is never shown.
        if not grounding.check_text(written.text, results):
            template, next_step = written.text, written.next_step
    except (ModelError, ValidationError):
        pass
    yield from finish(
        event(
            "answer",
            text=grounding.render(template, results),
            template=template,
            values=grounding.values([template], results),
            next_step={"kind": "run_agent", "playbook": next_step} if next_step else None,
        ),
        event("sources", query_ids=sources),
    )
