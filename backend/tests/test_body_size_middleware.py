"""app/security/middleware.py's BodySizeLimitMiddleware (Enterprise Readiness Plan C.2):
enforces the size ceiling against actual streamed bytes, not just the client-declared
Content-Length header. These drive the raw ASGI middleware directly with a fake downstream
app and a fake `receive`, since the point being tested - a body whose real bytes exceed what
was declared, or that declares nothing at all - can't be produced by httpx's own request
builder (it always sets an honest Content-Length matching what it actually sends).
"""

import pytest
from starlette.exceptions import HTTPException

from app.security.middleware import BodySizeLimitMiddleware


def _make_scope(headers: list[tuple[bytes, bytes]] | None = None, scope_type: str = "http") -> dict:
    return {"type": scope_type, "method": "POST", "path": "/api/whatever", "headers": headers or []}


class RecordingApp:
    """Stands in for the rest of the ASGI stack (router/endpoint) - drains `receive()` the
    same way Starlette's own request-body reading does, until `more_body` is false."""

    def __init__(self):
        self.received_bodies: list[bytes] = []
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        while True:
            message = await receive()
            self.received_bodies.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})


def _chunked_receive(chunks: list[bytes]):
    """Delivers `chunks` as a series of `http.request` ASGI messages - this is how uvicorn
    presents a body to the app either way, whether the client used a Content-Length header
    or chunked Transfer-Encoding; ASGI itself has no concept of the wire framing."""
    messages = [
        {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
        for i, chunk in enumerate(chunks)
    ]

    async def receive():
        return messages.pop(0)

    return receive


async def test_rejects_actual_streamed_bytes_over_the_limit_with_no_content_length_header_at_all():
    """The exact gap this item exists to close: chunked Transfer-Encoding carries no
    Content-Length header at all, which the old header-only check simply never saw."""
    app = RecordingApp()
    middleware = BodySizeLimitMiddleware(app, max_bytes=10)
    receive = _chunked_receive([b"01234567890123456789"])  # 21 bytes, no header
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    with pytest.raises(HTTPException) as exc_info:
        await middleware(_make_scope(), receive, send)
    assert exc_info.value.status_code == 413


async def test_rejects_actual_streamed_bytes_over_the_limit_despite_a_dishonest_smaller_content_length():
    app = RecordingApp()
    middleware = BodySizeLimitMiddleware(app, max_bytes=10)
    headers = [(b"content-length", b"5")]  # lies - the real body is much larger
    receive = _chunked_receive([b"01234567890123456789"])
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    with pytest.raises(HTTPException) as exc_info:
        await middleware(_make_scope(headers), receive, send)
    assert exc_info.value.status_code == 413


async def test_rejects_as_soon_as_the_running_total_crosses_the_limit_across_multiple_chunks():
    """A body delivered in several small pieces, none individually over the limit, must
    still be caught once their sum crosses it - not just a single oversized chunk."""
    app = RecordingApp()
    middleware = BodySizeLimitMiddleware(app, max_bytes=10)
    receive = _chunked_receive([b"123456", b"789012"])  # 6 + 6 = 12 bytes, over a 10-byte limit
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    with pytest.raises(HTTPException) as exc_info:
        await middleware(_make_scope(), receive, send)
    assert exc_info.value.status_code == 413


async def test_allows_a_body_within_the_limit_streamed_across_multiple_chunks():
    app = RecordingApp()
    middleware = BodySizeLimitMiddleware(app, max_bytes=100)
    receive = _chunked_receive([b"12345", b"67890"])  # 10 bytes total, under the limit
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await middleware(_make_scope(), receive, send)

    assert app.called
    assert app.received_bodies == [b"12345", b"67890"]
    assert sent[0]["status"] == 200


async def test_fast_path_rejects_an_honestly_declared_oversized_content_length_without_reading_anything():
    """The header check is kept as a cheap, immediate rejection for the common/honest case -
    it must never even call the downstream app or read a single byte."""
    app = RecordingApp()
    middleware = BodySizeLimitMiddleware(app, max_bytes=10)
    headers = [(b"content-length", b"1000")]

    async def receive():
        raise AssertionError("must not be called - the fast path rejects before reading anything")

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await middleware(_make_scope(headers), receive, send)

    assert not app.called
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


async def test_real_app_returns_a_clean_413_not_an_unhandled_exception_or_500():
    """Drives the actual app object (app/main.py's `app`, the same instance every other test
    hits through the ASGI test client) directly at the ASGI level, since httpx's own request
    builder always sets an honest Content-Length matching what it really sends and so cannot
    produce the "declares less than it sends" case this test needs. This is the one test that
    proves the HTTPException raised from deep inside a wrapped `receive()` actually survives
    FastAPI's own body-parsing exception handling and comes back as a real 413 response
    end-to-end - not just that the middleware raises the right exception in isolation. (An
    earlier version of this middleware raised a plain AppError subclass here instead, which
    this exact test caught: FastAPI's request-body parsing swallows any non-HTTPException
    into its own generic 400 "error parsing body" response - see the class docstring.)"""
    from app.main import app as real_app

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [(b"content-type", b"application/json"), (b"content-length", b"2")],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    receive = _chunked_receive([b'{"email": "a@b.c", "password": "' + b"x" * 6_000_000 + b'"}'])
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await real_app(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert start["status"] == 413
    assert b"too large" in body.lower()


async def test_non_http_scope_passes_through_untouched():
    app = RecordingApp()
    middleware = BodySizeLimitMiddleware(app, max_bytes=10)

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        pass

    await middleware(_make_scope(scope_type="websocket"), receive, send)
    assert app.called
