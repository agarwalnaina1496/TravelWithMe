from fastapi import FastAPI
from fastapi.testclient import TestClient

from twm.middleware import SecurityBoundaryMiddleware


def secured_app(limit: int = 2, body_limit: int = 100) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        SecurityBoundaryMiddleware,
        requests_per_minute=limit,
        max_body_bytes=body_limit,
    )

    @app.post("/scout")
    def scout() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_security_boundary_throttles_agent_requests() -> None:
    with TestClient(secured_app()) as client:
        assert client.post("/scout").status_code == 200
        assert client.post("/scout").status_code == 200
        response = client.post("/scout")

    assert response.status_code == 429
    assert response.json() == {"detail": "Too many requests."}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_security_boundary_rejects_oversized_body_and_sets_headers() -> None:
    with TestClient(secured_app(body_limit=10)) as client:
        rejected = client.post("/scout", content="x" * 11)
    with TestClient(secured_app()) as client:
        accepted = client.post("/scout")

    assert rejected.status_code == 413
    assert rejected.headers["x-content-type-options"] == "nosniff"
    assert rejected.headers["cache-control"] == "no-store"
    assert accepted.headers["x-content-type-options"] == "nosniff"
    assert accepted.headers["x-frame-options"] == "DENY"
    assert accepted.headers["cache-control"] == "no-store"
