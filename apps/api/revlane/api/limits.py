"""Rate limits and request size limits, applied before any route runs.

The counters live in this process. That is right for one API instance; with
more than one, the same limits need a shared store.
"""

import time
from collections.abc import Awaitable, Callable
from threading import Lock

from fastapi import FastAPI, Request
from starlette.responses import Response

from revlane.api.errors import envelope
from revlane.config import Settings

WINDOW_SECONDS = 60
UPLOAD_BYTES = 25 * 1024 * 1024
EXEMPT = ("/api/v1/health", "/api/v1/metrics")


def group_for(path: str) -> str:
    if "/uploads" in path:
        return "upload"
    if path.endswith("/chat"):
        return "chat"
    if path.endswith("/auth/login"):
        return "sign_in"
    return "default"


class Limiter:
    """A fixed window per client and group of routes."""

    def __init__(self, limits: dict[str, int], clock: Callable[[], float] = time.monotonic) -> None:
        self.limits = limits
        self.clock = clock
        self._lock = Lock()
        self._windows: dict[tuple[str, str], tuple[float, int]] = {}

    def check(self, client: str, group: str) -> int:
        """0 if the request may go ahead; otherwise the seconds to wait."""
        now = self.clock()
        with self._lock:
            started, count = self._windows.get((client, group), (now, 0))
            if now - started >= WINDOW_SECONDS:
                started, count = now, 0
            if count >= self.limits[group]:
                return max(1, int(WINDOW_SECONDS - (now - started)) + 1)
            self._windows[(client, group)] = (started, count + 1)
            if len(self._windows) > 10_000:  # forget clients whose window has passed
                self._windows = {
                    key: value
                    for key, value in self._windows.items()
                    if now - value[0] < WINDOW_SECONDS
                }
            return 0


def install(app: FastAPI, settings: Settings) -> None:
    limiter = Limiter(
        {
            "default": settings.rate_limit_per_minute,
            "chat": settings.rate_limit_chat_per_minute,
            "upload": settings.rate_limit_uploads_per_minute,
            "sign_in": settings.rate_limit_sign_in_per_minute,
        }
    )
    app.state.limiter = limiter

    @app.middleware("http")
    async def limit(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if request.method == "OPTIONS" or path in EXEMPT:
            return await call_next(request)
        group = group_for(path)
        declared = request.headers.get("content-length")
        largest = UPLOAD_BYTES if group == "upload" else settings.max_body_bytes
        if declared is not None and declared.isdigit() and int(declared) > largest:
            return envelope(413, "The request is larger than this endpoint accepts.")
        client = request.client.host if request.client else "unknown"
        wait = limiter.check(client, group)
        if wait:
            response = envelope(429, "Too many requests. Try again shortly.")
            response.headers["Retry-After"] = str(wait)
            return response
        return await call_next(request)
