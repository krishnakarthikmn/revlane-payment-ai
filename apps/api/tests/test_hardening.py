"""Milestone 8: audit-log export, retention, erasure, offboarding, request
limits, upload type checks, tracing per run and metrics."""

import csv
import io
import json
import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from revlane import lifecycle
from revlane.api import limits, metrics
from revlane.bootstrap import ensure_tenant
from revlane.config import get_settings
from revlane.connectors.sanitize import shopper_token, tenant_token_key
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.harness import queue
from revlane.llm.client import ModelRequest, ModelResponse
from revlane.llm.stub import StubClient
from revlane.main import create_app
from revlane.vault import get_vault
from revlane.vault.base import SecretNotFound
from tests.merchant import AS_OF, Merchant, load_merchant
from tests.seed import seed_tenant
from tests.signin import sign_in

pytestmark = pytest.mark.usefixtures("database")

API = "/api/v1"


@pytest.fixture(scope="module")
def merchant(database: None, tmp_path_factory: pytest.TempPathFactory) -> Merchant:
    loaded = load_merchant(tmp_path_factory.mktemp("hardening"), "hardening")
    queue.run_now(loaded.tenant_id, "leakage_audit", StubClient(), as_of=AS_OF)
    return loaded


@pytest.fixture(scope="module")
def client(merchant: Merchant) -> Iterator[TestClient]:
    app = create_app()
    sign_in(app, merchant.tenant_id)
    with TestClient(app) as test_client:
        yield test_client


def count(tenant_id: UUID, model: type[Any]) -> int:
    with tenant_session(tenant_id) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


# --- Audit-log export --------------------------------------------------------------------


def test_the_audit_log_is_exported_whole_as_csv(client: TestClient, merchant: Merchant) -> None:
    response = client.get(f"{API}/audit-log/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "revlane-audit-log.csv" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == count(merchant.tenant_id, m.AuditLog) > 20
    assert list(rows[0]) == ["occurred_at", "kind", "actor", "ref", "payload_hash", "id"]
    assert {row["kind"] for row in rows} >= {"connector_call", "model_call", "tool_call"}
    assert [row["occurred_at"] for row in rows] == sorted(row["occurred_at"] for row in rows)
    # Hashes only: nothing a provider sent is in the export.
    assert all(len(row["payload_hash"]) == 64 for row in rows)
    assert "amount" not in response.text and "BK-" not in response.text


# --- Tracing per run ----------------------------------------------------------------------


def test_every_run_has_a_trace_of_its_model_turns_and_tool_calls(
    merchant: Merchant,
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Metered(StubClient):
        def complete(self, request: ModelRequest) -> ModelResponse:
            response = super().complete(request)
            return ModelResponse(
                tool_calls=response.tool_calls,
                output=response.output,
                usage={"input_tokens": 1_000, "output_tokens": 50},
            )

    # Alembic's logging setup, run by the test database fixture, switches off loggers
    # that already exist. A real process never loads it alongside the API.
    monkeypatch.setattr(logging.getLogger("revlane.trace"), "disabled", False)
    with caplog.at_level(logging.INFO, logger="revlane.trace"):
        run = queue.run_now(merchant.tenant_id, "fee_audit", Metered(), as_of=AS_OF)

    trace = client.get(f"{API}/agents/runs/{run.id}/trace").json()
    assert (trace["trace_id"], trace["playbook"], trace["status"]) == (
        str(run.id),
        "fee_audit",
        "succeeded",
    )
    assert [(span["kind"], span["name"]) for span in trace["spans"]] == [
        ("model", "turn1"),
        ("tool", "t1"),
        ("model", "turn2"),
        ("tool", "t2"),
        ("tool", "t3"),
        ("tool", "t4"),
        ("model", "turn3"),
    ]
    assert trace["spans"][1]["tool"] == "findings_for"
    assert all(span["ms"] >= 0 and span["at"] for span in trace["spans"])
    assert trace["usage"] == {"input_tokens": 3_000, "output_tokens": 150}
    # The same steps go to the log, one line of JSON each, tied together by the run's id.
    logged = [json.loads(record.message) for record in caplog.records]
    assert len(logged) == 7
    assert {line["trace_id"] for line in logged} == {str(run.id)}
    assert client.get(f"{API}/agents/runs/{uuid4()}/trace").status_code == 404


# --- Metrics ------------------------------------------------------------------------------


def test_metrics_report_sync_lag_unmapped_statuses_findings_and_model_use(
    client: TestClient, merchant: Merchant
) -> None:
    token = get_settings().metrics_token
    response = client.get(f"{API}/metrics", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    for name in (
        "revlane_sync_lag_seconds",
        "revlane_unmapped_status_attempts",
        "revlane_findings",
        "revlane_findings_amount_minor",
        "revlane_agent_runs",
        "revlane_model_calls_total",
        "revlane_model_tokens_total",
    ):
        assert f"# TYPE {name} " in text and f"# HELP {name} " in text
    assert 'revlane_sync_lag_seconds{provider="razorpay"}' in text
    assert 'revlane_unmapped_status_attempts{provider="payu"}' in text
    assert 'revlane_findings{state="recoverable",type="fee_above_contract"}' in text
    assert 'revlane_model_calls_total{surface="agent"}' in text
    assert 'revlane_model_tokens_total{direction="input",model="stub"}' in text
    # Aggregates only: no tenant is named.
    assert str(merchant.tenant_id) not in text and "tenant" not in text


def test_sync_lag_is_measured_from_the_least_recently_synced_account() -> None:
    rendered = metrics.render(
        [("revlane_x", "gauge", "Help.", [({"b": "2", "a": "1"}, 7), ({}, 3)])]
    )

    assert (
        rendered
        == '# HELP revlane_x Help.\n# TYPE revlane_x gauge\nrevlane_x{a="1",b="2"} 7\nrevlane_x 3\n'
    )
    assert "revlane_sync_lag_seconds" in metrics.collect(datetime.now(tz=UTC) + timedelta(days=1))


# --- Erasure ------------------------------------------------------------------------------


def test_erasing_a_shopper_removes_their_token_and_nothing_else(
    client: TestClient, merchant: Merchant
) -> None:
    tenant_id = merchant.tenant_id
    key = tenant_token_key(get_settings().token_hash_root_key, tenant_id)
    token = shopper_token(key, "email:someone@example.com")
    with tenant_session(tenant_id) as session:
        attempts = list(
            session.scalars(
                select(m.PaymentAttempt.attempt_id).limit(3).order_by(m.PaymentAttempt.attempt_id)
            )
        )
        session.execute(
            update(m.PaymentAttempt)
            .where(m.PaymentAttempt.attempt_id.in_(attempts))
            .values(customer_token=token)
        )
    before = count(tenant_id, m.PaymentAttempt)

    erased = client.post(f"{API}/privacy/erasures", json={"email": " Someone@Example.com "})
    again = client.post(f"{API}/privacy/erasures", json={"customer_token": token})

    assert erased.json() == {"erased": {"payment_attempt": 3, "mandate": 0}}
    assert again.json() == {"erased": {"payment_attempt": 0, "mandate": 0}}
    assert count(tenant_id, m.PaymentAttempt) == before  # the payments stay; the link goes
    with tenant_session(tenant_id) as session:
        left = session.scalar(
            select(func.count())
            .select_from(m.PaymentAttempt)
            .where(m.PaymentAttempt.customer_token == token)
        )
        logged = session.execute(
            select(m.AuditLog.actor, m.AuditLog.ref).where(m.AuditLog.actor == "erasure")
        ).all()
    assert left == 0
    # The log records that an erasure happened, and nothing about whose.
    assert logged == [("erasure", "erasure"), ("erasure", "erasure")]
    assert client.post(f"{API}/privacy/erasures", json={}).status_code == 422
    assert client.post(f"{API}/privacy/erasures", json={"name": "x"}).status_code == 422


# --- Retention ----------------------------------------------------------------------------


def test_retention_deletes_old_provider_records_and_keeps_the_record_of_decisions(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    kept = load_merchant(tmp_path_factory.mktemp("retention"), "retention")
    tenant_id = kept.tenant_id
    queue.run_now(tenant_id, "leakage_audit", StubClient(), as_of=AS_OF)
    tables = (
        m.PaymentAttempt,
        m.AttemptEvent,
        m.Settlement,
        m.SettlementLine,
        m.BankCredit,
        m.Order,
    )
    before = {model.__tablename__: count(tenant_id, model) for model in tables}
    findings, audit_rows = count(tenant_id, m.Finding), count(tenant_id, m.AuditLog)

    # No period set: nothing happens.
    assert lifecycle.apply_retention(tenant_id, today=AS_OF) == {}
    with tenant_session(tenant_id) as session:
        session.execute(update(m.Tenant).values(policy={"retention": {"days": 30}}))

    removed = lifecycle.apply_retention(tenant_id, today=AS_OF)

    cutoff = datetime(2026, 10, 17, tzinfo=UTC)
    after = {model.__tablename__: count(tenant_id, model) for model in tables}
    assert all(0 < after[name] < before[name] for name in before), after
    assert removed["payment_attempt"] == before["payment_attempt"] - after["payment_attempt"]
    with tenant_session(tenant_id) as session:
        oldest = session.scalar(select(func.min(m.PaymentAttempt.first_seen_at)))
        orphans = session.scalar(
            select(func.count())
            .select_from(m.SettlementLine)
            .outerjoin(
                m.Settlement,
                (m.Settlement.provider == m.SettlementLine.provider)
                & (m.Settlement.settlement_id == m.SettlementLine.settlement_id),
            )
            .where(m.Settlement.id.is_(None))
        )
    assert oldest is not None and oldest >= cutoff - timedelta(hours=6)
    assert orphans == 0
    # What was found and decided is kept, and the run is itself on the record.
    assert count(tenant_id, m.Finding) == findings
    assert count(tenant_id, m.AuditLog) == audit_rows + 1
    # Running it again the same day removes nothing more.
    assert sum(lifecycle.apply_retention(tenant_id, today=AS_OF).values()) == 0
    assert tenant_id in lifecycle.apply_retention_everywhere(today=AS_OF + timedelta(days=5))


def test_a_retention_period_too_short_to_be_meant_is_refused() -> None:
    assert lifecycle.retention_days({}) is None
    assert lifecycle.retention_days({"retention": {"days": 365}}) == 365
    assert lifecycle.retention_days({"retention": {"days": "90"}}) is None
    with pytest.raises(ValueError, match="at least 30 days"):
        lifecycle.retention_days({"retention": {"days": 1}})


# --- Offboarding --------------------------------------------------------------------------


def test_offboarding_deletes_credentials_at_once_and_stops_the_agents() -> None:
    from revlane.db.session import admin_session

    with admin_session() as session:
        tenant_id = seed_tenant(session, f"leaving-{uuid4().hex[:6]}")
    vault = get_vault()
    ref = vault.put(tenant_id, "razorpay", "not-a-real-secret")
    other = uuid4()
    ensure_tenant(other, "staying")
    kept = vault.put(other, "razorpay", "someone-elses")
    with tenant_session(tenant_id) as session:
        session.execute(update(m.ProviderAccount).values(credential_ref=str(ref)))
    queued, _ = queue.enqueue(tenant_id, "fee_audit", as_of=date(2026, 11, 16))

    done = lifecycle.offboard(tenant_id)

    # The seeded secret shares the name, so there was one to delete.
    assert done == {"secrets_deleted": 1, "accounts_revoked": 1, "runs_stopped": 2}
    with pytest.raises(SecretNotFound):
        vault.get(ref)
    assert vault.get(kept) == "someone-elses"
    with tenant_session(tenant_id) as session:
        account = session.scalar(select(m.ProviderAccount))
        run = session.get(m.AgentRun, queued)
        logged = session.scalar(select(m.AuditLog.kind).where(m.AuditLog.actor == "offboarding"))
    assert account is not None and (account.credential_ref, account.status) == (None, "revoked")
    assert run is not None and (run.status, run.error) == ("skipped", lifecycle.OFFBOARDED)
    assert logged == "action"
    # Nothing new can start: the tenant's agents are switched off.
    blocked = queue.run_now(tenant_id, "fee_audit", StubClient(), as_of=date(2026, 11, 16))
    assert (blocked.status, blocked.error) == (
        "skipped",
        "Agents are switched off for this tenant.",
    )
    with pytest.raises(LookupError):
        lifecycle.offboard(uuid4())


# --- Rate limits, request size limits, upload type checks ---------------------------------


def test_a_client_is_limited_per_minute_and_per_kind_of_route() -> None:
    now = [0.0]
    limiter = limits.Limiter({"default": 3, "chat": 1, "upload": 2}, clock=lambda: now[0])

    assert [limiter.check("a", "default") for _ in range(4)] == [0, 0, 0, 61]
    assert limiter.check("b", "default") == 0  # another client has its own allowance
    assert [limiter.check("a", "chat"), limiter.check("a", "chat")] == [0, 61]
    now[0] = 45.0
    assert limiter.check("a", "default") == 16
    now[0] = 60.0
    assert limiter.check("a", "default") == 0  # a new minute
    assert [
        limits.group_for(path)
        for path in ("/api/v1/chat", "/api/v1/connectors/bank/uploads", "/api/v1/findings")
    ] == ["chat", "upload", "default"]


def test_too_many_requests_and_too_large_a_request_are_refused(
    merchant: Merchant, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    monkeypatch.setattr(settings, "rate_limit_chat_per_minute", 1)
    monkeypatch.setattr(settings, "max_body_bytes", 200)
    app = create_app()
    sign_in(app, merchant.tenant_id)
    with TestClient(app) as limited:
        large = limited.post(f"{API}/chat", json={"question": "x" * 400})
        asked = [
            limited.post(f"{API}/chat", json={"question": "What is our churn?"}) for _ in range(2)
        ]
        listed = [limited.get(f"{API}/findings") for _ in range(4)]
        health = [limited.get(f"{API}/health") for _ in range(6)]

    assert large.status_code == 413
    assert large.json()["error"]["code"] == "payload_too_large"
    assert [r.status_code for r in asked] == [200, 429]
    assert [r.status_code for r in listed] == [200, 200, 200, 429]
    assert listed[3].json()["error"] == {
        "code": "rate_limited",
        "message": "Too many requests. Try again shortly.",
    }
    assert 1 <= int(listed[3].headers["retry-after"]) <= 61
    # Health checks are never limited.
    assert {r.status_code for r in health} == {200}


@pytest.mark.parametrize(
    ("filename", "body", "content_type", "status", "words"),
    [
        ("statement.csv", b"a,b\n1,2\n", "image/png", 415, "A image/png file cannot be read"),
        ("statement.csv", b"a,b\n1,2\n", "application/pdf", 415, "cannot be read"),
        ("statement.csv", b"PK\x03\x04\x00\x00binary", "text/csv", 415, "not a text file"),
        ("statement.xlsx", b"a,b\n1,2\n", "text/csv", 422, "only .csv and .jsonl"),
        ("statement.csv", b"a,b\n1,2\n", "text/csv", 422, "does not look like a bank file"),
    ],
)
def test_an_upload_that_is_not_a_text_export_is_refused(
    client: TestClient, filename: str, body: bytes, content_type: str, status: int, words: str
) -> None:
    response = client.post(
        f"{API}/connectors/bank/uploads",
        params={"filename": filename},
        content=body,
        headers={"Content-Type": content_type},
    )

    assert response.status_code == status, response.text
    assert words in response.json()["error"]["message"]
