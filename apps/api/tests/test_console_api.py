"""The console's own endpoints: the live view, running the audit, rules and
simulation, and uploads that work out whose file they are."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from revlane.config import get_settings
from revlane.harness import queue
from revlane.llm.stub import StubClient
from revlane.main import create_app
from tests.merchant import AS_OF, Merchant, load_merchant
from tests.signin import sign_in

pytestmark = pytest.mark.usefixtures("database")

API = "/api/v1"


@pytest.fixture(scope="module")
def merchant(database: None, tmp_path_factory: pytest.TempPathFactory) -> Merchant:
    return load_merchant(tmp_path_factory.mktemp("console"), "console")


@pytest.fixture(scope="module")
def client(merchant: Merchant) -> Iterator[TestClient]:
    app = create_app()
    sign_in(app, merchant.tenant_id)
    # The queue is shared by every tenant: finish whatever earlier tests left
    # waiting, so the runs worked below are this merchant's.
    while queue.work_once("test", StubClient()) is not None:
        pass
    with TestClient(app) as test_client:
        yield test_client


def console(client: TestClient) -> dict[str, Any]:
    body: dict[str, Any] = client.get(f"{API}/console").json()
    return body


def test_before_any_audit_the_console_says_what_is_left_to_set_up(client: TestClient) -> None:
    view = console(client)

    assert view["merchant"] == {"name": "console"}
    assert view["sample_data"] is False  # only the demo tenant is the synthetic merchant
    assert view["setup"] == {"has_data": True, "has_contracts": True, "has_audit": False}
    assert view["audit"] == {"running": False, "last_finished_at": None}
    recon = view["agents"]["recon"]
    assert (recon["lost_minor"], recon["headline"], recon["proposals"]) == (0, None, [])
    assert recon["last_run"] is None
    assert view["notifications"] == []


def test_running_the_audit_queues_the_three_playbooks_in_order(
    client: TestClient, merchant: Merchant
) -> None:
    started = client.post(f"{API}/audit", json={"as_of": AS_OF.isoformat()})

    assert started.status_code == 202
    assert len(started.json()["run_ids"]) == 3
    assert console(client)["audit"]["running"] is True
    # A second press while it is running starts nothing new.
    assert client.post(f"{API}/audit").json()["run_ids"] == started.json()["run_ids"]
    worked = [queue.work_once("test", StubClient()) for _ in range(3)]
    assert [run.playbook for run in worked if run] == [
        "leakage_audit",
        "reconciliation",
        "fee_audit",
    ]
    assert {run.status for run in worked if run} == {"succeeded"}


def test_after_the_audit_the_two_real_agents_show_the_engines_numbers(client: TestClient) -> None:
    view = console(client)
    recon, fees = view["agents"]["recon"], view["agents"]["fees"]

    assert view["setup"]["has_audit"] is True and view["audit"]["running"] is False
    assert (recon["lost_minor"], fees["lost_minor"]) == (31_000_000, 26_000_000)
    assert recon["headline"] == "₹3.1L was paid but has not reached the bank, in 3 findings."
    assert fees["headline"] == "₹2.6L in fees was charged outside the contracts, across 3 findings."
    assert recon["causes"] == [
        {
            "label": "Payments refunded automatically on bookings that never confirmed at Razorpay",
            "pct": 42,
        },
        {"label": "Payouts lower than the sum of their lines at the marketplace", "pct": 39},
        {"label": "Captured payments still in no settlement at PayU", "pct": 19},
    ]
    assert recon["tools"] == "findings_for → evidence_for → bank_credits"
    assert [(p["action_type"], p["amount_minor"], p["status"]) for p in recon["proposals"]] == [
        ("draft_settlement_query", 12_000_000, "pending"),
        ("draft_settlement_query", 6_000_000, "pending"),
        ("draft_reoffer_list", 13_000_000, "pending"),
    ]
    assert recon["proposals"][0]["title"] == "Ask the marketplace about ₹1.2L not settled in full"
    assert recon["meta"] == [
        {"k": "Findings", "v": "3"},
        {"k": "Records", "v": "65"},
        {"k": "Waiting for you", "v": "3"},
        {"k": "At stake", "v": "₹3.1L"},
    ]
    assert [rule["key"] for rule in recon["rules"]] == [
        "grace_working_days",
        "bank_window_days",
        "group_tolerance_minor",
    ]
    assert (recon["autonomy_level"], recon["enabled"]) == (1, True)
    assert recon["last_run"] == {"status": "succeeded", "error": None}


def test_notifications_come_from_findings_and_proposals(client: TestClient) -> None:
    notes = console(client)["notifications"]

    titles = [note["title"] for note in notes]
    assert "A fix is waiting for you" in titles
    assert "New finding: ₹1.6L" in titles
    new = next(note for note in notes if note["title"] == "New finding: ₹1.6L")
    assert new["body"] == "Fees charged above the contract rate at Cashfree."
    assert new["link"] == "/agents/fees"
    assert all(set(note) == {"id", "title", "body", "dot", "link", "time"} for note in notes)
    assert [note["time"] for note in notes] == sorted((n["time"] for n in notes), reverse=True)


def test_an_approval_shows_up_with_its_documents(client: TestClient) -> None:
    query = console(client)["agents"]["recon"]["proposals"][0]

    decided = client.post(f"{API}/proposals/{query['id']}/decisions", json={"decision": "approved"})

    assert decided.status_code == 200, decided.text
    after = {p["id"]: p for p in console(client)["agents"]["recon"]["proposals"]}[query["id"]]
    assert after["status"] == "approved"
    assert len(after["claim_ids"]) == 1 and len(after["documents"]) == 2
    states = {f["label"]: f["state"] for f in console(client)["agents"]["recon"]["findings"]}
    assert states["Payouts lower than the sum of their lines at the marketplace"] == "claimed"
    assert "Claim drafted: ₹1.2L" in [n["title"] for n in console(client)["notifications"]]


def test_rules_can_be_simulated_and_saved(client: TestClient) -> None:
    url = f"{API}/agents/reconciliation"
    assert client.get(f"{url}/rules").json()["rules"] == {
        "grace_working_days": 2,
        "bank_window_days": 3,
        "group_tolerance_minor": 100,
    }

    # Waiting three weeks before calling a payment late hides the unsettled ones.
    patient = client.post(f"{url}/simulate", json={"rules": {"grace_working_days": 15}}).json()
    same = client.post(f"{url}/simulate", json={"rules": {}}).json()

    assert (
        same["with_these_rules"]
        == same["with_current_rules"]
        == {
            "finding_count": 3,
            "amount_minor": 31_000_000,
            "record_count": 65,
        }
    )
    assert patient["with_these_rules"] == {
        "finding_count": 2,
        "amount_minor": 25_000_000,
        "record_count": 43,
    }
    # Simulating stores nothing.
    assert client.get(f"{url}/rules").json()["rules"]["grace_working_days"] == 2
    saved = client.put(f"{url}/rules", json={"rules": {"bank_window_days": 5}})
    assert saved.json()["rules"] == {
        "grace_working_days": 2,
        "bank_window_days": 5,
        "group_tolerance_minor": 100,
    }
    assert console(client)["agents"]["recon"]["rules"][1]["value"] == 5
    client.put(f"{url}/rules", json={"rules": {"bank_window_days": 3}})


def test_a_stricter_fee_tolerance_is_simulated_against_the_same_lines(client: TestClient) -> None:
    url = f"{API}/agents/fee_audit/simulate"

    lenient = client.post(url, json={"rules": {"tolerance_minor": 20_000}}).json()

    assert lenient["with_current_rules"]["amount_minor"] == 26_000_000
    assert lenient["with_current_rules"]["record_count"] == 1_912
    # A ₹200 threshold lets most of the small overcharges through.
    assert 0 < lenient["with_these_rules"]["record_count"] < 1_912


@pytest.mark.parametrize(
    ("path", "rules", "status"),
    [
        ("reconciliation", {"grace_working_days": 31}, 422),
        ("reconciliation", {"grace_working_days": -1}, 422),
        ("reconciliation", {"grace_working_days": 2.5}, 422),
        ("reconciliation", {"tolerance_minor": 5}, 422),
        ("fee_audit", {"bank_window_days": 3}, 422),
        ("leakage_audit", {}, 404),
        ("payroll", {}, 404),
    ],
)
def test_rules_outside_what_an_agent_has_are_refused(
    client: TestClient, path: str, rules: dict[str, Any], status: int
) -> None:
    saved = client.put(f"{API}/agents/{path}/rules", json={"rules": rules})
    simulated = client.post(f"{API}/agents/{path}/simulate", json={"rules": rules})

    assert (saved.status_code, simulated.status_code) == (status, status), saved.text


def test_an_upload_works_out_whose_file_it_is(client: TestClient, merchant: Merchant) -> None:
    statement = (merchant.path / "bank").glob("*.csv").__next__()
    payments = merchant.path / "cashfree" / "payments.jsonl"

    bank = client.post(
        f"{API}/connectors/uploads",
        params={"filename": statement.name},
        content=statement.read_bytes(),
    )
    cashfree = client.post(
        f"{API}/connectors/uploads",
        params={"filename": "export.jsonl"},
        content=payments.read_bytes(),
    )
    unknown = client.post(
        f"{API}/connectors/uploads", params={"filename": "notes.csv"}, content=b"a,b\n1,2\n"
    )
    wrong_kind = client.post(
        f"{API}/connectors/uploads", params={"filename": "notes.pdf"}, content=b"%PDF"
    )

    assert (bank.status_code, bank.json()["provider"]) == (200, "bank")
    assert set(bank.json()["added"].values()) == {0}  # already ingested
    assert cashfree.json()["provider"] == "cashfree"
    assert unknown.status_code == 422
    assert "does not look like a file from any provider" in unknown.json()["error"]["message"]
    assert (wrong_kind.status_code, "only .csv and .jsonl" in wrong_kind.text) == (422, True)


def test_with_no_worker_the_api_carries_the_audit_out_itself(
    merchant: Merchant, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "agent_runs_inline", True)
    app = create_app()
    sign_in(app, merchant.tenant_id)
    with TestClient(app) as inline:
        started = inline.post(f"{API}/audit", json={"as_of": AS_OF.isoformat()})
        # The test client finishes background work before it returns.
        runs = [
            inline.get(f"{API}/agents/runs/{run_id}").json() for run_id in started.json()["run_ids"]
        ]

    assert [run["status"] for run in runs] == ["succeeded", "succeeded", "succeeded"]
