"""Regression tests for findings from a live, real-time QA pass (docs/QA_LIVE_FINDINGS.md) -
bugs that only surfaced by actually running the application against a real Oracle connection
and a genuinely unreachable Redis, not by reading code or running the existing suite against
fakes. Findings 01 (rate limiter crash) and 02 (naive datetime serialization) have their own
regression tests in tests/test_security_hardening.py and tests/test_dashboard_api.py
respectively; this file covers Finding 05 (Redis timeout) and Finding 06 (CORS on an
unhandled exception).
"""

from httpx import ASGITransport, AsyncClient

import app.redis_client as redis_client_module
from app.redis_client import get_redis


def test_redis_client_is_constructed_with_a_short_connect_and_socket_timeout():
    """Finding 05: with no explicit timeout, redis-py falls back to the OS-level TCP connect
    timeout (tens of seconds on Windows against an unreachable host) before every fail-open
    path (RateLimitMiddleware, require_rate_limit, login's limiter, /api/ready) can actually
    fail open - live testing measured a multi-second "Checking your session..." stall on
    every request while Redis was down. A short, explicit timeout makes "unreachable" get
    detected quickly instead of eventually.

    tests/conftest.py pre-seeds get_redis()'s module-level singleton with a fakeredis
    instance so the rest of the suite never opens a real socket - this test resets that
    singleton to force get_redis() to actually run its own from_url(...) construction, then
    restores the fake for every test that runs after it."""
    original = redis_client_module._redis
    redis_client_module._redis = None
    try:
        redis = get_redis()
        conn_kwargs = redis.connection_pool.connection_kwargs
        assert conn_kwargs["socket_connect_timeout"] <= 5
        assert conn_kwargs["socket_timeout"] <= 5
    finally:
        redis_client_module._redis = original


async def test_unhandled_exception_response_still_carries_cors_headers(admin_headers, monkeypatch):
    """Finding 06: a handler registered for the bare `Exception` type is routed by Starlette
    through ServerErrorMiddleware, which sits OUTSIDE every middleware added via
    app.add_middleware() - including CORSMiddleware, despite CORS being registered last
    (outermost) specifically so error responses carry CORS headers (see app/main.py's own
    comment). Confirmed empirically before this fix: a real unhandled exception's response
    was missing `Access-Control-Allow-Origin` entirely, which is why the pre-fix rate-limiter
    crash (Finding 01) surfaced in the browser as a confusing CORS failure instead of the
    real 500 underneath it. Every OTHER exception handler in this app (AppError,
    RateLimitedError, RequestValidationError) is keyed to a specific type, which Starlette
    routes through ExceptionMiddleware (inside CORS) - those were never affected.

    Starlette's ServerErrorMiddleware re-raises the original exception after sending the
    handler's response (so server-side tooling still sees it) - the shared `client` fixture's
    ASGITransport propagates that re-raise to the test itself by default, same as it would to
    a real server's own logging, but a real browser only ever sees the response that was
    actually sent on the wire. `raise_app_exceptions=False` reproduces exactly what a browser
    experiences: the response, not the exception."""
    import app.repositories.rbac_repo as rbac_repo_module
    from app.main import app

    async def _boom(self, user_id):
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr(rbac_repo_module.RbacRepository, "get_user_role_names", _boom)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    origin = "http://localhost:5173"
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/auth/me", headers={**admin_headers, "Origin": origin})

    assert resp.status_code == 500
    assert resp.headers.get("access-control-allow-origin") == origin
    assert resp.headers.get("access-control-allow-credentials") == "true"
