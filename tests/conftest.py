"""Shared pytest fixtures for API tests."""

import os

import pytest
from fastapi.testclient import TestClient

# Application imports construct the selected engine. Keep the shared suite
# credential-free; focused tests exercise the LangGraph engine with fakes.
os.environ["AGENT_ENGINE"] = "n8n"
os.environ["ENVIRONMENT"] = "test"
os.environ["N8N_SCOUT_WEBHOOK_URL"] = "https://agents.test/webhook/scout"
os.environ["N8N_MERIDIAN_WEBHOOK_URL"] = "https://agents.test/webhook/meridian"
os.environ["N8N_WEBHOOK_TOKEN"] = "test-token"

from twm.main import app


@pytest.fixture
def api_client() -> TestClient:
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
