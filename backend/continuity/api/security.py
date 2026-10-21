"""Origin checks and bounded authentication throttling without buffering SSE bodies."""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from ..settings import Settings


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app
        self.attempts: OrderedDict[str, deque[float]] = OrderedDict()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        settings = Settings.read()
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"} and origin:
            same_host = urlsplit(origin).netloc == headers.get("host")
            if not same_host and origin.rstrip("/") not in settings.origins:
                return await JSONResponse({"detail": "Request origin is not allowed."}, 403)(scope, receive, send)
        if settings.environment == "production" and scope["path"].endswith(("/auth/login", "/auth/register")):
            client = scope.get("client")
            key = client[0] if client else "unknown"
            now = time.monotonic()
            attempts = self.attempts.setdefault(key, deque())
            self.attempts.move_to_end(key)
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if len(attempts) >= 10:
                return await JSONResponse({"detail": "Too many sign-in attempts. Try again in a minute."},
                                          429, headers={"Retry-After": "60"})(scope, receive, send)
            attempts.append(now)
            while len(self.attempts) > 10000:
                self.attempts.popitem(last=False)

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message["headers"]) + [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"x-frame-options", b"DENY"),
                ]
            await send(message)

        await self.app(scope, receive, secure_send)
