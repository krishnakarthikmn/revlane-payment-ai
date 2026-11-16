"""The live chat model, against a stand-in for the SDK. Never the network."""

from typing import Any

import pytest
from anthropic.types.beta import BetaMessage

from revlane.chat import semantic
from revlane.chat.live import AnthropicChat, plan_format, tidy_plan
from revlane.chat.models import Plan, StubChat
from revlane.chat.service import get_chat_model
from revlane.config import Settings
from revlane.llm.client import ModelError

LAYER = semantic.load()


def message(text: str, stop_reason: str = "end_turn") -> BetaMessage:
    return BetaMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )


class FakeSdk:
    def __init__(self, *answers: BetaMessage) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []
        self.beta = type("Beta", (), {"messages": self})()
        self.messages = self

    def create(self, **kwargs: Any) -> BetaMessage:  # noqa: ANN401
        self.calls.append(kwargs)
        return self.answers.pop(0)


PLAN = (
    '{"kind": "answer", "missing": null, "steps": [{"kind": "query", "name": null, '
    '"arguments": null, "spec": {"metrics": ["lost_minor"], "dimensions": ["family"], '
    '"filters": [], "period": "last_seven_days", "order": "largest_first", "rows": "all"}}, '
    '{"kind": "tool", "spec": null, "name": "findings_for", "arguments": {"family": "fee_audit", '
    '"state": null, "type": null, "finding_id": null, "group_by": null, "provider": null, '
    '"method": null}}]}'
)


def test_understanding_asks_for_a_plan_in_a_format_with_no_room_for_sql() -> None:
    sdk = FakeSdk(message(PLAN))

    raw = AnthropicChat(client=sdk).understand("Where did we lose the most last week?", LAYER)

    [call] = sdk.calls
    assert call["model"] == "claude-sonnet-5-5"
    assert "never write SQL" in call["system"]
    assert '"payment_success_rate"' in call["system"]  # the catalogue
    assert call["messages"] == [
        {"role": "user", "content": "The question:\nWhere did we lose the most last week?"}
    ]
    assert "tools" not in call and "thinking" not in call
    schema = call["output_config"]["format"]["schema"]
    assert schema == plan_format(LAYER)
    spec = schema["properties"]["steps"]["items"]["properties"]["spec"]["anyOf"][0]
    # Metric and dimension names can only be ones the semantic layer defines.
    assert spec["properties"]["metrics"]["items"]["enum"] == sorted(LAYER.metrics)
    assert "tenant_id" not in spec["properties"]["dimensions"]["items"]["enum"]
    assert set(spec["properties"]) == {
        "metrics",
        "dimensions",
        "filters",
        "period",
        "order",
        "rows",
    }
    assert "sql" not in str(schema).lower()
    # What comes back is the plan the service validates.
    plan = Plan.model_validate(raw)
    assert [step.kind for step in plan.steps] == ["query", "tool"]
    assert raw["steps"][1] == {
        "kind": "tool",
        "name": "findings_for",
        "arguments": {"family": "fee_audit"},
    }


def test_a_refusal_from_the_model_keeps_what_is_missing() -> None:
    raw = tidy_plan({"kind": "refuse", "steps": [], "missing": "customer churn"})

    assert Plan.model_validate(raw).missing == "customer churn"


def test_answering_sends_the_results_and_asks_for_references() -> None:
    sdk = FakeSdk(message('{"text": "Lost: {{q1.totals.lost_minor}}.", "next_step": null}'))
    plan = Plan.model_validate(StubChat().understand("how much did we lose", LAYER))
    results = {"q1": {"totals": {"lost_minor": 57_000_000}, "rows": []}}

    answer = AnthropicChat(client=sdk).answer("how much did we lose", plan, results)

    [call] = sdk.calls
    assert "Never write a number" in call["system"]
    assert '"lost_minor":57000000' in call["messages"][0]["content"]
    assert answer == {"text": "Lost: {{q1.totals.lost_minor}}.", "next_step": None}


@pytest.mark.parametrize(
    ("reply", "problem"),
    [
        (message("no", "refusal"), "declined"),
        (message("SELECT * FROM payment_attempt"), "not JSON"),
        (message('{"kind": "ans', "max_tokens"), "ran out of room"),
    ],
)
def test_an_unusable_reply_is_a_model_error(reply: BetaMessage, problem: str) -> None:
    with pytest.raises(ModelError, match=problem):
        AnthropicChat(client=FakeSdk(reply)).understand("anything", LAYER)


def test_the_live_chat_model_is_used_only_with_a_key_and_stub_mode_off() -> None:
    live = Settings(_env_file=None, llm_api_key="not-a-real-key", llm_stub=False)

    assert get_chat_model(live).name == "anthropic:claude-sonnet-5-5"
    assert get_chat_model(Settings(_env_file=None, llm_api_key="")).name == "stub"
    assert get_chat_model(Settings(_env_file=None, llm_api_key="k", llm_stub=True)).name == "stub"
