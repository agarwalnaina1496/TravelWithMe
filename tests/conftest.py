"""Shared pytest fixtures for API tests."""

import os

import pytest
from fastapi.testclient import TestClient

# Application imports construct the selected engine. Keep the shared suite
# credential-free; focused tests exercise the LangGraph engine with fakes.
os.environ["AGENT_ENGINE"] = "n8n"
os.environ["ENVIRONMENT"] = "dev"
os.environ["N8N_SCOUT_WEBHOOK_URL"] = "https://agents.test/webhook/scout"
os.environ["N8N_MERIDIAN_WEBHOOK_URL"] = "https://agents.test/webhook/meridian"
os.environ["N8N_GUIDE_WEBHOOK_URL"] = "https://agents.test/webhook/guide"
os.environ["N8N_ATLAS_WEBHOOK_URL"] = "https://agents.test/webhook/atlas"
os.environ["N8N_ROUTE_CLASSIFIER_WEBHOOK_URL"] = "https://agents.test/webhook/route-classifier"
os.environ["TRUSTED_HOSTS"] = '["testserver"]'
os.environ["CORS_ALLOWED_ORIGINS"] = '["https://ui.test"]'

from twm.dependencies import get_current_user
from twm.main import app


@pytest.fixture
def api_client() -> TestClient:
    # Default every API test to an unauthenticated (guest-only) request —
    # get_current_user otherwise depends on get_auth_service, which raises
    # 503 when app.state.auth_service is unset (no APP_DATABASE_URL in this
    # suite). Tests exercising authenticated behavior override this again.
    app.dependency_overrides[get_current_user] = lambda: None
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
