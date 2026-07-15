"""Shared pytest fixtures for API tests."""

import pytest
from fastapi.testclient import TestClient

from twm.main import app


@pytest.fixture
def api_client() -> TestClient:
    with TestClient(app) as client:
        yield client
