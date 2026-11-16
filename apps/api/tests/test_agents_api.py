"""Agents over the API: queue a run, read its summary, decide its proposals,
download the drafted claim, and read the audit log."""

from collections.abc import Iterator
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from revlane.audit import store
from revlane.bootstrap import ensure_tenant
from revlane.config import get_settings
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.engine import findings as fd
from revlane.harness import queue, run
from revlane.llm.stub import StubClient
from revlane.main import create_app
from tests.signin import sign_in

API = "/api/v1"
AMOUNTS = (38_204, 35_608, 3_926_188)  # ₹40,000.00 in all


@pytest.fixture(scope="module")
def client(database: None) -> Iterator[TestClient]:
    settings = get_settings()
    ensure_tenant(settings.demo_tenant_id, settings.demo_tenant_name)
    cause = f"fee_type:test{uuid4().hex[:6]}"
    items = [
        fd.Item(f"setl_{n}/adj_{n}", amount, date(2026, 10, 9), (("settlement_lines", f"s/{n}"),))
        for n, amount in enumerate(AMOUNTS)
    ]
    [detected] = fd.group("fee_not_in_contract", "razorpay", cause, items, "1")
    with tenant_session(settings.demo_tenant_id) as session:
        store.apply(session, settings.demo_tenant_id, [fd.Create(detected, 1)])
    app = create_app()
    sign_in(app, settings.demo_tenant_id)
    with TestClient(app) as test_client:
        yield test_client


def work(run_id: str) -> None:
    """What `make worker` does, for one run."""
    tenant_id = get_settings().demo_tenant_id
    assert queue.claim(tenant_id, UUID(run_id), "test-worker")
    run.execute(tenant_id, UUID(run_id), StubClient())


@pytest.fixture(scope="module")
def finished(client: TestClient) -> dict[str, Any]:
    """A fee audit, queued through the API and carried out by a worker."""
    queued = client.post(f"{API}/agents/fee_audit/runs", json={"as_of": "2026-11-16"})
    assert queued.status_code == 202, queued.text
    again = client.post(f"{API}/agents/fee_audit/runs")
    assert again.json()["id"] == queued.json()["id"]
    assert (queued.json()["created"], again.json()["created"]) == (True, False)
    assert queued.json()["status"] == "queued"
    work(queued.json()["id"])
    done: dict[str, Any] = client.get(f"{API}/agents/runs/{queued.json()['id']}").json()
    return done


def own_proposal(client: TestClient, finished: dict[str, Any]) -> dict[str, Any]:
    proposals = [client.get(f"{API}/proposals/{pid}").json() for pid in finished["proposal_ids"]]
    found: dict[str, Any] = next(p for p in proposals if p["amount_minor"] == sum(AMOUNTS))
    return found


def test_the_agents_are_listed_with_their_settings(client: TestClient) -> None:
    items = {item["playbook"]: item for item in client.get(f"{API}/agents").json()["items"]}

    assert set(items) == {"leakage_audit", "reconciliation", "fee_audit"}
    assert items["reconciliation"]["actions"] == ["draft_settlement_query", "draft_reoffer_list"]
    assert (items["leakage_audit"]["autonomy_level"], items["leakage_audit"]["enabled"]) == (
        1,
        True,
    )
    assert "three_way_match" in items["leakage_audit"]["read_tools"]


def test_a_queued_run_is_carried_out_and_its_summary_can_be_read(finished: dict[str, Any]) -> None:
    assert (finished["status"], finished["error"], finished["model"]) == ("succeeded", None, "stub")
    assert finished["as_of"] == "2026-11-16"
    assert "in fees was charged outside the contracts" in finished["headline"]
    assert any("₹40,000" in point for point in finished["points"])
    assert finished["tool_calls"][0] == "findings_for"
    assert finished["proposal_ids"]


def test_proposals_are_listed_and_wait_as_pending(
    client: TestClient, finished: dict[str, Any]
) -> None:
    proposal = own_proposal(client, finished)
    pending = client.get(f"{API}/proposals", params={"status": "pending", "limit": 100}).json()

    assert proposal["id"] in [item["id"] for item in pending["items"]]
    assert (proposal["status"], proposal["action_type"]) == ("pending", "draft_fee_claim")
    assert proposal["title"] == "Claim ₹40,000 back from Razorpay"
    assert proposal["providers"] == ["razorpay"]
    assert (proposal["decisions"], proposal["claim_ids"], proposal["documents"]) == ([], [], [])
    assert client.get(f"{API}/proposals", params={"status": "sent"}).status_code == 422
    assert client.get(f"{API}/proposals/{uuid4()}").status_code == 404


def test_approving_drafts_a_claim_that_can_be_downloaded(
    client: TestClient, finished: dict[str, Any]
) -> None:
    proposal = own_proposal(client, finished)
    decisions = f"{API}/proposals/{proposal['id']}/decisions"

    approved = client.post(decisions, json={"decision": "approved"})
    twice = client.post(decisions, json={"decision": "approved"})

    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert [d["decision"] for d in body["decisions"]] == ["approved"]
    assert len(body["claim_ids"]) == 1 and len(body["documents"]) == 2
    assert twice.status_code == 409
    assert twice.json()["error"]["message"] == "This proposal is already approved."

    claim_id = body["claim_ids"][0]
    listed = client.get(f"{API}/claims", params={"limit": 100}).json()["items"]
    claim = next(item for item in listed if item["id"] == claim_id)
    assert (claim["provider"], claim["amount_minor"], claim["status"]) == (
        "razorpay",
        4_000_000,
        "drafted",
    )
    document = client.get(f"{API}/claims/{claim_id}/document.pdf")
    email = client.get(f"{API}/claims/{claim_id}/document.txt")
    assert document.headers["content-type"] == "application/pdf"
    assert document.content.startswith(b"%PDF-1.4")
    assert b"Amount claimed: INR 40,000.00" in document.content
    assert email.headers["content-type"].startswith("text/plain")
    assert (
        "Subject: Fees not provided for in the contract, October 2026: INR 40,000.00" in email.text
    )
    assert "setl_2/adj_2, 39261.88" in email.text
    assert client.get(f"{API}/claims/{claim_id}/document.docx").status_code == 404
    assert client.get(f"{API}/claims/{uuid4()}/document.pdf").status_code == 404
    # The finding is claimed; the decision is recorded against whoever is signed in.
    with tenant_session(get_settings().demo_tenant_id) as session:
        state = session.scalar(
            select(m.Finding.state).where(m.Finding.id == UUID(proposal["finding_ids"][0]))
        )
        approver = session.scalar(
            select(m.AppUser.email).where(m.AppUser.id == UUID(body["decisions"][0]["approver_id"]))
        )
    assert state == "claimed"
    assert approver == "admin@signed-in.example"


def test_a_decision_is_refused_for_a_missing_proposal_or_in_another_name(
    client: TestClient,
) -> None:
    missing = client.post(f"{API}/proposals/{uuid4()}/decisions", json={"decision": "rejected"})
    with tenant_session(get_settings().demo_tenant_id) as session:
        pending = session.scalar(select(m.Proposal.id).where(m.Proposal.status == "pending"))
    url = f"{API}/proposals/{pending}/decisions"

    assert missing.status_code == 404
    if pending is not None:
        named = client.post(url, json={"decision": "approved", "approver_id": str(uuid4())})
        assert named.status_code == 422
    assert client.post(url, json={"decision": "maybe"}).status_code == 422


def test_an_agent_can_be_switched_off_and_set_to_observe(client: TestClient) -> None:
    url = f"{API}/agents/reconciliation/settings"

    off = client.put(url, json={"autonomy_level": 0, "enabled": False})
    queued = client.post(f"{API}/agents/reconciliation/runs").json()
    work(queued["id"])
    skipped = client.get(f"{API}/agents/runs/{queued['id']}").json()
    listed = {i["playbook"]: i for i in client.get(f"{API}/agents").json()["items"]}

    assert off.json() == {"playbook": "reconciliation", "autonomy_level": 0, "enabled": False}
    assert (skipped["status"], skipped["error"]) == ("skipped", "This agent is switched off.")
    assert listed["reconciliation"]["enabled"] is False
    assert listed["reconciliation"]["last_run"]["id"] == queued["id"]
    # Batch and Auto are not built: the API refuses them.
    assert client.put(url, json={"autonomy_level": 2, "enabled": True}).status_code == 422
    assert (
        client.put(
            f"{API}/agents/nope/settings", json={"autonomy_level": 1, "enabled": True}
        ).status_code
        == 404
    )
    assert client.post(f"{API}/agents/nope/runs").status_code == 404
    assert client.get(f"{API}/agents/runs/{uuid4()}").status_code == 404
    client.put(url, json={"autonomy_level": 1, "enabled": True})


def test_the_audit_log_can_be_read_and_holds_only_hashes(
    client: TestClient, finished: dict[str, Any]
) -> None:
    first = client.get(f"{API}/audit-log", params={"limit": 3}).json()
    second = client.get(
        f"{API}/audit-log", params={"limit": 3, "cursor": first["next_cursor"]}
    ).json()
    approvals = client.get(f"{API}/audit-log", params={"kind": "approval"}).json()["items"]
    tools = client.get(f"{API}/audit-log", params={"kind": "tool_call", "limit": 100}).json()

    assert len(first["items"]) == 3 and first["next_cursor"]
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    assert set(first["items"][0]) == {"id", "actor", "kind", "ref", "payload_hash", "occurred_at"}
    assert approvals and approvals[0]["actor"].startswith("user:")
    assert f"run:{finished['id']}:t1" in [item["ref"] for item in tools["items"]]
    assert client.get(f"{API}/audit-log", params={"kind": "gossip"}).status_code == 422
