import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from carapi.api.errors import error_response
from carapi.core.config import Settings
from carapi.core.rate_limit import SlidingWindowLimiter, ceil_seconds
from carapi.core.request_context import request_id_var
from carapi.core.security import API_KEY_HEADER, is_valid_key, key_fingerprint

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_RATE_LIMIT_EXEMPT = ("/health", "/docs", "/redoc", "/openapi.json")

access_log = logging.getLogger("carapi.access")
error_log = logging.getLogger("carapi.errors")


class RequestContextMiddleware:
    """Outermost: assigns X-Request-ID, logs access, turns unhandled errors into JSON 500s."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status_code = 500
        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, started
            if message["type"] == "http.response.start":
                started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            error_log.exception("Unhandled error on %s %s", scope["method"], scope["path"])
            if started:
                raise
            status_code = 500
            await error_response(500, "Internal server error")(scope, receive, send_wrapper)
        finally:
            ms = (time.perf_counter() - start) * 1000
            client = scope.get("client")
            access_log.info(
                "%s %s %d %.1fms client=%s",
                scope["method"],
                scope["path"],
                status_code,
                ms,
                client[0] if client else "-",
            )
            request_id_var.reset(token)


class RateLimitMiddleware:
    """Per-identity limit: valid API key -> per key; otherwise per client IP."""

    def __init__(self, app: ASGIApp, settings: Settings, limiter: SlidingWindowLimiter) -> None:
        self.app = app
        self.settings = settings
        self.limiter = limiter

    def _identity(self, scope: Scope) -> str:
        key = Headers(scope=scope).get(API_KEY_HEADER)
        if key and is_valid_key(key, self.settings.api_keys):
            return f"key:{key_fingerprint(key)}"
        client = scope.get("client")
        return f"ip:{client[0] if client else 'unknown'}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or not self.settings.rate_limit_enabled
            or scope["path"].startswith(_RATE_LIMIT_EXEMPT)
        ):
            await self.app(scope, receive, send)
            return

        result = self.limiter.hit(self._identity(scope))
        headers = {
            "X-RateLimit-Limit": str(result.limit),
            "X-RateLimit-Remaining": str(result.remaining),
            "X-RateLimit-Reset": str(ceil_seconds(result.reset_after)),
        }
        if not result.allowed:
            headers["Retry-After"] = headers["X-RateLimit-Reset"]
            response = error_response(429, "Rate limit exceeded", headers=headers)
            await response(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).update(headers)
            await send(message)

        await self.app(scope, receive, send_wrapper)
