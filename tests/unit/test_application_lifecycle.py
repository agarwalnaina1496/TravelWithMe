"""Application lifecycle ownership tests."""

import asyncio
from unittest.mock import Mock

from fastapi import FastAPI

from twm import main
from twm.services import AgentEngineSettings
from twm.services.agent_engine.langgraph import LangGraphAgentAdapter


class _FakeLangGraphAgentAdapter(LangGraphAgentAdapter):
    """A real subclass (so `isinstance` checks in application_lifespan
    behave correctly) that skips the actual LangGraph runtime construction.
    """

    def __init__(self, settings=None) -> None:  # noqa: super-init-not-called
        self.settings = settings


def test_application_lifespan_owns_and_closes_shared_http_client(monkeypatch) -> None:
    class FakeAsyncClient:
        closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            self.closed = True

    client = FakeAsyncClient()
    engine = Mock()
    settings = AgentEngineSettings(
        engine="n8n",
        environment="test",
        n8n_timeout_seconds=185,
        langgraph_model_provider="groq",
        langgraph_api_key="test",
    )
    captured_timeout = None

    def build_client(timeout):
        nonlocal captured_timeout
        captured_timeout = timeout
        return client

    monkeypatch.setattr(main.httpx, "AsyncClient", build_client)
    monkeypatch.setattr(main.AgentEngineSettings, "load", lambda: settings)
    monkeypatch.setattr(main, "build_agent_adapter", lambda loaded, transport: Mock())
    monkeypatch.setattr(
        main,
        "get_agent_engine",
        lambda loaded, logger, transport, adapter=None: engine,
    )
    monkeypatch.setattr(main, "LangGraphAgentAdapter", _FakeLangGraphAgentAdapter)
    app = FastAPI()
    app.state.telemetry = Mock()

    async def exercise_lifespan() -> None:
        async with main.application_lifespan(app):
            assert app.state.agent_engine is engine
            assert client.closed is False

    asyncio.run(exercise_lifespan())

    assert client.closed is True
    assert captured_timeout == 185.0
    app.state.telemetry.shutdown.assert_called_once_with()


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
    monkeypatch.setattr(main, "build_agent_adapter", lambda loaded, transport: Mock())
    monkeypatch.setattr(
        main,
        "get_agent_engine",
        lambda loaded, logger, transport, adapter=None: engine if transport is None else None,
    )
    app = FastAPI()
    app.state.telemetry = Mock()

    async def exercise_lifespan() -> None:
        async with main.application_lifespan(app):
            assert app.state.agent_engine is engine

    asyncio.run(exercise_lifespan())

    app.state.telemetry.shutdown.assert_called_once_with()


def test_n8n_lifespan_gives_route_classifier_a_dedicated_langgraph_adapter(
    monkeypatch,
) -> None:
    # PR-review fix (TWM-195): even when engine="n8n" (no deployed n8n
    # classifier workflow), the route classifier must get a
    # LangGraphAgentAdapter, not the primary n8n adapter.
    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    settings = AgentEngineSettings(
        engine="n8n",
        environment="test",
        n8n_timeout_seconds=185,
        langgraph_model_provider="groq",
        langgraph_api_key="test",
    )
    n8n_adapter = Mock()

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda timeout: FakeAsyncClient())
    monkeypatch.setattr(main.AgentEngineSettings, "load", lambda: settings)
    monkeypatch.setattr(main, "build_agent_adapter", lambda loaded, transport: n8n_adapter)
    monkeypatch.setattr(
        main,
        "get_agent_engine",
        lambda loaded, logger, transport, adapter=None: Mock(),
    )
    monkeypatch.setattr(main, "LangGraphAgentAdapter", _FakeLangGraphAgentAdapter)
    app = FastAPI()
    app.state.telemetry = Mock()

    async def exercise_lifespan() -> None:
        async with main.application_lifespan(app):
            assert isinstance(app.state.route_classifier.adapter, _FakeLangGraphAgentAdapter)
            assert app.state.route_classifier.adapter is not n8n_adapter

    asyncio.run(exercise_lifespan())


def test_langgraph_lifespan_reuses_primary_adapter_for_route_classifier(
    monkeypatch,
) -> None:
    settings = AgentEngineSettings(
        engine="langgraph",
        environment="test",
        langgraph_model_provider="groq",
        langgraph_api_key="test",
    )
    shared_adapter = Mock(spec=LangGraphAgentAdapter)

    monkeypatch.setattr(main.AgentEngineSettings, "load", lambda: settings)
    monkeypatch.setattr(main, "build_agent_adapter", lambda loaded, transport: shared_adapter)
    monkeypatch.setattr(
        main,
        "get_agent_engine",
        lambda loaded, logger, transport, adapter=None: Mock(),
    )
    app = FastAPI()
    app.state.telemetry = Mock()

    async def exercise_lifespan() -> None:
        async with main.application_lifespan(app):
            assert app.state.route_classifier.adapter is shared_adapter

    asyncio.run(exercise_lifespan())
