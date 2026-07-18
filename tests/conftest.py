"""Shared pytest fixtures for API tests."""

import os

import pytest
from fastapi.testclient import TestClient

# Application imports construct the selected engine. Keep the shared suite
# credential-free; focused tests exercise the LangGraph engine with fakes.
os.environ["AGENT_ENGINE"] = "n8n"
os.environ["ENVIRONMENT"] = "test"

from twm.main import app


@pytest.fixture
def api_client() -> TestClient:
    with TestClient(app) as client:
        yield client
