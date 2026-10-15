"""Master Plan Phase 24 (security hardening): security headers, request body size limit,
per-route rate limiting, and the SSRF guard's pure resolution logic. The blanket
per-IP RateLimitMiddleware default (300/min) is deliberately generous, so it is tested in
isolation against a tiny standalone ASGI app rather than by firing 300+ real requests through
the full application in every test run.
"""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.security.middleware import RateLimitMiddleware
from app.security.ssrf_guard import assert_target_url_is_safe

DB_PAYLOAD = {
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


async def test_security_headers_present_on_every_response(client, admin_headers):
    resp = await client.get("/api/databases", headers=admin_headers)
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "permissions-policy" in resp.headers


async def test_security_headers_present_even_on_error_response(client):
    resp = await client.get("/api/databases")  # unauthenticated -> 401
    assert resp.status_code == 401
    assert resp.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------------------
# Body size limit
# ---------------------------------------------------------------------------


async def test_oversized_request_body_rejected_before_auth_or_parsing(client):
    huge_payload = {"email": "a@b.com", "password": "x" * 6_000_000}
    resp = await client.post("/api/auth/login", json=huge_payload)
    assert resp.status_code == 413


async def test_normal_sized_request_body_not_affected(client, admin_headers):
    resp = await client.get("/api/databases", headers=admin_headers)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Per-route rate limiting (tight enough limits to exhaust in a handful of requests)
# ---------------------------------------------------------------------------


async def test_bulk_create_rate_limit_exceeded_returns_429(client, admin_headers):
    payload_for = lambda i: {  # noqa: E731
        "databases": [{**DB_PAYLOAD, "name": f"RLDB{i}"}]
    }
    responses = []
    for i in range(6):  # limit is 5/minute
        responses.append(await client.post("/api/databases/bulk", headers=admin_headers, json=payload_for(i)))

    assert [r.status_code for r in responses[:5]] == [201] * 5
    assert responses[5].status_code == 429
    assert "Retry-After" in responses[5].headers


async def test_ai_ask_rate_limit_exceeded_returns_429(client, admin_headers):
    responses = []
    for _ in range(11):  # limit is 10/minute; AI is disabled so each 502s, which still counts
        responses.append(await client.post("/api/ai/ask", headers=admin_headers, json={"question": "How's it going?"}))

    assert all(r.status_code == 502 for r in responses[:10])
    assert responses[10].status_code == 429


async def test_rate_limit_is_scoped_per_user(client, admin_headers):
    """A different user's requests are not affected by another user's exhausted budget -
    require_rate_limit keys by user id, not by a shared IP bucket."""
    await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"email": "rluser@ocpcc.dev", "password": "RlUserPassw0rd!123", "display_name": "RL", "roles": ["OPERATOR"]},
    )
    login = await client.post("/api/auth/login", json={"email": "rluser@ocpcc.dev", "password": "RlUserPassw0rd!123"})
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    for _ in range(10):
        resp = await client.post("/api/ai/ask", headers=admin_headers, json={"question": "How's it going?"})
        assert resp.status_code == 502  # exhausts admin's own budget, not rluser's

    resp = await client.post("/api/ai/ask", headers=other_headers, json={"question": "How's it going?"})
    assert resp.status_code == 502  # a fresh budget for a different user, not 429


# ---------------------------------------------------------------------------
# RateLimitMiddleware (the blanket per-IP floor) - isolated so this file never needs to
# fire 300+ requests through the full app to prove it works
# ---------------------------------------------------------------------------


@pytest.fixture
def rate_limited_mini_app():
    async def endpoint(request):
        return JSONResponse({"ok": True})

    mini_app = Starlette(routes=[Route("/api/ping", endpoint)])
    mini_app.add_middleware(RateLimitMiddleware, limit=3, window_seconds=60)
    return mini_app


async def test_rate_limit_middleware_blocks_after_limit(rate_limited_mini_app):
    transport = httpx.ASGITransport(app=rate_limited_mini_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        responses = [await ac.get("/api/ping") for _ in range(4)]

    assert [r.status_code for r in responses[:3]] == [200, 200, 200]
    assert responses[3].status_code == 429
    assert responses[3].headers["Retry-After"] == "60"


async def test_rate_limit_middleware_exempts_health_endpoints():
    async def endpoint(request):
        return JSONResponse({"ok": True})

    mini_app = Starlette(routes=[Route("/api/health", endpoint)])
    mini_app.add_middleware(RateLimitMiddleware, limit=1, window_seconds=60)
    transport = httpx.ASGITransport(app=mini_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        responses = [await ac.get("/api/health") for _ in range(5)]

    assert all(r.status_code == 200 for r in responses)


async def test_rate_limit_middleware_ignores_non_api_paths():
    async def endpoint(request):
        return JSONResponse({"ok": True})

    mini_app = Starlette(routes=[Route("/docs", endpoint)])
    mini_app.add_middleware(RateLimitMiddleware, limit=1, window_seconds=60)
    transport = httpx.ASGITransport(app=mini_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        responses = [await ac.get("/docs") for _ in range(3)]

    assert all(r.status_code == 200 for r in responses)


# ---------------------------------------------------------------------------
# SSRF guard - pure resolution logic, no real DNS/socket calls
# ---------------------------------------------------------------------------


async def _fake_resolver(ips: list[str]):
    async def resolve(host: str) -> list[str]:
        return ips

    return resolve


async def test_ssrf_guard_allows_public_ip():
    await assert_target_url_is_safe("https://example.com/hook", resolve=await _fake_resolver(["93.184.216.34"]))


async def test_ssrf_guard_blocks_private_ip():
    with pytest.raises(Exception, match="private|reserved"):
        await assert_target_url_is_safe("https://internal.example/hook", resolve=await _fake_resolver(["10.0.0.5"]))


async def test_ssrf_guard_blocks_loopback():
    with pytest.raises(Exception):
        await assert_target_url_is_safe("https://weird.example/hook", resolve=await _fake_resolver(["127.0.0.1"]))


async def test_ssrf_guard_blocks_localhost_hostname_without_resolving():
    calls = []

    async def resolve(host: str) -> list[str]:
        calls.append(host)
        return ["93.184.216.34"]  # would be allowed if reached - it must not be reached

    with pytest.raises(Exception):
        await assert_target_url_is_safe("http://localhost:9000/hook", resolve=resolve)
    assert calls == []  # the hostname fast-path short-circuited before any resolution


async def test_ssrf_guard_allow_internal_flag_bypasses_the_check():
    await assert_target_url_is_safe("http://127.0.0.1/hook", allow_internal=True, resolve=await _fake_resolver(["127.0.0.1"]))


async def test_ssrf_guard_unresolvable_host_does_not_block():
    """DNS flakiness shouldn't block configuration - the real send attempt will simply fail
    later and surface as a normal delivery error."""
    await assert_target_url_is_safe("https://does-not-resolve.example/hook", resolve=await _fake_resolver([]))
