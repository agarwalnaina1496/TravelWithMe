"""Application lifecycle ownership tests."""

import asyncio
from unittest.mock import Mock

from fastapi import FastAPI

from twm import main
from twm.services import AgentEngineSettings


def test_application_lifespan_owns_and_closes_shared_http_client(monkeypatch) -> None:
    class FakeAsyncClient:
        closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            self.closed = True

    client = FakeAsyncClient()
    engine = Mock()
    settings = AgentEngineSettings(engine="n8n", environment="test")
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda timeout: client)
    monkeypatch.setattr(main.AgentEngineSettings, "load", lambda: settings)
    monkeypatch.setattr(main, "get_agent_engine", lambda loaded, transport: engine)
    app = FastAPI()

    async def exercise_lifespan() -> None:
        async with main.application_lifespan(app):
            assert app.state.agent_engine is engine
            assert client.closed is False

    asyncio.run(exercise_lifespan())

    assert client.closed is True


def test_langgraph_lifespan_does_not_construct_n8n_transport(monkeypatch) -> None:
    engine = Mock()
    settings = AgentEngineSettings(
        engine="langgraph",
        environment="test",
        langgraph_model_provider="groq",
        langgraph_api_key="test",
    )
    monkeypatch.setattr(main.AgentEngineSettings, "load", lambda: settings)
    monkeypatch.setattr(
        main.httpx,
        "AsyncClient",
        lambda timeout: (_ for _ in ()).throw(
            AssertionError("LangGraph must not construct the n8n transport")
        ),
    )
    monkeypatch.setattr(
        main,
        "get_agent_engine",
        lambda loaded, transport: engine if transport is None else None,
    )
    app = FastAPI()

    async def exercise_lifespan() -> None:
        async with main.application_lifespan(app):
            assert app.state.agent_engine is engine

    asyncio.run(exercise_lifespan())
