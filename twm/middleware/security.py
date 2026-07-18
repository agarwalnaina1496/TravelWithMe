"""Small dependency-free HTTP abuse and response-hardening boundary."""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SecurityBoundaryMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        requests_per_minute: int = 60,
        max_body_bytes: int = 131_072,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.requests_per_minute = requests_per_minute
        self.max_body_bytes = max_body_bytes
        self.clock = clock
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length:
            try:
                body_bytes = int(content_length)
            except ValueError:
                await self._error(400, "Invalid Content-Length header.", scope, receive, send)
                return
            if body_bytes < 0:
                await self._error(400, "Invalid Content-Length header.", scope, receive, send)
                return
            if body_bytes > self.max_body_bytes:
                await self._error(413, "Request body is too large.", scope, receive, send)
                return

        if scope["path"] in {"/scout", "/meridian"} and scope["method"] == "POST":
            client = scope.get("client")
            key = client[0] if client else "unknown"
            if not self._allow(key):
                await self._error(429, "Too many requests.", scope, receive, send)
                return

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                        (b"cache-control", b"no-store"),
                    ]
                )
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, receive, secure_send)

    def _allow(self, key: str) -> bool:
        now = self.clock()
        cutoff = now - 60
        with self._lock:
            requests = self._requests[key]
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= self.requests_per_minute:
                return False
            requests.append(now)
            return True

    async def _error(
        self,
        status: int,
        detail: str,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        response = JSONResponse(
            {"detail": detail},
            status_code=status,
            headers={
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                "Cache-Control": "no-store",
            },
        )
        await response(scope, receive, send)
