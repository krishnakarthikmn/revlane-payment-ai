"""Sandbox connectors for Razorpay and PayU.

No test here reaches a network: the transport is a fake. Rule 10 (sandbox
only), rule 5 (read calls only) and rule 8 (credentials stay in the vault)
are what these pin.
"""

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from revlane.bootstrap import ensure_tenant
from revlane.config import Settings
from revlane.connectors.live import (
    SANDBOX_HOSTS,
    LiveAccess,
    LiveCallsDisabled,
    SandboxTransport,
)
from revlane.connectors.payu import PayUConnector, response_hash
from revlane.connectors.razorpay import RazorpayConnector
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.ingest import live
from revlane.vault import CredentialRef, get_vault

RAZORPAY = {"key_id": "rzp_test_abc123", "key_secret": "s3cret", "webhook_secret": "whsec"}
PAYU = {"key": "gtKFFx", "salt": "eCwWELxi"}
ON = Settings(provider_live_calls=True)
OFF = Settings(provider_live_calls=False)


class FakeTransport:
    """Answers from a script and remembers what it was asked."""

    def __init__(self, replies: dict[str, list[Any]] | None = None) -> None:
        self.replies = replies or {}
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "params": params, "form": form}
        )
        queue = self.replies.get(url, [])
        return queue.pop(0) if queue else {}


class MemoryVault:
    def __init__(self) -> None:
        self.secrets: dict[CredentialRef, str] = {}

    def put(self, tenant_id: UUID, name: str, secret: str) -> CredentialRef:
        ref = CredentialRef(tenant_id, name)
        self.secrets[ref] = secret
        return ref

    def get(self, ref: CredentialRef) -> str:
        return self.secrets[ref]

    def delete(self, ref: CredentialRef) -> None:
        self.secrets.pop(ref, None)

    def delete_all(self, tenant_id: UUID) -> int:
        return 0


def access(
    credentials: dict[str, str], transport: FakeTransport, enabled: bool = True
) -> LiveAccess:
    return LiveAccess(
        enabled=enabled, transport=transport, vault=MemoryVault(), entered=credentials
    )


def payment(n: int) -> dict[str, Any]:
    return {
        "id": f"pay_live{n}",
        "entity": "payment",
        "amount": 10_000 + n,
        "currency": "INR",
        "status": "captured",
        "method": "upi",
        "captured": True,
        "notes": {"booking_id": f"BK-L{n}"},
        "contact": "+919800000001",
        "created_at": 1_794_800_000 + n,
    }


# --- Off by default --------------------------------------------------------------


def test_live_calls_are_off_by_default() -> None:
    assert Settings().provider_live_calls is False
    assert Settings.model_fields["provider_live_calls"].default is False


@pytest.mark.parametrize(
    ("connector", "credentials"), [(RazorpayConnector, RAZORPAY), (PayUConnector, PAYU)]
)
def test_nothing_is_called_while_the_flag_is_off(
    connector: type[RazorpayConnector] | type[PayUConnector], credentials: dict[str, str]
) -> None:
    transport = FakeTransport()
    live_connector = connector(access(credentials, transport, enabled=False))

    with pytest.raises(LiveCallsDisabled):
        list(live_connector.backfill(date(2026, 11, 1)))
    with pytest.raises(LiveCallsDisabled):
        list(live_connector.poll(None))
    with pytest.raises(LiveCallsDisabled):
        live_connector.authorize(uuid4())
    assert transport.calls == []


def test_sync_and_connect_refuse_while_the_flag_is_off(database: None) -> None:
    transport = FakeTransport()

    with pytest.raises(LiveCallsDisabled):
        live.sync(uuid4(), "razorpay", date(2026, 11, 1), settings=OFF, transport=transport)
    with pytest.raises(LiveCallsDisabled):
        live.connect(uuid4(), "razorpay", RAZORPAY, settings=OFF, transport=transport)
    with pytest.raises(KeyError, match="read from files"):
        live.sync(uuid4(), "cashfree", date(2026, 11, 1), settings=ON, transport=transport)
    assert transport.calls == []


# --- Sandbox only ----------------------------------------------------------------


@pytest.mark.parametrize("key_id", ["rzp_live_abc123", "abc", ""])
def test_razorpay_refuses_anything_but_a_test_mode_key(key_id: str) -> None:
    transport = FakeTransport()
    vault_access = access(RAZORPAY | {"key_id": key_id}, transport)
    connector = RazorpayConnector(vault_access)

    with pytest.raises(PermissionError, match="test-mode"):
        connector.authorize(uuid4())
    with pytest.raises(PermissionError, match="test-mode"):
        list(connector.backfill(date(2026, 11, 1)))
    assert transport.calls == []
    assert vault_access.ref is None  # nothing was stored


@pytest.mark.parametrize(
    "url",
    [
        "https://info.payu.in/merchant/postservice?form=2",
        "https://secure.payu.in/_payment",
        "https://api.cashfree.com/pg/orders",
        "http://api.razorpay.com/v1/payments",
        "https://api.razorpay.com.evil.example/v1/payments",
    ],
)
def test_the_transport_refuses_every_host_that_is_not_a_sandbox(url: str) -> None:
    with pytest.raises(PermissionError, match="not a provider sandbox host"):
        SandboxTransport().request("GET", url, headers={})


def test_no_other_provider_host_is_written_anywhere_in_the_connectors() -> None:
    package = Path(__file__).resolve().parents[1] / "revlane" / "connectors"
    hosts = set()
    for path in package.rglob("*.py"):
        hosts |= set(re.findall(r"https?://([A-Za-z0-9.-]+)", path.read_text(encoding="utf-8")))
    documentation = {"docs.payu.in", "razorpay.com", "www.cashfree.com"}

    assert {"api.razorpay.com", "test.payu.in"} == SANDBOX_HOSTS
    assert hosts - documentation <= SANDBOX_HOSTS


# --- Razorpay --------------------------------------------------------------------


def test_razorpay_backfill_pages_through_payments_with_get_requests_only() -> None:
    url = "https://api.razorpay.com/v1/payments"
    transport = FakeTransport(
        {url: [{"items": [payment(n) for n in range(100)]}, {"items": [payment(100)]}]}
    )
    connector = RazorpayConnector(access(RAZORPAY, transport))

    records = list(connector.backfill(date(2026, 11, 1)))

    assert [record.payload["id"] for record in records] == [f"pay_live{n}" for n in range(101)]
    assert {(record.provider, record.kind, record.source) for record in records} == {
        ("razorpay", "payment", "api")
    }
    pages = [call for call in transport.calls if call["url"] == url]
    assert [(call["params"]["skip"], call["params"]["count"]) for call in pages] == [
        ("0", "100"),
        ("100", "100"),
    ]
    since = int(datetime(2026, 10, 31, 18, 30, tzinfo=UTC).timestamp())  # midnight in India
    assert pages[0]["params"]["from"] == str(since)
    # Rule 5: reads only.
    assert {call["method"] for call in transport.calls} == {"GET"}
    assert {
        call["url"].removeprefix("https://api.razorpay.com/v1/") for call in transport.calls
    } == {
        "payments",
        "refunds",
        "settlements",
        "settlements/recon/combined",
    }
    assert all(call["headers"]["Authorization"].startswith("Basic ") for call in transport.calls)


def test_razorpay_webhook_needs_a_valid_signature() -> None:
    connector = RazorpayConnector(access(RAZORPAY, FakeTransport(), enabled=False))
    body = json.dumps(
        {
            "entity": "event",
            "event": "payment.captured",
            "payload": {"payment": {"entity": payment(1)}},
            "created_at": 1_794_800_010,
        }
    ).encode()
    signature = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()

    (record,) = connector.handle_webhook(body, signature)

    assert (record.kind, record.source) == ("webhook", "webhook")
    assert [type(item).__name__ for item in connector.map(record)] == [
        "AttemptRecord",
        "EventRecord",
    ]
    with pytest.raises(PermissionError, match="signature"):
        connector.handle_webhook(body, "0" * 64)
    with pytest.raises(PermissionError, match="signature"):
        connector.handle_webhook(body + b" ", signature)


# --- PayU ------------------------------------------------------------------------


def test_payu_backfill_asks_the_test_host_one_day_at_a_time() -> None:
    url = "https://test.payu.in/merchant/postservice?form=2"
    row = {
        "id": "403993715500000009",
        "mode": "UPI",
        "status": "success",
        "unmappedstatus": "captured",
        "txnid": "BK-P1-1",
        "amount": "499.00",
        "addedon": "2026-11-15 10:00:00",
        "udf1": "BK-P1",
        "card_no": "",
        "error_code": "E000",
    }
    transport = FakeTransport({url: [{"status": 1, "Transaction_details": [row]}]})
    connector = PayUConnector(access(PAYU, transport))

    records = list(connector._transactions(date(2026, 11, 15), date(2026, 11, 16)))

    assert len(records) == 1
    assert (records[0].kind, records[0].source) == ("transaction", "api")
    assert records[0].payload["mihpayid"] == "403993715500000009"
    assert [call["form"]["var1"] for call in transport.calls] == ["2026-11-15", "2026-11-16"]
    assert {call["url"] for call in transport.calls} == {url}
    form = transport.calls[0]["form"]
    assert form["command"] == "get_Transaction_Details"
    assert (
        form["hash"]
        == hashlib.sha512(b"gtKFFx|get_Transaction_Details|2026-11-15|eCwWELxi").hexdigest()
    )
    assert "salt" not in form
    (attempt, event) = connector.map(records[0])
    assert (attempt.amount_minor, event.provider_status) == (49_900, "captured")  # type: ignore[union-attr]


def test_payu_webhook_needs_a_valid_hash() -> None:
    connector = PayUConnector(access(PAYU, FakeTransport(), enabled=False))
    report = {
        "mihpayid": "403993715500000010",
        "mode": "NB",
        "status": "failure",
        "unmappedstatus": "failed",
        "txnid": "BK-P2-1",
        "amount": "750.00",
        "addedon": "2026-11-15 11:00:00",
        "productinfo": "Acme Travel booking",
        "firstname": "Shopper1",
        "email": "shopper1@example.com",
        "udf1": "BK-P2",
        "error": "E303",
    }
    expected = hashlib.sha512(
        b"eCwWELxi|failure||||||||||BK-P2|shopper1@example.com|Shopper1|"
        b"Acme Travel booking|750.00|BK-P2-1|gtKFFx"
    ).hexdigest()
    assert response_hash(report, "gtKFFx", "eCwWELxi") == expected
    body = json.dumps(report | {"hash": expected}).encode()

    (record,) = connector.handle_webhook(body, "")

    assert (record.kind, record.source) == ("transaction", "webhook")
    tampered = json.dumps(report | {"amount": "7.50", "hash": expected}).encode()
    with pytest.raises(PermissionError, match="hash"):
        connector.handle_webhook(tampered, "")


# --- Credentials go to the vault, and a sync is ingested ---------------------------


def test_connect_then_sync_through_a_fake_sandbox(database: None) -> None:
    tenant_id = uuid4()
    ensure_tenant(tenant_id, "live-sync")
    url = "https://api.razorpay.com/v1/payments"
    transport = FakeTransport({url: [{"items": [payment(1), payment(2)]}]})

    ref = live.connect(tenant_id, "razorpay", RAZORPAY, settings=ON, transport=transport)

    assert str(ref) == f"vault://{tenant_id}/razorpay"
    assert json.loads(get_vault(ON).get(ref)) == RAZORPAY
    with tenant_session(tenant_id) as session:
        account = session.scalars(select(m.ProviderAccount)).one()
        stored = session.scalar(select(m.VaultSecret.ciphertext))
    # The account row holds a reference, and the vault row is not the secret in the clear.
    assert account.credential_ref == str(ref)
    assert stored is not None and b"s3cret" not in bytes(stored)

    report = live.sync(tenant_id, "razorpay", date(2026, 11, 1), settings=ON, transport=transport)

    assert (report.source, report.read, report.rejected) == ("api", 2, 0)
    assert report.added["payment_attempt"] == 2
    with tenant_session(tenant_id) as session:
        sources = set(session.scalars(select(m.AttemptEvent.source)))
        statuses = set(session.scalars(select(m.AttemptStatus.status)))
        audit = session.scalars(select(m.AuditLog.ref)).all()
    assert (sources, statuses) == ({"api"}, {"succeeded"})
    assert audit == ["razorpay:api"]
    assert {call["method"] for call in transport.calls} == {"GET"}


def test_sync_needs_stored_credentials(database: None) -> None:
    tenant_id = uuid4()
    ensure_tenant(tenant_id, "live-none")

    with pytest.raises(PermissionError, match="No sandbox credentials"):
        live.sync(tenant_id, "payu", date(2026, 11, 1), settings=ON, transport=FakeTransport())
