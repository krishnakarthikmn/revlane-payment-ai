"""The one door to a language model.

Everything the harness needs from a model goes through `ModelClient`. The
deterministic stub is the default: it is used in tests, in `make eval`, and
whenever no key is set. The live client is the only code that talks to a
provider, and it is given no personal data, card data or credentials.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from revlane.config import Settings, get_settings


class ModelError(RuntimeError):
    """The model could not be reached or gave an answer that cannot be read."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    # The provider's own id for the call, when it has one.
    call_id: str = ""


@dataclass(frozen=True, slots=True)
class Exchange:
    """One tool call the harness has answered. `ref` is how output refers to it."""

    ref: str
    call: ToolCall
    result: dict[str, Any]
    # Which model turn asked for it: calls made together share a turn.
    turn: int = 0


@dataclass(frozen=True, slots=True)
class ModelRequest:
    playbook: str
    system: str
    # Trigger, tenant policy, past decisions and a compact data summary.
    context: dict[str, Any]
    tools: tuple[ToolSpec, ...]
    output_schema: dict[str, Any]
    transcript: tuple[Exchange, ...] = ()
    # Why the last output was refused, when the model is asked to try again.
    feedback: str | None = None
    # Earlier turns exactly as a provider's model wrote them, by turn number,
    # so a live client can send them back unchanged.
    provider_turns: Mapping[int, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelResponse:
    tool_calls: tuple[ToolCall, ...] = ()
    # The final answer, to be validated against the output schema.
    output: dict[str, Any] | None = None
    usage: dict[str, int] = field(default_factory=dict)
    # This turn as the provider returned it. The stub has none.
    provider_turn: list[dict[str, Any]] | None = None


class ModelClient(Protocol):
    name: str

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Either ask for tools or give the final output."""


def get_client(settings: Settings | None = None) -> ModelClient:
    settings = settings or get_settings()
    if settings.llm_mode == "stub":
        from revlane.llm.stub import StubClient

        return StubClient()
    if settings.llm_provider != "anthropic":
        raise ModelError(f"No model provider called {settings.llm_provider}.")
    from revlane.llm.anthropic import AnthropicClient

    return AnthropicClient(api_key=settings.llm_api_key, model=settings.llm_model)
