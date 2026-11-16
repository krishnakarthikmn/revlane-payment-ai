"""Milestone 1: the prototype's sample data is served from /api/v1/fixtures."""

from typing import Any

from fastapi.testclient import TestClient

from revlane.api.fixtures import load_fixtures
from revlane.main import create_app

LAKH_MINOR = 100_000 * 100


def get_fixtures() -> dict[str, Any]:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/fixtures")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def test_route_serves_the_fixture_file_unchanged() -> None:
    assert get_fixtures() == load_fixtures()


def test_merchant_and_user_match_the_prototype() -> None:
    merchant = get_fixtures()["merchant"]

    assert merchant["name"] == "Acme Travel"
    assert merchant["user"]["name"] == "Priya Menon"
    assert merchant["second_approver"] == {"name": "Arjun", "team": "Payments"}


def test_six_leaks_total_22_6_lakh() -> None:
    agents = get_fixtures()["agents"]

    assert [a["key"] for a in agents] == [
        "failure",
        "routing",
        "recon",
        "fees",
        "subs",
        "chargeback",
    ]
    assert sum(a["lost_minor"] for a in agents) == 226 * LAKH_MINOR // 10


def test_gateway_split_adds_up_to_the_same_total() -> None:
    data = get_fixtures()
    split = data["leak_chart"]["gateway_split"]

    assert [g["name"] for g in split] == ["Razorpay", "PayU", "Cashfree"]
    assert sum(g["amount_minor"] for g in split) == sum(a["lost_minor"] for a in data["agents"])


def test_reconciliation_and_fee_numbers_the_engines_must_reproduce() -> None:
    agents = {a["key"]: a for a in get_fixtures()["agents"]}

    assert agents["recon"]["lost_minor"] == 31_000_000
    assert agents["fees"]["lost_minor"] == 26_000_000


def test_initial_states_match_the_design_spec() -> None:
    agents = {a["key"]: a for a in get_fixtures()["agents"]}

    assert {k: a["initial_status"] for k, a in agents.items()} == {
        "failure": "pending",
        "routing": "pending",
        "recon": "pending",
        "fees": "pending",
        "chargeback": "approved",
        "subs": "approved",
    }
    assert {k: a["initial_autonomy"] for k, a in agents.items()} == {
        "failure": 1,
        "routing": 0,
        "recon": 1,
        "fees": 1,
        "chargeback": 1,
        "subs": 2,
    }


def test_every_agent_has_three_causes_four_facts_and_four_settings() -> None:
    for agent in get_fixtures()["agents"]:
        assert len(agent["causes"]) == 3
        assert sum(c["pct"] for c in agent["causes"]) == 100
        assert len(agent["meta"]) == 4
        assert len(agent["settings"]) == 4


def _money_values(node: object, key: str = "") -> list[object]:
    if isinstance(node, dict):
        return [v for k, child in node.items() for v in _money_values(child, k)]
    if isinstance(node, list):
        return [v for child in node for v in _money_values(child, key)]
    return [node] if key.endswith("_minor") else []


def test_money_is_integer_minor_units() -> None:
    values = _money_values(get_fixtures())

    assert values
    assert all(type(v) is int for v in values)


def test_every_reply_action_points_at_a_known_agent() -> None:
    data = get_fixtures()
    keys = {a["key"] for a in data["agents"]}

    for reply in data["chat"]["replies"].values():
        action = reply.get("action", "")
        if action.startswith("card:"):
            assert action.removeprefix("card:") in keys
        if "agent_key" in reply:
            assert reply["agent_key"] in keys


def test_every_routing_rule_has_a_reply() -> None:
    chat = get_fixtures()["chat"]

    assert {rule["kind"] for rule in chat["routing"]} | {"none"} == set(chat["replies"])
