"""Sign-in: passwords, session cookies, who may do what, and that nothing
is reachable without signing in."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from revlane.api.auth import REFUSED
from revlane.auth import SessionError, hash_password, issue, read, verify_password
from revlane.auth.accounts import authenticate
from revlane.auth.passwords import WeakPassword
from revlane.auth.providers import NotConfigured, second_factor, single_sign_on
from revlane.auth.sessions import COOKIE
from revlane.bootstrap import demo_user_id, ensure_demo_users, ensure_tenant
from revlane.config import get_settings
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.main import create_app
from tests.signin import sign_in

pytestmark = pytest.mark.usefixtures("database")

API = "/api/v1"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
SECRET = "a-secret-for-these-tests"
PASSWORD = "test-only-demo-password"


@pytest.fixture(scope="module")
def tenant(database: None) -> UUID:
    """A tenant of its own with the two demo people and a viewer."""
    tenant_id = uuid4()
    ensure_tenant(tenant_id, "Auth Test")
    settings = get_settings().model_copy(
        update={
            "demo_user_email": f"priya-{tenant_id.hex[:6]}@example.com",
            "demo_second_approver_email": f"arjun-{tenant_id.hex[:6]}@example.com",
        }
    )
    ensure_demo_users(tenant_id, settings)
    with tenant_session(tenant_id) as session:
        session.add(
            m.AppUser(
                tenant_id=tenant_id,
                email=f"viewer-{tenant_id.hex[:6]}@example.com",
                name="Viewer",
                role="viewer",
                password_hash=hash_password(PASSWORD),
            )
        )
    return tenant_id


def email(tenant: UUID, who: str) -> str:
    return f"{who}-{tenant.hex[:6]}@example.com"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def login(client: TestClient, address: str, password: str = PASSWORD) -> int:
    return client.post(
        f"{API}/auth/login", json={"email": address, "password": password}
    ).status_code


# --- Passwords and tokens -----------------------------------------------------------------


def test_a_password_is_stored_as_a_salted_hash_and_verified_against_it() -> None:
    stored = hash_password("correct horse battery")

    assert stored.startswith("scrypt$16384$8$1$")
    assert "correct horse" not in stored
    assert hash_password("correct horse battery") != stored  # a new salt each time
    assert verify_password("correct horse battery", stored)
    assert not verify_password("correct horse batterx", stored)
    assert not verify_password("anything", None)
    assert not verify_password("anything", "not-a-hash")
    with pytest.raises(WeakPassword):
        hash_password("short")


def test_a_session_token_names_its_tenant_and_user_until_it_expires() -> None:
    tenant_id, user_id = uuid4(), uuid4()
    token = issue(tenant_id, user_id, SECRET, NOW, timedelta(hours=12))

    assert read(token, SECRET, NOW + timedelta(hours=11)) == (tenant_id, user_id)
    for bad, when in (
        (token, NOW + timedelta(hours=12)),  # expired
        (token[:-2] + "xx", NOW),  # signature changed
        (
            issue(uuid4(), user_id, SECRET, NOW, timedelta(hours=1)).split(".")[0]
            + "."
            + token.split(".")[1],
            NOW,
        ),
        ("", NOW),
        (None, NOW),
        ("not.a.token", NOW),
    ):
        with pytest.raises(SessionError):
            read(bad, SECRET, when)
    # A token signed with another secret is worth nothing.
    with pytest.raises(SessionError):
        read(token, "another-secret", NOW)
    with pytest.raises(SessionError):
        issue(tenant_id, user_id, "", NOW, timedelta(hours=1))


def test_single_sign_on_and_a_second_factor_are_stubs_behind_their_interfaces() -> None:
    with pytest.raises(NotConfigured):
        single_sign_on().start("someone@example.com")
    assert second_factor().required(uuid4(), uuid4()) is False


# --- Signing in ------------------------------------------------------------------------------


def test_signing_in_sets_a_cookie_and_says_who_you_are(client: TestClient, tenant: UUID) -> None:
    response = client.post(
        f"{API}/auth/login", json={"email": email(tenant, "PRIYA").upper(), "password": PASSWORD}
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(demo_user_id(tenant, email(tenant, "priya"))),
        "tenant_id": str(tenant),
        "email": email(tenant, "priya"),
        "name": "Priya Menon",
        "role": "admin",
    }
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert PASSWORD not in response.text
    assert client.get(f"{API}/auth/me").json()["role"] == "admin"
    assert client.get(f"{API}/console").json()["merchant"] == {"name": "Auth Test"}


@pytest.mark.parametrize(
    ("who", "password"),
    [("priya", "the-wrong-password"), ("nobody", PASSWORD), ("priya", " ")],
)
def test_a_wrong_email_and_a_wrong_password_get_the_same_answer(
    client: TestClient, tenant: UUID, who: str, password: str
) -> None:
    response = client.post(
        f"{API}/auth/login", json={"email": email(tenant, who), "password": password}
    )

    assert response.status_code == 401
    assert response.json()["error"] == {"code": "unauthenticated", "message": REFUSED}
    assert "set-cookie" not in response.headers


def test_someone_with_no_password_set_cannot_sign_in(tenant: UUID) -> None:
    with tenant_session(tenant) as session:
        session.add(
            m.AppUser(tenant_id=tenant, email=email(tenant, "new"), name="New", role="viewer")
        )

    assert authenticate(email(tenant, "new"), "") is None
    assert authenticate(email(tenant, "new"), PASSWORD) is None


def test_signing_out_ends_the_session(client: TestClient, tenant: UUID) -> None:
    assert login(client, email(tenant, "priya")) == 200

    assert client.post(f"{API}/auth/logout").status_code == 204
    assert client.get(f"{API}/auth/me").status_code == 401


def test_a_removed_user_is_signed_out_and_a_changed_role_applies_at_once(
    client: TestClient, tenant: UUID
) -> None:
    address = email(tenant, "leaver")
    with tenant_session(tenant) as session:
        session.add(
            m.AppUser(
                tenant_id=tenant,
                email=address,
                name="Leaver",
                role="admin",
                password_hash=hash_password(PASSWORD),
            )
        )
    assert login(client, address) == 200
    assert client.get(f"{API}/audit-log/export").status_code == 200

    with tenant_session(tenant) as session:
        session.execute(update(m.AppUser).where(m.AppUser.email == address).values(role="viewer"))
    assert client.get(f"{API}/audit-log/export").status_code == 403
    with tenant_session(tenant) as session:
        session.execute(delete(m.AppUser).where(m.AppUser.email == address))
    assert client.get(f"{API}/auth/me").status_code == 401


def test_sign_in_attempts_are_limited(tenant: UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_sign_in_per_minute", 3)
    with TestClient(create_app()) as limited:
        tries = [login(limited, email(tenant, "priya"), "guess-number-one") for _ in range(4)]

    assert tries == [401, 401, 401, 429]


def test_without_a_session_secret_nobody_can_sign_in(
    tenant: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "session_secret", "")
    with TestClient(create_app()) as unset:
        assert login(unset, email(tenant, "priya")) == 503
        assert unset.get(f"{API}/console").status_code == 401


# --- Nothing without signing in -----------------------------------------------------------------

PUBLIC = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/fixtures"),  # the prototype's sample data: no tenant's records
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("GET", "/api/v1/auth/sso"),
    ("GET", "/api/v1/metrics"),  # its own bearer token; checked below
}


def routes() -> list[tuple[str, str]]:
    """Every method and path the API serves, from its own description of itself."""
    paths = create_app().openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, methods in paths.items()
        for method in methods
        if path.startswith("/api/v1")
    )


def filled(path: str) -> str:
    """The route with something in each placeholder."""
    for name, value in (
        ("{provider}", "razorpay"),
        ("{name}", "fee_audit"),
        ("{extension}", "pdf"),
    ):
        path = path.replace(name, value)
    return "/".join(str(uuid4()) if part.startswith("{") else part for part in path.split("/"))


@pytest.mark.parametrize(("method", "path"), [r for r in routes() if r not in PUBLIC])
def test_every_route_but_the_public_few_refuses_a_request_with_no_session(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, filled(path), json={})

    assert response.status_code == 401, f"{method} {path} answered {response.status_code}"
    assert response.json()["error"] == {
        "code": "unauthenticated",
        "message": "Sign in to continue.",
    }


def test_the_list_of_routes_is_the_one_this_file_expects() -> None:
    assert len(routes()) > 35 and len([r for r in routes() if r not in PUBLIC]) > 30
    assert set(routes()) >= PUBLIC


def test_a_forged_or_expired_cookie_is_no_session(client: TestClient, tenant: UUID) -> None:
    user_id = demo_user_id(tenant, email(tenant, "priya"))
    secret = get_settings().session_secret
    forged = issue(tenant, user_id, "not-the-real-secret", datetime.now(tz=UTC), timedelta(hours=1))
    expired = issue(
        tenant, user_id, secret, datetime.now(tz=UTC) - timedelta(days=2), timedelta(hours=1)
    )
    elsewhere = issue(uuid4(), user_id, secret, datetime.now(tz=UTC), timedelta(hours=1))
    good = issue(tenant, user_id, secret, datetime.now(tz=UTC), timedelta(hours=1))

    for token in (forged, expired, elsewhere, "garbage"):
        client.cookies.set(COOKIE, token)
        assert client.get(f"{API}/console").status_code == 401, token
    client.cookies.set(COOKIE, good)
    assert client.get(f"{API}/console").status_code == 200


def test_metrics_need_their_own_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    token = get_settings().metrics_token

    assert client.get(f"{API}/metrics").status_code == 401
    assert (
        client.get(f"{API}/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    )
    assert (
        client.get(f"{API}/metrics", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )
    monkeypatch.setattr(get_settings(), "metrics_token", "")
    assert client.get(f"{API}/metrics", headers={"Authorization": "Bearer "}).status_code == 404


# --- Who may do what ------------------------------------------------------------------------------

CHANGES = [
    # (method, path, body, the least role that may)
    ("POST", "/audit", {}, "approver"),
    ("POST", "/agents/fee_audit/runs", {}, "approver"),
    ("POST", "/agents/fee_audit/simulate", {"rules": {}}, "approver"),
    ("POST", f"/proposals/{uuid4()}/decisions", {"decision": "approved"}, "approver"),
    ("POST", f"/findings/{uuid4()}/transitions", {"to_state": "lost"}, "approver"),
    ("PUT", "/agents/fee_audit/settings", {"autonomy_level": 1, "enabled": True}, "admin"),
    ("PUT", "/agents/fee_audit/rules", {"rules": {}}, "admin"),
    ("DELETE", f"/fee-contracts/{uuid4()}", None, "admin"),
    ("PUT", "/connectors/razorpay/credentials", {"values": {}}, "admin"),
    ("POST", "/privacy/erasures", {"customer_token": "x" * 32}, "admin"),
    ("GET", "/audit-log/export", None, "admin"),
]
RANK = {"viewer": 0, "approver": 1, "admin": 2}


@pytest.mark.parametrize("role", ["viewer", "approver", "admin"])
def test_each_role_can_do_what_it_should_and_no_more(tenant: UUID, role: str) -> None:
    app = create_app()
    sign_in(app, tenant, role)
    with TestClient(app) as signed_in:
        for method, path, body, least in CHANGES:
            status = signed_in.request(method, f"{API}{path}", json=body).status_code
            allowed = RANK[role] >= RANK[least]
            assert (status != 403) == allowed, f"{role} {method} {path} answered {status}"
        # Everyone signed in can read, and ask.
        assert signed_in.get(f"{API}/console").status_code == 200
        assert signed_in.get(f"{API}/findings").status_code == 200
        asked = signed_in.post(f"{API}/chat", json={"question": "What is our churn?"})
        assert asked.status_code == 200


def test_a_decision_is_recorded_against_whoever_is_signed_in(tenant: UUID) -> None:
    app = create_app()
    who = sign_in(app, tenant, "approver")
    with tenant_session(tenant) as session:
        proposal = m.Proposal(
            tenant_id=tenant,
            finding_ids=[],
            playbook="fee_audit",
            playbook_version=1,
            action_type="draft_leakage_report",
            idempotency_key=uuid4().hex,
        )
        session.add(proposal)
        session.flush()
        proposal_id = proposal.id
    with TestClient(app) as signed_in:
        url = f"{API}/proposals/{proposal_id}/decisions"
        named = signed_in.post(url, json={"decision": "approved", "approver_id": str(uuid4())})
        decided = signed_in.post(url, json={"decision": "rejected", "reason": "Not now"})

    # Nobody can decide in someone else's name: the field is not accepted at all.
    assert named.status_code == 422
    assert decided.status_code == 200
    assert decided.json()["decisions"][0]["approver_id"] == str(who.user_id)
    with tenant_session(tenant) as session:
        actor = session.scalar(
            select(m.AuditLog.actor).where(m.AuditLog.ref == f"proposal:{proposal_id}")
        )
    assert actor == f"user:{who.user_id}"


def test_a_signed_in_person_sees_only_their_own_tenant(tenant: UUID) -> None:
    other = uuid4()
    ensure_tenant(other, "Someone Else")
    app = create_app()
    sign_in(app, other, "admin")
    with TestClient(app) as theirs:
        assert theirs.get(f"{API}/console").json()["merchant"] == {"name": "Someone Else"}
        assert theirs.get(f"{API}/findings").json()["items"] == []


def test_a_person_is_added_from_the_command_line_and_can_then_sign_in(
    client: TestClient, tenant: UUID
) -> None:
    from revlane.auth.__main__ import NoSuchUser, add_user, set_password

    address = email(tenant, "newcomer")
    user_id = add_user(tenant, address.upper(), "New Comer", "approver", "a-first-password")

    assert login(client, address, "a-first-password") == 200
    assert client.get(f"{API}/auth/me").json() == {
        "id": str(user_id),
        "tenant_id": str(tenant),
        "email": address,
        "name": "New Comer",
        "role": "approver",
    }
    set_password(tenant, address, "a-second-password")
    assert login(client, address, "a-first-password") == 401
    assert login(client, address, "a-second-password") == 200
    # Adding the same person again changes them; it does not make a second one.
    assert add_user(tenant, address, "New Comer", "viewer", "a-third-password") == user_id
    with pytest.raises(NoSuchUser):
        set_password(tenant, "nobody@example.com", "a-long-password")
    with pytest.raises(WeakPassword):
        add_user(tenant, address, "New Comer", "viewer", "short")
    with pytest.raises(ValueError, match="A role is one of"):
        add_user(tenant, address, "New Comer", "owner", "a-long-password")
