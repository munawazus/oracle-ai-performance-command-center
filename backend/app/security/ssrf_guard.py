"""Blocks an admin-configured outbound target (currently: Integration.target_url) from
resolving to a private/loopback/link-local/reserved address (master plan Sec 29/36 - SSRF is
explicitly named in the Phase 24/27 security-test list). Checked at config-time
(create/update, app/services/integration_service.py) AND again immediately before every send
(app/integrations/webhook_sender.py, added in Phase 27) - the second check shrinks, but does
not eliminate, the DNS-rebinding window: see docs/THREAT_MODEL.md for the residual gap (a
resolve-then-connect check can never fully close the race against the actual socket connect,
short of pinning the connected IP itself - a materially larger change than this phase made).

DNS resolution is a real external dependency, same as SMTP/Oracle/webhook delivery - it's
behind a mockable Protocol (same test-double pattern as EmailSender/WebhookSender/
OracleConnector) so tests never make a real socket call.
"""

import asyncio
import ipaddress
import socket
from typing import Protocol
from urllib.parse import urlparse

from app.exceptions import ValidationAppError

# Cloud metadata endpoints are the highest-value SSRF target (AWS/GCP/Azure instance
# credentials) and are link-local addresses that ipaddress already flags - no extra check
# needed beyond is_link_local. localhost variants are blocked by hostname as a fast path
# before DNS resolution even runs.
_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}


class HostResolver(Protocol):
    async def __call__(self, host: str) -> list[str]:
        """Returns the resolved IP address strings for `host`, or an empty list if it
        can't be resolved right now."""
        ...


async def resolve_host_real(host: str) -> list[str]:
    # A literal IP needs no lookup at all - parsing it is pure computation (no syscall), and
    # skipping straight to that avoids even a loopback DNS/socket round-trip for the common
    # case of an operator pasting an IP directly into target_url.
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        pass

    try:
        # socket.getaddrinfo is blocking - run off the event loop rather than stalling every
        # other request while DNS resolves.
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except socket.gaierror:
        # Can't resolve right now - don't block configuration on transient DNS flakiness; the
        # actual delivery attempt will simply fail and surface as a normal delivery error.
        return []
    return [info[4][0] for info in infos]


def is_private_or_reserved(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified


async def assert_target_url_is_safe(
    url: str, *, allow_internal: bool = False, resolve: HostResolver = resolve_host_real
) -> None:
    if allow_internal:
        return

    host = urlparse(url).hostname
    if not host:
        raise ValidationAppError("target_url must include a hostname.")
    if host.lower() in _BLOCKED_HOSTNAMES:
        raise ValidationAppError("target_url may not point at an internal/reserved host.")

    # A literal IP resolves instantly with no network I/O; only an actual hostname triggers
    # a real DNS lookup - either way this goes through the same injectable `resolve`.
    for ip_str in await resolve(host):
        if is_private_or_reserved(ip_str):
            raise ValidationAppError(
                f"target_url resolves to a private/reserved address ({ip_str}) and is not allowed."
            )
