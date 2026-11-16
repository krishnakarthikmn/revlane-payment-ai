"""Fee contracts over the API: what the entry form calls."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from revlane.bootstrap import ensure_tenant
from revlane.config import get_settings
from revlane.main import create_app
from tests.signin import sign_in

URL = "/api/v1/fee-contracts"
AMEX = {
    "provider": "cashfree",
    "method": "Card",
    "card_network": " AMEX ",
    "rate_bps": 200,
    "effective_from": "2026-01-01",
}


@pytest.fixture(scope="module")
def client(database: None) -> Iterator[TestClient]:
    settings = get_settings()
    ensure_tenant(settings.demo_tenant_id, settings.demo_tenant_name)
    app = create_app()
    sign_in(app, settings.demo_tenant_id)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def amex(client: TestClient) -> dict[str, Any]:
    response = client.post(URL, json=AMEX)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def test_a_contract_is_created_with_defaults_and_tidied_values(amex: dict[str, Any]) -> None:
    assert amex == {
        "id": amex["id"],
        "provider": "cashfree",
        "account_ref": "default",
        "method": "card",
        "card_network": "amex",
        "card_type": None,
        "rate_bps": 200,
        "fixed_fee_minor": 0,
        "tax_rate_bps": 1800,
        "tax_on_refunded_fee": False,
        "allowed_fee_types": ["payment"],
        "effective_from": "2026-01-01",
        "effective_to": None,
    }


def test_contracts_are_listed(client: TestClient, amex: dict[str, Any]) -> None:
    body = client.get(URL).json()

    assert amex in body["items"]
    assert body["next_cursor"] is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"rate_bps": 2.15}, "rate_bps"),  # a rate is whole basis points, never a float
        ({"rate_bps": 10_001}, "rate_bps"),
        ({"fixed_fee_minor": -1}, "fixed_fee_minor"),
        ({"fixed_fee_minor": 3.5}, "fixed_fee_minor"),
        ({"provider": "stripe"}, "No provider called stripe"),
        ({"effective_to": "2025-12-31"}, "effective_to is before effective_from"),
        ({"method": " "}, "method"),
        ({"rate_percent": 2}, "rate_percent"),
    ],
)
def test_a_contract_that_does_not_make_sense_is_refused(
    client: TestClient, change: dict[str, Any], message: str
) -> None:
    response = client.post(URL, json=AMEX | {"card_network": "diners"} | change)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert message in response.json()["error"]["message"]


def test_two_contracts_for_the_same_card_cannot_overlap(
    client: TestClient, amex: dict[str, Any]
) -> None:
    overlapping = client.post(URL, json=AMEX | {"effective_from": "2026-06-01", "rate_bps": 190})

    assert overlapping.status_code == 409
    assert overlapping.json()["error"]["code"] == "conflict"
    assert "already in force from 2026-01-01" in overlapping.json()["error"]["message"]
    # A different card network is a different row and may run alongside.
    other = client.post(URL, json=AMEX | {"card_network": "visa", "rate_bps": 197})
    assert other.status_code == 201


def test_a_contract_is_ended_and_its_successor_added(
    client: TestClient, amex: dict[str, Any]
) -> None:
    ended = client.put(f"{URL}/{amex['id']}", json=AMEX | {"effective_to": "2026-05-31"})
    successor = client.post(URL, json=AMEX | {"effective_from": "2026-06-01", "rate_bps": 190})

    assert ended.status_code == 200
    assert ended.json()["effective_to"] == "2026-05-31"
    assert ended.json()["id"] == amex["id"]
    assert successor.status_code == 201
    # Stretching the first one back over its successor is refused.
    stretched = client.put(f"{URL}/{amex['id']}", json=AMEX)
    assert stretched.status_code == 409


def test_a_contract_is_deleted(client: TestClient) -> None:
    created = client.post(URL, json=AMEX | {"provider": "payu", "method": "wallet"}).json()

    assert client.delete(f"{URL}/{created['id']}").status_code == 204
    assert created["id"] not in [item["id"] for item in client.get(URL).json()["items"]]
    assert client.delete(f"{URL}/{created['id']}").status_code == 404
    assert client.put(f"{URL}/{created['id']}", json=AMEX).status_code == 404
