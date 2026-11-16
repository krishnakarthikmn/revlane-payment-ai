"""Milestone 0 acceptance: the API has a health route the web page can call."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from revlane.api.health import database_status
from revlane.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[database_status] = lambda: "ok"
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "revlane-api"
    assert body["database"] == "ok"
    assert body["llm_mode"] == "stub"


def test_health_is_degraded_when_database_is_down() -> None:
    app = create_app()
    app.dependency_overrides[database_status] = lambda: "unavailable"

    with TestClient(app) as test_client:
        body = test_client.get("/api/v1/health").json()

    assert body["status"] == "degraded"
    assert body["database"] == "unavailable"


def test_unknown_route_uses_the_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


def test_web_origin_is_allowed_by_cors(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
