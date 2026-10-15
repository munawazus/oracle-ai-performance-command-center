"""ASGI middleware for Master Plan Phase 24 (security hardening) - applied to every request
in app/main.py, ahead of and independent from the per-route RBAC/rate-limit dependencies in
app/security/deps.py.
"""

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.logging_config import get_logger
from app.redis_client import get_redis
from app.security.deps import get_client_ip
from app.services.rate_limit_service import RateLimitService

logger = get_logger(__name__)

# Health checks are polled frequently by infra (load balancers, container orchestrators) and
# carry no sensitive data - exempt them so a busy probe interval can never itself trigger 429s.
_RATE_LIMIT_EXEMPT_PREFIXES = ("/api/health", "/api/ready")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Standard defense-in-depth response headers (master plan Sec 29/36 "API hardening").
    None of these replace RBAC or input validation - they narrow what a successful attack
    (XSS, clickjacking, MIME sniffing) can actually do."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        if get_settings().is_production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        return response


class BodySizeLimitMiddleware:
    """Enforces the request body size ceiling against actual streamed bytes, not just the
    client-declared Content-Length header (Enterprise Readiness Plan C.2 - the header-only
    version of this check, kept as a fast pre-check below, could be bypassed entirely by a
    client using chunked Transfer-Encoding with no Content-Length, or simply lying about a
    smaller one; see docs/THREAT_MODEL.md).

    Deliberately a raw ASGI middleware (`__call__(scope, receive, send)`), not
    `BaseHTTPMiddleware`: `BaseHTTPMiddleware` reads the *entire* request body into memory
    before handing control to `dispatch`, which would defeat the purpose of this check for
    exactly the case it exists to catch - by the time `dispatch` could inspect anything, an
    oversized body would already be fully buffered. Instead this wraps the ASGI `receive`
    callable itself, counting bytes as `http.request` messages arrive and raising before the
    running total - not the declared header - ever exceeds the limit.

    Raises `starlette.exceptions.HTTPException`, specifically - NOT a plain/`AppError`
    exception - from inside the wrapped `receive()` call, while a route handler or Pydantic
    is mid-read. This matters: FastAPI's own JSON-body-parsing code
    (`fastapi/routing.py::get_request_handler`) wraps `await request.json()` in a broad
    `except Exception` that rewrites *any* other exception type into its own generic
    `400 "There was an error parsing the body"` - confirmed the hard way, via a real
    end-to-end test that returned exactly that 400 instead of a 413 before this was
    switched from a custom `AppError` subclass to `HTTPException`. The one documented
    exception to that broad catch is `except HTTPException: raise` immediately above it -
    which is otherwise how a dependency is meant to reject a request from deep inside
    FastAPI's own request-body handling. Starlette's own default `HTTPException` handler
    (registered automatically, same shape FastAPI's own routes use) then produces the
    correct `{"detail": ...}` JSON response with the exception's own status code.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Fast path: an honestly-declared oversized Content-Length is rejected immediately,
        # without reading anything or ever invoking the downstream app - cheaper than waiting
        # for streamed bytes to accumulate, and it's most requests' actual behavior.
        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                pass

        total = 0

        async def limited_receive() -> Message:
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    raise HTTPException(status_code=413, detail="Request body too large.")
            return message

        await self.app(scope, limited_receive, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = b'{"detail":"Request body too large."}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """A per-client-IP floor across every /api request, on top of any route's own tighter
    limit (require_rate_limit in app/security/deps.py) and login's own per-IP limit. Fails
    open (logs and allows the request through) if Redis itself is unreachable - a rate
    limiter outage must never take down the whole API, same "external dependency failure
    never blocks core function" principle as SMTP/webhook/AI failures never blocking incident
    persistence elsewhere in this platform."""

    def __init__(self, app, *, limit: int, window_seconds: int):
        super().__init__(app)
        self.limit = limit
        self.window_seconds = window_seconds

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if not path.startswith("/api/") or path.startswith(_RATE_LIMIT_EXEMPT_PREFIXES):
            return await call_next(request)

        ip = get_client_ip(request) or "unknown"
        try:
            limiter = RateLimitService(get_redis())
            allowed, _count = await limiter.hit(f"global:{ip}", limit=self.limit, window_seconds=self.window_seconds)
        except Exception:
            logger.error("rate_limit_check_failed_failing_open", path=path)
            return await call_next(request)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again shortly."},
                headers={"Retry-After": str(self.window_seconds)},
            )
        return await call_next(request)
