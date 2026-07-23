"""ASGI integration for correlation context and minimal lifecycle events."""

from collections.abc import Callable
from time import monotonic
from typing import Any
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .context import (
    CorrelationContext,
    build_correlation_context,
    reset_correlation_context,
    set_correlation_context,
)
from .logger import TelemetryLogger
from .sanitization import redact_error_detail


REQUEST_ID_HEADER = "X-TWM-Request-ID"
TRIP_ID_HEADER = "X-TWM-Trip-ID"
TURN_ID_HEADER = "X-TWM-Turn-ID"
CORRELATION_HEADERS = (REQUEST_ID_HEADER, TRIP_ID_HEADER, TURN_ID_HEADER)
_AGENT_PATHS = {"/scout", "/meridian"}


class TelemetryContextMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        logger: TelemetryLogger,
        request_id_factory: Callable[[], Any] = uuid4,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.app = app
        self.logger = logger
        self.request_id_factory = request_id_factory
        self.clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["path"] not in _AGENT_PATHS
            or scope["method"] != "POST"
        ):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        supplied_request_id = headers.get(REQUEST_ID_HEADER)
        context = build_correlation_context(
            request_id=supplied_request_id,
            trip_id=headers.get(TRIP_ID_HEADER),
            turn_id=headers.get(TURN_ID_HEADER),
        )
        if supplied_request_id is None:
            context = CorrelationContext(
                request_id=str(self.request_id_factory()),
                trip_id=context.trip_id,
                turn_id=context.turn_id,
            )
        token = set_correlation_context(context)
        started_at = self.clock()

        async def correlated_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers[REQUEST_ID_HEADER] = context.request_id
                if context.trip_id:
                    response_headers[TRIP_ID_HEADER] = context.trip_id
                if context.turn_id:
                    response_headers[TURN_ID_HEADER] = context.turn_id
            await send(message)

        agent = scope["path"].removeprefix("/").capitalize()
        try:
            await self.app(scope, receive, correlated_send)
        except Exception as exc:
            detail = redact_error_detail(
                str(exc).strip() or "unexpected request failure"
            )
            self.logger.error(
                f"FastAPI failed while handling {agent} request. Detail - "
                f"{type(exc).__name__}: {detail}",
                event="be.http.request.failed",
                source="http",
                fields={
                    "method": scope["method"],
                    "path": scope["path"],
                    "component": "fastapi",
                    "operation": f"{agent.lower()}.http",
                    "failure_stage": "request_handling",
                    "error_type": type(exc).__name__,
                    "error_detail": detail,
                    "duration_ms": round((self.clock() - started_at) * 1000, 3),
                },
            )
            raise
        finally:
            reset_correlation_context(token)
