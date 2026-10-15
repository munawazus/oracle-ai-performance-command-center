"""Thin, mockable wrapper around httpx - same pattern as app/notifications/mailer.py and
app/oracle/connector.py. Tests inject a fake WebhookSender instead of needing a real HTTP
endpoint.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import get_settings
from app.exceptions import ValidationAppError
from app.security.ssrf_guard import assert_target_url_is_safe
from app.security.ssrf_transport import SSRFBlockedError, SSRFSafeAsyncTransport


@dataclass(frozen=True)
class WebhookSendResult:
    status_code: int
    response_snippet: str


class WebhookSendError(Exception):
    def __init__(self, category: str, message: str):
        self.category = category
        super().__init__(message)


class WebhookSender(Protocol):
    async def __call__(self, *, url: str, payload: dict, secret: str | None) -> WebhookSendResult: ...


async def send_webhook(*, url: str, payload: dict, secret: str | None) -> WebhookSendResult:
    # Master Plan Phase 27 (Adversarial QA): re-checked immediately before every send, not
    # just once at integration create/update time. This is a fast, clean rejection for the
    # common case (an obviously-internal hostname/IP) before a socket is ever opened; it is
    # not the authoritative defense against DNS rebinding by itself - that's
    # SSRFSafeAsyncTransport below (Enterprise Readiness Plan C.1), which pins the actually-
    # connected socket's IP so no separate, later resolution can slip in a different answer.
    allow_internal = get_settings().allow_internal_integration_targets
    try:
        await assert_target_url_is_safe(url, allow_internal=allow_internal)
    except ValidationAppError as exc:
        raise WebhookSendError("ssrf_blocked", str(exc)) from exc

    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if secret:
        # Lets a generic WEBHOOK receiver verify authenticity - same convention as GitHub/
        # Stripe webhooks. Slack/Teams incoming webhooks have no way to check this, so it's
        # only meaningful for the WEBHOOK integration type, but harmless to send regardless.
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-Signature-256"] = f"sha256={signature}"

    # allow_internal bypasses the socket-pinning transport too - it exists for dev/test
    # environments where an internal target is intentional, and assert_target_url_is_safe
    # above already returned without checking anything in that case.
    transport = None if allow_internal else SSRFSafeAsyncTransport()
    try:
        # follow_redirects defaults to False in httpx, which is what SSRF-safety here
        # depends on - stated explicitly so it can never silently change out from under this
        # guard (e.g. via a future httpx upgrade changing its own default).
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False, transport=transport) as client:
            response = await client.post(url, content=body, headers=headers)
    except SSRFBlockedError as exc:
        raise WebhookSendError("ssrf_blocked", str(exc)) from exc
    except httpx.RequestError as exc:
        # httpx's own timeout/connection exceptions sometimes carry an empty str() - fall
        # back to the exception's type name so the caller (and the Test button's response
        # body) never shows a blank, unhelpful message.
        detail = str(exc) or type(exc).__name__
        raise WebhookSendError("network_error", detail) from exc

    if response.status_code >= 400:
        raise WebhookSendError("http_error", f"HTTP {response.status_code}")

    return WebhookSendResult(status_code=response.status_code, response_snippet=response.text[:500])
