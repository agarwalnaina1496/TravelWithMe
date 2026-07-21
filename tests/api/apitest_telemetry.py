from uuid import UUID

from fastapi.testclient import TestClient


def test_agent_validation_error_returns_safe_correlation_headers(
    api_client: TestClient,
) -> None:
    response = api_client.post(
        "/scout",
        headers={
            "Origin": "https://ui.test",
            "X-TWM-Request-ID": "invalid request id",
            "X-TWM-Trip-ID": "invalid trip id",
            "X-TWM-Turn-ID": "turn-7",
        },
        content="{",
    )

    assert response.status_code == 422
    UUID(response.headers["X-TWM-Request-ID"])
    assert "X-TWM-Trip-ID" not in response.headers
    assert response.headers["X-TWM-Turn-ID"] == "turn-7"
    assert response.headers["access-control-allow-origin"] == "https://ui.test"
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "x-twm-request-id" in exposed
    assert "x-twm-trip-id" in exposed
    assert "x-twm-turn-id" in exposed


def test_cors_preflight_allows_future_correlation_headers(
    api_client: TestClient,
) -> None:
    response = api_client.options(
        "/meridian",
        headers={
            "Origin": "https://ui.test",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "Content-Type, X-TWM-Request-ID, X-TWM-Trip-ID, X-TWM-Turn-ID"
            ),
        },
    )

    assert response.status_code == 200
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "x-twm-request-id" in allowed
    assert "x-twm-trip-id" in allowed
    assert "x-twm-turn-id" in allowed
