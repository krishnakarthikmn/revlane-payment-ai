"""The live model client: Claude, through Anthropic's SDK.

One call here is one model turn. The harness owns the loop (it checkpoints
after every tool call), so this rebuilds the conversation from the run's
checkpoint each time: the context, then each earlier turn exactly as the
model wrote it, then the tool results.

What the model is sent: the rules, the playbook, aggregate context and tool
results. No personal data, card data or credentials are in any of them.
"""

import json
from collections.abc import Mapping
from typing import Any

import anthropic

from revlane.db.session import exact_json
from revlane.llm.client import (
    Exchange,
    ModelError,
    ModelRequest,
    ModelResponse,
    ToolCall,
)

# Decision 3: a mid-tier model unless LLM_MODEL names another.
DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 16_000
# Thinking is left adaptive; effort sets how hard the model works a multi-step run.
EFFORT = "medium"
# Models whose safety checks can decline a request and which can hand it to
# another model server-side instead of stopping.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
WITH_FALLBACK = frozenset(
    {
        "claude-sonnet-5-5",
        "claude-opus-5-5",
        "claude-opus-5",
        "claude-fable-5-2",
        "claude-fable-5-1",
    }
)

TEXT = {"type": "string"}
# The output schema as the API's structured outputs accept it: every field
# required, no length limits. The harness applies the full schema afterwards.
OUTPUT_FORMAT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": TEXT,
        "points": {"type": "array", "items": TEXT},
        "top_cause": {"anyOf": [TEXT, {"type": "null"}]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action_type": TEXT,
                    "finding_ids": {"type": "array", "items": TEXT},
                    "title": TEXT,
                    "rationale": TEXT,
                },
                "required": ["action_type", "finding_ids", "title", "rationale"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "points", "top_cause", "confidence", "proposals"],
    "additionalProperties": False,
}

OPENING = (
    "Run the playbook for this tenant. Use the tools to read what you need, then answer "
    'in the output format. Each tool result comes back as {"ref": ..., "result": ...}: '
    "refer to its values as {{ref.path}}.\n\nContext:\n"
)


def _tool_results(exchanges: list[Exchange]) -> dict[str, Any]:
    blocks = []
    for exchange in exchanges:
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": exchange.call.call_id or f"call_{exchange.ref}",
            "content": exact_json({"ref": exchange.ref, "result": exchange.result}),
        }
        if "error" in exchange.result:
            block["is_error"] = True
        blocks.append(block)
    return {"role": "user", "content": blocks}


def build_messages(request: ModelRequest) -> list[dict[str, Any]]:
    """The conversation so far, rebuilt from the run's checkpoint."""
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": OPENING + json.dumps(request.context, indent=1, sort_keys=True)}
    ]
    by_turn: dict[int, list[Exchange]] = {}
    for exchange in request.transcript:
        by_turn.setdefault(exchange.turn, []).append(exchange)
    for turn in sorted(by_turn.keys() | request.provider_turns.keys()):
        exchanges = by_turn.get(turn, [])
        # The turn as the model wrote it goes back unchanged, thinking blocks included.
        stored = request.provider_turns.get(turn)
        content = stored or [
            {
                "type": "tool_use",
                "id": exchange.call.call_id or f"call_{exchange.ref}",
                "name": exchange.call.name,
                "input": exchange.call.arguments,
            }
            for exchange in exchanges
        ]
        messages.append({"role": "assistant", "content": content})
        if exchanges:
            messages.append(_tool_results(exchanges))
    if request.feedback:
        messages.append({"role": "user", "content": request.feedback})
    return messages


def call(client: Any, model: str, arguments: dict[str, Any]) -> Any:  # noqa: ANN401
    """One request to the Messages API. Every SDK error becomes a `ModelError`
    whose message says what happened and nothing about the request."""
    try:
        if model in WITH_FALLBACK:
            return client.beta.messages.create(
                betas=[FALLBACK_BETA], fallbacks="default", model=model, **arguments
            )
        return client.messages.create(model=model, **arguments)
    except anthropic.AuthenticationError as error:
        raise ModelError("The model API key was not accepted.") from error
    except anthropic.RateLimitError as error:
        raise ModelError("The model API rate limit was reached.") from error
    except anthropic.BadRequestError as error:
        # The SDK's message can echo the request; say only that it was refused.
        raise ModelError("The model API refused the request as malformed.") from error
    except anthropic.APIStatusError as error:
        raise ModelError(f"The model API failed with status {error.status_code}.") from error
    except anthropic.APIConnectionError as error:
        raise ModelError("The model API could not be reached.") from error


def check_stop(message: Any) -> None:  # noqa: ANN401
    if message.stop_reason == "refusal":
        raise ModelError("The model declined to answer.")
    if message.stop_reason == "max_tokens":
        raise ModelError("The model ran out of room before it finished its answer.")


def json_answer(message: Any) -> dict[str, Any]:  # noqa: ANN401
    """The JSON object a structured-output answer carries."""
    text = next((block.text for block in message.content if block.type == "text"), None)
    if text is None:
        raise ModelError("The model gave no answer.")
    try:
        output = json.loads(text)
    except json.JSONDecodeError as error:
        raise ModelError("The model's answer is not JSON.") from error
    if not isinstance(output, Mapping):
        raise ModelError("The model's answer is not JSON in the output format.")
    return dict(output)


class AnthropicClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "",
        client: Any = None,  # noqa: ANN401  (the SDK client, or a stand-in in tests)
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self.name = f"anthropic:{self.model}"
        self._client = client or anthropic.Anthropic(api_key=api_key)

    def __repr__(self) -> str:
        return f"AnthropicClient({self.model})"

    def complete(self, request: ModelRequest) -> ModelResponse:
        arguments: dict[str, Any] = {
            "max_tokens": MAX_TOKENS,
            "system": request.system,
            "messages": build_messages(request),
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in request.tools
            ],
            "output_config": {
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": OUTPUT_FORMAT},
            },
            # The rules, the playbook and the tools are the same on every turn of a run.
            "cache_control": {"type": "ephemeral"},
        }
        message = call(self._client, self.model, arguments)
        return self._read(message)

    @staticmethod
    def _read(message: Any) -> ModelResponse:  # noqa: ANN401
        check_stop(message)
        usage = {
            "input_tokens": int(message.usage.input_tokens),
            "output_tokens": int(message.usage.output_tokens),
        }
        turn = message.to_dict()["content"]
        calls = tuple(
            ToolCall(block.name, dict(block.input), block.id)
            for block in message.content
            if block.type == "tool_use"
        )
        if calls:
            return ModelResponse(tool_calls=calls, usage=usage, provider_turn=turn)
        return ModelResponse(output=json_answer(message), usage=usage, provider_turn=turn)
