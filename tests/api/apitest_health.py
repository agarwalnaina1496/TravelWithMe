"""API tests for the health router."""

from fastapi.testclient import TestClient


def test_root(api_client: TestClient) -> None:
    response = api_client.get("/")

    assert response.status_code == 200
    assert response.json() == {"service": "travelwithme", "status": "ok"}


def test_health(api_client: TestClient) -> None:
    response = api_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
