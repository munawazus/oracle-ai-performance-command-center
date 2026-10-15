"""Socket-level SSRF pinning for outbound webhook delivery (Enterprise Readiness Plan C.1).

`app/security/ssrf_guard.py`'s resolve-then-check can never fully close the DNS-rebinding
race: it resolves a hostname, validates the result, and then a *separate* resolution happens
later when httpx actually connects - an attacker's DNS server can answer safely for the
first lookup and unsafely for the second, since nothing ties the validated IP to the one
actually connected to (see `ssrf_guard.py`'s own docstring and
`app/integrations/webhook_sender.py`'s comment, both written when that gap was known but not
yet closed).

This module closes that gap by taking over connection establishment itself: the custom
network backend below resolves the hostname exactly once, validates every candidate IP, and
connects the raw TCP socket directly to a validated IP literal - never to the hostname. There
is no second resolution left for an attacker's DNS server to race against. TLS SNI/
certificate verification is unaffected: httpcore calls `stream.start_tls(server_hostname=...)`
with the original hostname on the already-open stream, regardless of which IP the raw socket
connected to, so certificate validation still checks the real hostname.
"""

import typing

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend
from httpx._transports.default import map_httpcore_exceptions

from app.security.ssrf_guard import HostResolver, is_private_or_reserved, resolve_host_real


class SSRFBlockedError(Exception):
    """Deliberately NOT an `httpcore.ConnectError` subclass - httpx's own exception-mapping
    (`map_httpcore_exceptions`, used below to give genuine network failures their normal
    `httpx.ConnectError`/`httpx.TimeoutException`/etc. types) matches by `isinstance` against
    httpcore's exception hierarchy. Inheriting from it would get this exception silently
    rewritten into a generic `httpx.ConnectError`, losing both its distinct type (callers
    need to tell "blocked for safety" apart from "genuinely unreachable") and its `host`/`ip`
    attributes."""

    def __init__(self, host: str, ip: str):
        self.host = host
        self.ip = ip
        super().__init__(f"Connection to '{host}' ({ip}) blocked: private/reserved address.")


class _AsyncResponseStream(httpx.AsyncByteStream):
    """Minimal equivalent of httpx's own internal response-stream wrapper - reimplemented
    here rather than importing httpx._transports.default's private class, so this module
    only depends on httpx's public API."""

    def __init__(self, httpcore_stream: typing.AsyncIterable[bytes]) -> None:
        self._httpcore_stream = httpcore_stream

    async def __aiter__(self) -> typing.AsyncIterator[bytes]:
        async for part in self._httpcore_stream:
            yield part

    async def aclose(self) -> None:
        if hasattr(self._httpcore_stream, "aclose"):
            await self._httpcore_stream.aclose()  # type: ignore[attr-defined]


class _SSRFSafeNetworkBackend(httpcore.AsyncNetworkBackend):
    """Wraps a real network backend, resolving and validating the target host itself and
    connecting to the validated IP literal - so the wrapped backend never gets a hostname to
    resolve a second time. Mirrors `assert_target_url_is_safe`'s all-candidates-must-be-safe
    stance: if any resolved IP is private/reserved, the whole connection is refused, even if
    another A/AAAA record in the same response would have been safe."""

    def __init__(self, real_backend: httpcore.AsyncNetworkBackend, resolve: HostResolver):
        self._real = real_backend
        self._resolve = resolve

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        ips = await self._resolve(host)
        if not ips:
            raise httpcore.ConnectError(f"Could not resolve host '{host}'.")
        for ip in ips:
            if is_private_or_reserved(ip):
                raise SSRFBlockedError(host, ip)
        return await self._real.connect_tcp(
            ips[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:  # pragma: no cover - not used by this platform
        return await self._real.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._real.sleep(seconds)


class SSRFSafeAsyncTransport(httpx.AsyncBaseTransport):
    """Drop-in replacement for `httpx.AsyncHTTPTransport` that pins the actually-connected
    socket's IP instead of trusting a separately-resolved one. No proxy/Unix-socket support -
    not needed by this platform's only user of a custom transport (webhook delivery)."""

    def __init__(self, *, resolve: HostResolver = resolve_host_real, verify: bool | str = True):
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=verify),
            network_backend=_SSRFSafeNetworkBackend(AutoBackend(), resolve),
        )

    async def __aenter__(self) -> "SSRFSafeAsyncTransport":
        await self._pool.__aenter__()
        return self

    async def __aexit__(self, exc_type=None, exc_value=None, traceback=None) -> None:
        await self._pool.__aexit__(exc_type, exc_value, traceback)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Type-narrowing only, not a security check - mirrors httpx's own AsyncHTTPTransport.
        assert isinstance(request.stream, typing.AsyncIterable)  # nosec B101
        req = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            with map_httpcore_exceptions():
                resp = await self._pool.handle_async_request(req)
        except SSRFBlockedError:
            # map_httpcore_exceptions only rewrites exceptions matching httpcore's own
            # hierarchy (see SSRFBlockedError's docstring for why this one deliberately
            # doesn't) - this except exists purely so mypy/readers see it's handled
            # deliberately, not falling through by accident.
            raise
        # Type-narrowing only, not a security check - mirrors httpx's own AsyncHTTPTransport.
        assert isinstance(resp.stream, typing.AsyncIterable)  # nosec B101
        return httpx.Response(
            status_code=resp.status,
            headers=resp.headers,
            stream=_AsyncResponseStream(resp.stream),
            extensions=resp.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()
