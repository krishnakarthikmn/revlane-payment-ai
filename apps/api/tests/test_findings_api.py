"""Findings over the API: list, detail, and moving through the lifecycle."""

from collections.abc import Iterator
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from revlane.audit import store
from revlane.bootstrap import ensure_tenant
from revlane.config import get_settings
from revlane.db.session import tenant_session
from revlane.engine import findings as fd
from revlane.main import create_app
from tests.signin import sign_in

URL = "/api/v1/findings"


def found(kind: str, provider: str, cause: str, *amounts: int) -> fd.Detected:
    items = [
        fd.Item(f"{cause}-{n}", amount, date(2026, 11, 10), (("attempts", f"{cause}-{n}"),))
        for n, amount in enumerate(amounts)
    ]
    [detected] = fd.group(kind, provider, cause, items, "1")
    return detected


@pytest.fixture(scope="module")
def client(database: None) -> Iterator[TestClient]:
    settings = get_settings()
    ensure_tenant(settings.demo_tenant_id, settings.demo_tenant_name)
    # Causes are unique to this run, so other tests' findings do not get in the way.
    run = uuid4().hex[:8]
    with tenant_session(settings.demo_tenant_id) as session:
        store.apply(
            session,
            settings.demo_tenant_id,
            [
                fd.Create(found("captured_not_settled", "payu", f"a{run}", 300, 200), 1),
                fd.Create(found("settled_short", "marketplace", f"b{run}", 4_000), 1),
                fd.Create(found("fee_above_contract", "cashfree", f"c{run}", 10, 20, 30), 1),
            ],
        )
    app = create_app()
    sign_in(app, settings.demo_tenant_id)
    with TestClient(app) as test_client:
        yield test_client


def test_findings_are_listed_newest_first_without_their_evidence(client: TestClient) -> None:
    body = client.get(URL, params={"type": "captured_not_settled"}).json()

    finding = body["items"][0]
    assert finding["type"] == "captured_not_settled"
    assert (finding["provider"], finding["amount_minor"], finding["state"]) == (
        "payu",
        500,
        "recoverable",
    )
    assert finding["cause"].startswith("a")
    assert (finding["period_start"], finding["period_end"]) == ("2026-11-01", "2026-11-30")
    assert finding["record_counts"] == {"attempts": 2}
    assert "evidence" not in finding


def test_the_list_filters_by_state_and_type(client: TestClient) -> None:
    short = client.get(URL, params={"type": "settled_short", "state": "recoverable"}).json()
    none = client.get(URL, params={"type": "settled_short", "state": "recovered"}).json()

    assert {item["type"] for item in short["items"]} == {"settled_short"}
    assert none["items"] == []
    assert client.get(URL, params={"state": "paid"}).status_code == 422
    assert client.get(URL, params={"type": "chargeback"}).status_code == 422


def test_the_list_pages_with_a_cursor(client: TestClient) -> None:
    everything = client.get(URL, params={"limit": 100}).json()["items"]
    first = client.get(URL, params={"limit": 2}).json()
    second = client.get(URL, params={"limit": 2, "cursor": first["next_cursor"]}).json()

    assert len(everything) >= 3
    assert [item["id"] for item in first["items"] + second["items"]] == [
        item["id"] for item in everything[:4]
    ]
    assert first["next_cursor"]
    assert client.get(URL, params={"cursor": "not-a-cursor"}).status_code == 422
    assert client.get(URL, params={"limit": 0}).status_code == 422


def test_a_finding_shows_its_evidence_and_history(client: TestClient) -> None:
    listed = client.get(URL, params={"type": "fee_above_contract"}).json()["items"][0]

    finding = client.get(f"{URL}/{listed['id']}").json()

    assert finding["amount_minor"] == 60
    assert len(finding["evidence"]["attempts"]) == 3
    assert [item["amount_minor"] for item in finding["evidence"]["items"]] == [10, 20, 30]
    assert [(e["from_state"], e["to_state"], e["actor"]) for e in finding["events"]] == [
        (None, "recoverable", "engine")
    ]
    assert client.get(f"{URL}/{uuid4()}").status_code == 404


def test_a_finding_moves_through_its_lifecycle(client: TestClient) -> None:
    listed = client.get(URL, params={"type": "settled_short"}).json()["items"][0]
    moves = f"{URL}/{listed['id']}/transitions"

    claimed = client.post(moves, json={"to_state": "claimed", "reason": "Claim emailed"})
    refused = client.post(moves, json={"to_state": "lost"})
    recovered = client.post(moves, json={"to_state": "recovered"})

    assert claimed.status_code == 200
    assert claimed.json()["state"] == "claimed"
    assert refused.status_code == 409
    assert refused.json()["error"] == {
        "code": "conflict",
        "message": "A finding cannot move from claimed to lost.",
    }
    assert recovered.json()["state"] == "recovered"
    assert [
        (e["from_state"], e["to_state"], e["actor"].split(":")[0], e["reason"])
        for e in recovered.json()["events"][1:]
    ] == [
        ("recoverable", "claimed", "user", "Claim emailed"),
        ("claimed", "recovered", "user", None),
    ]
    assert client.post(f"{URL}/{uuid4()}/transitions", json={"to_state": "lost"}).status_code == 404
    assert client.post(moves, json={"to_state": "paid"}).status_code == 422
