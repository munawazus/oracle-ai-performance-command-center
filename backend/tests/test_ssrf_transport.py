"""app/security/ssrf_transport.py (Enterprise Readiness Plan C.1): pins the actually-
connected socket's IP instead of trusting a separately-resolved one. These tests exercise
the new logic directly with fake network backends/resolvers - no real sockets - the same
test-double convention as app/security/ssrf_guard.py's own HostResolver Protocol.
"""

import typing

import httpcore
import httpx
import pytest

from app.security.ssrf_transport import SSRFBlockedError, SSRFSafeAsyncTransport, _SSRFSafeNetworkBackend


class FakeNetworkStream(httpcore.AsyncNetworkStream):
    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return b""

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return self


class FakeRealBackend(httpcore.AsyncNetworkBackend):
    """Records what it was actually asked to connect to - stands in for the real backend
    (httpcore's AutoBackend) that would otherwise open a genuine socket."""

    def __init__(self) -> None:
        self.connect_calls: list[tuple[str, int]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connect_calls.append((host, port))
        return FakeNetworkStream()


def _fake_resolver(ips: list[str]):
    async def resolve(host: str) -> list[str]:
        return ips

    return resolve


async def test_connects_to_the_resolved_ip_literal_not_the_hostname():
    real = FakeRealBackend()
    backend = _SSRFSafeNetworkBackend(real, _fake_resolver(["93.184.216.34"]))

    await backend.connect_tcp("example.com", 443)

    assert real.connect_calls == [("93.184.216.34", 443)]


async def test_blocks_a_private_resolved_ip_before_ever_touching_the_real_backend():
    real = FakeRealBackend()
    backend = _SSRFSafeNetworkBackend(real, _fake_resolver(["10.0.0.5"]))

    with pytest.raises(SSRFBlockedError) as exc_info:
        await backend.connect_tcp("internal.example", 443)

    assert exc_info.value.host == "internal.example"
    assert exc_info.value.ip == "10.0.0.5"
    assert real.connect_calls == []  # never reached the real backend


async def test_blocks_loopback_and_link_local_metadata_address():
    for ip in ["127.0.0.1", "169.254.169.254"]:
        real = FakeRealBackend()
        backend = _SSRFSafeNetworkBackend(real, _fake_resolver([ip]))
        with pytest.raises(SSRFBlockedError):
            await backend.connect_tcp("attacker.example", 443)
        assert real.connect_calls == []


async def test_blocks_if_any_resolved_ip_is_unsafe_even_when_another_is_public():
    """Matches assert_target_url_is_safe's own stance: one bad answer in a multi-A-record
    response blocks the whole connection, rather than trusting the client to pick the safe
    one (which an attacker controlling DNS could not rely on anyway, but is not a chance
    worth taking)."""
    real = FakeRealBackend()
    backend = _SSRFSafeNetworkBackend(real, _fake_resolver(["93.184.216.34", "127.0.0.1"]))

    with pytest.raises(SSRFBlockedError):
        await backend.connect_tcp("mixed.example", 443)
    assert real.connect_calls == []


async def test_unresolvable_host_raises_a_plain_connect_error_not_ssrf_blocked():
    real = FakeRealBackend()
    backend = _SSRFSafeNetworkBackend(real, _fake_resolver([]))

    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("does-not-resolve.example", 443)
    assert real.connect_calls == []


async def test_resolves_exactly_once_and_connects_to_precisely_that_answer():
    """The property that actually closes the DNS-rebinding gap: a resolve-then-check pattern
    does two independent lookups (one to validate, a separate one inside the real HTTP
    client later) that an attacker's DNS server could answer differently. This backend
    performs exactly one resolution per connection attempt and connects to precisely what it
    returned - there is no second lookup left to race."""
    real = FakeRealBackend()
    resolve_calls: list[str] = []

    async def resolve(host: str) -> list[str]:
        resolve_calls.append(host)
        return ["93.184.216.34"]

    backend = _SSRFSafeNetworkBackend(real, resolve)
    await backend.connect_tcp("attacker-controlled.example", 443)

    assert resolve_calls == ["attacker-controlled.example"]
    assert real.connect_calls == [("93.184.216.34", 443)]


async def test_transport_raises_ssrf_blocked_error_not_a_generic_connect_error():
    """The critical exception-identity property: httpx's own httpcore-exception-mapping
    (used so genuine network failures still surface as normal httpx.ConnectError/etc.) must
    not silently rewrite SSRFBlockedError into a generic httpx.ConnectError - callers need to
    tell "blocked for safety" apart from "genuinely unreachable"."""
    transport = SSRFSafeAsyncTransport(resolve=_fake_resolver(["127.0.0.1"]))
    request = httpx.Request("GET", "https://example.com/hook")

    with pytest.raises(SSRFBlockedError):
        await transport.handle_async_request(request)

    await transport.aclose()


async def test_transport_translates_a_successful_httpcore_response_into_an_httpx_response():
    """The request/response translation logic (mirrors httpx's own AsyncHTTPTransport) is
    exercised directly against a fake pool, without needing a real socket."""

    class FakePool:
        async def handle_async_request(self, req: httpcore.Request) -> httpcore.Response:
            assert req.method == b"GET"

            async def body() -> typing.AsyncIterator[bytes]:
                yield b"ok"

            return httpcore.Response(status=200, headers=[(b"content-type", b"text/plain")], content=body())

    transport = SSRFSafeAsyncTransport(resolve=_fake_resolver(["93.184.216.34"]))
    transport._pool = FakePool()  # type: ignore[assignment]

    response = await transport.handle_async_request(httpx.Request("GET", "https://example.com/hook"))
    await response.aread()

    assert response.status_code == 200
    assert response.text == "ok"


# ---------------------------------------------------------------------------
# Real network - skips (doesn't fail) when this environment has no internet access.
# Everything above uses fakes; these two prove the real DNS resolution -> IP-pinning ->
# real TCP+TLS connect path actually works end to end, not just against mocks.
# ---------------------------------------------------------------------------


def _real_internet_reachable() -> bool:
    import socket as _socket

    try:
        with _socket.create_connection(("example.com", 443), timeout=3):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _real_internet_reachable(), reason="no real internet access in this environment")
async def test_real_https_request_succeeds_through_the_ssrf_safe_transport():
    async with httpx.AsyncClient(transport=SSRFSafeAsyncTransport(), timeout=10.0) as client:
        response = await client.get("https://example.com/")

    assert response.status_code == 200
    assert "Example Domain" in response.text


@pytest.mark.skipif(not _real_internet_reachable(), reason="no real internet access in this environment")
async def test_real_loopback_target_is_actually_blocked_not_just_in_theory():
    async with httpx.AsyncClient(transport=SSRFSafeAsyncTransport(), timeout=5.0) as client:
        with pytest.raises(SSRFBlockedError):
            await client.get("http://127.0.0.1:1/")
