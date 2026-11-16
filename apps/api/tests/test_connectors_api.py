"""Upload endpoints and the connector health summary."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from revlane.api import connectors
from revlane.bootstrap import ensure_tenant
from revlane.config import get_settings
from revlane.main import create_app
from tests.signin import sign_in

PAYMENTS = (
    '{"id":"pay_up1","entity":"payment","amount":250000,"currency":"INR","status":"captured",'
    '"method":"upi","captured":true,"vpa":"shopper1@ybl","contact":"+919800000001",'
    '"notes":{"booking_id":"BK-U1"},"created_at":1794800000}\n'
    '{"id":"pay_up2","entity":"payment","amount":90000,"currency":"INR","status":"on_hold_new",'
    '"method":"upi","captured":false,"notes":{"booking_id":"BK-U2"},"created_at":1794800100}\n'
    "this line is not json\n"
)
STATEMENT = (
    "Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance\n"
    "09/11/26,NEFT CR-RAZORPAY SOFTWARE PVT LTD-UTR1,UTR1,09/11/26,,2441.00,102441.00\n"
    "09/11/26,ATM WDL,,09/11/26,500.00,,101941.00\n"
)


@pytest.fixture(scope="module")
def client(database: None) -> Iterator[TestClient]:
    settings = get_settings()
    ensure_tenant(settings.demo_tenant_id, settings.demo_tenant_name)
    app = create_app()
    sign_in(app, settings.demo_tenant_id)
    with TestClient(app) as test_client:
        yield test_client


def upload(client: TestClient, provider: str, filename: str, body: str | bytes) -> object:
    return client.post(
        f"/api/v1/connectors/{provider}/uploads", params={"filename": filename}, content=body
    )


def test_uploading_a_file_ingests_it_and_reports_what_happened(client: TestClient) -> None:
    response = upload(client, "razorpay", "payments.jsonl", PAYMENTS)

    assert response.status_code == 200  # type: ignore[attr-defined]
    report = response.json()  # type: ignore[attr-defined]
    assert (report["provider"], report["source"]) == ("razorpay", "payments.jsonl")
    assert (report["read"], report["rejected"]) == (3, 1)
    assert report["rejections"] == ["Not valid JSON"]
    assert report["added"]["payment_attempt"] == 2
    assert report["added"]["attempt_event"] == 2
    assert report["statuses_derived"] == 2
    assert report["unmapped_statuses"] == {"on_hold_new": 1}


def test_uploading_the_same_file_again_adds_nothing(client: TestClient) -> None:
    report = upload(client, "razorpay", "payments.jsonl", PAYMENTS).json()  # type: ignore[attr-defined]

    assert set(report["added"].values()) == {0}


def test_a_bank_statement_upload_keeps_only_credits(client: TestClient) -> None:
    report = upload(client, "bank", "statement.csv", STATEMENT.encode("utf-8-sig")).json()  # type: ignore[attr-defined]

    assert (report["read"], report["rejected"]) == (2, 0)
    assert report["added"]["bank_credit"] == 1


def test_health_summarises_each_connector(client: TestClient) -> None:
    response = client.get("/api/v1/connectors/health")

    assert response.status_code == 200
    body = response.json()
    by_provider = {connector["provider"]: connector for connector in body["connectors"]}
    razorpay = by_provider["razorpay"]
    assert razorpay["records"]["attempts"] == 2
    assert razorpay["records"]["events"] == 2
    assert razorpay["last_sync_at"] is not None
    assert razorpay["has_credentials"] is False
    assert razorpay["unmapped_statuses"] == [{"provider_status": "on_hold_new", "attempts": 1}]
    assert by_provider["bank"]["records"] == {"bank_credits": 1}
    assert [
        (alert["provider"], alert["subject"], alert["record_count"]) for alert in body["alerts"]
    ] == [("razorpay", "on_hold_new", 1)]
    # Nothing the API returns carries a contact detail.
    assert "9800000001" not in response.text and "shopper1" not in response.text


@pytest.mark.parametrize(
    ("provider", "filename", "body", "status", "code", "message"),
    [
        ("stripe", "payments.jsonl", PAYMENTS, 404, "not_found", "No connector for stripe."),
        ("payu", "payments.jsonl", PAYMENTS, 422, "invalid_request", "does not look like a payu"),
        ("bank", "statement.pdf", "%PDF", 422, "invalid_request", "only .csv and .jsonl"),
        ("bank", "statement.csv", b"\xff\xfe\x00", 422, "invalid_request", "not UTF-8"),
    ],
)
def test_upload_errors_use_the_error_envelope(
    client: TestClient,
    provider: str,
    filename: str,
    body: str | bytes,
    status: int,
    code: str,
    message: str,
) -> None:
    response = upload(client, provider, filename, body)

    assert response.status_code == status  # type: ignore[attr-defined]
    error = response.json()["error"]  # type: ignore[attr-defined]
    assert error["code"] == code
    assert message in error["message"]


def test_a_filename_is_required(client: TestClient) -> None:
    response = client.post("/api/v1/connectors/bank/uploads", content=STATEMENT)

    assert response.status_code == 422
    assert "filename" in response.json()["error"]["message"]


def test_an_upload_over_the_size_limit_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(connectors, "MAX_UPLOAD_BYTES", 64)

    response = upload(client, "bank", "statement.csv", STATEMENT)

    assert response.status_code == 413  # type: ignore[attr-defined]
    assert response.json()["error"]["code"] == "payload_too_large"  # type: ignore[attr-defined]


def test_sandbox_endpoints_answer_409_while_live_calls_are_off(client: TestClient) -> None:
    stored = client.put(
        "/api/v1/connectors/razorpay/credentials",
        json={"values": {"key_id": "rzp_test_x", "key_secret": "y"}},
    )
    synced = client.post("/api/v1/connectors/payu/sync", params={"since": "2026-11-01"})

    assert (stored.status_code, synced.status_code) == (409, 409)
    assert "PROVIDER_LIVE_CALLS" in stored.json()["error"]["message"]
    assert stored.json()["error"]["code"] == "conflict"


def test_file_only_providers_have_no_sandbox_endpoints(client: TestClient) -> None:
    response = client.put("/api/v1/connectors/cashfree/credentials", json={"values": {}})

    assert response.status_code == 404
