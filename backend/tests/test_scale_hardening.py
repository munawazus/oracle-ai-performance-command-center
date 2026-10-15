"""Master Plan Phase 25 (Scale testing): correctness tests for behavior that only shows up
under fleet load - a paginated repository query silently dropping rows past its default
page size, a bounded-batch queue processor under a large backlog, and the AI rate limiter
under real concurrency. These are fast, CI-sized proxies for the properties the standalone
benchmark (scripts/scale_bench.py) exercises at full 10/50/100+ database scale; see
docs/SCALE_TEST_REPORT.md for the actual load-test results and methodology.
"""

import asyncio
import math

from app.ai.gateway import get_copilot_llm_client
from app.db import SessionLocal
from app.integrations.webhook_sender import WebhookSendResult
from app.main import app
from app.models.integration import STATUS_SENT as INTEGRATION_STATUS_SENT
from app.models.notification import STATUS_SENT as NOTIFICATION_STATUS_SENT
from app.repositories.audit_repo import AuditRepository
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.integration_repo import IntegrationDeliveryRepository, IntegrationRepository
from app.repositories.notification_repo import NotificationEventRepository, NotificationSettingsRepository
from app.repositories.secret_repo import SecretRepository
from app.services.audit_service import AuditService
from app.services.integration_service import IntegrationService
from app.services.notification_service import NotificationService
from app.services.secret_service import LocalEncryptedSecretProvider
from tests.test_copilot_service import FakeLLMClient

# app/repositories/database_repo.py's list_all() defaults to a 200-row page, and
# list_active_ids() pages internally in batches of 500 - both must be exceeded to prove the
# paging logic actually loops rather than truncating.
FLEET_SIZE_BEYOND_ONE_PAGE = 650


async def test_list_active_ids_covers_a_fleet_larger_than_one_internal_page(client, admin_headers):
    """Regression test for a real bug found while researching Phase 25: both
    CollectorService.run_once_all_active and AnalyticsService.run_once_all_active used to
    call list_all(limit=1000), which would have silently stopped collecting telemetry for
    every database past the 1000th once a fleet grew that large. Exercising the fix at 650
    rows (well past list_active_ids()'s internal 500-row page size) proves the pagination
    loop, not just the common case of one page."""
    async with SessionLocal() as db:
        repo = MonitoredDatabaseRepository(db)
        for i in range(FLEET_SIZE_BEYOND_ONE_PAGE):
            await repo.create(
                name=f"SCALE{i:04d}",
                host="oracle.internal.ocpcc.dev",
                port=1521,
                service_name="ORCLPDB1",
                edition_hint="SE2",
                created_by_user_id=None,
            )
        await db.commit()

        active_ids = await repo.list_active_ids()

    assert len(active_ids) == FLEET_SIZE_BEYOND_ONE_PAGE
    assert len(set(active_ids)) == FLEET_SIZE_BEYOND_ONE_PAGE


async def _register_database(client, admin_headers, name: str) -> str:
    resp = await client.post(
        "/api/databases",
        headers=admin_headers,
        json={
            "name": name,
            "host": "oracle.internal.ocpcc.dev",
            "port": 1521,
            "service_name": "ORCLPDB1",
            "edition_hint": "SE2",
            "username": "monitoring_ro",
            "password": "SuperSecretOraclePassword!",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_notification_queue_drains_a_backlog_larger_than_one_batch(client, admin_headers):
    """process_pending() intentionally caps itself at 50 events per call (bounded per-tick
    scheduler work - see app/services/notification_service.py). Seed a backlog large enough
    to require three ticks and confirm the queue actually drains completely rather than
    stalling on, or silently dropping, whatever doesn't fit in the first batch."""
    db_id = await _register_database(client, admin_headers, "NOTIFYSCALE01")
    backlog_size = 120  # not a multiple of 50, to also prove the last partial batch drains

    async with SessionLocal() as db:
        incident_repo = IncidentRepository(db)
        event_repo = NotificationEventRepository(db)
        settings_repo = NotificationSettingsRepository(db)
        from app.models.base import utcnow

        await settings_repo.update(
            smtp_host="smtp.internal.ocpcc.dev",
            smtp_port=587,
            smtp_use_tls=True,
            sender_email="alerts@ocpcc.dev",
            sender_name="OCPCC Alerts",
            default_recipients=["oncall@ocpcc.dev"],
            escalation_recipients=[],
        )
        now = utcnow()
        for i in range(backlog_size):
            incident = await incident_repo.create(
                monitored_database_id=db_id,
                severity="HIGH",
                title=f"Scale test incident {i}",
                opened_at=now,
                last_evidence_at=now,
            )
            await event_repo.create(
                incident_id=incident.id,
                dedupe_key=f"scale-notify-{i}",
                severity="HIGH",
                recipients=["oncall@ocpcc.dev"],
                subject=f"[HIGH] Scale test {i}",
                body_text="synthetic backlog entry",
            )
        await db.commit()

    sent_calls: list[dict] = []

    async def fake_sender(**kwargs):
        sent_calls.append(kwargs)

    ticks = 0
    max_ticks = math.ceil(backlog_size / 50) + 1  # +1 safety margin, never expected to be needed
    while ticks < max_ticks:
        async with SessionLocal() as db:
            service = NotificationService(
                NotificationSettingsRepository(db),
                NotificationEventRepository(db),
                MonitoredDatabaseRepository(db),
                IncidentRepository(db),
                LocalEncryptedSecretProvider(SecretRepository(db)),
                sender=fake_sender,
            )
            await service.process_pending()
            await db.commit()
        ticks += 1

        async with SessionLocal() as db:
            remaining = await NotificationEventRepository(db).list_pending_or_retrying(limit=200)
        if not remaining:
            break

    assert len(sent_calls) == backlog_size
    assert ticks == math.ceil(backlog_size / 50)

    async with SessionLocal() as db:
        from sqlalchemy import select

        from app.models.notification import NotificationEvent

        statuses = (await db.execute(select(NotificationEvent.status))).scalars().all()
    assert all(s == NOTIFICATION_STATUS_SENT for s in statuses)


async def test_integration_queue_drains_a_backlog_larger_than_one_batch(client, admin_headers):
    """Same bounded-batch-draining property as the notification queue, for the independent
    webhook/Slack/Teams delivery outbox added in Phase 23."""
    db_id = await _register_database(client, admin_headers, "INTEGSCALE01")
    backlog_size = 120

    async with SessionLocal() as db:
        integration_repo = IntegrationRepository(db)
        integration = await integration_repo.create(
            integration_type="WEBHOOK",
            name="Scale test webhook",
            target_url="https://1.1.1.1/hook",
            secret_ref=None,
            enabled=True,
            trigger_severities=["HIGH", "CRITICAL"],
            created_by_user_id=None,
        )
        incident_repo = IncidentRepository(db)
        delivery_repo = IntegrationDeliveryRepository(db)
        from app.models.base import utcnow

        now = utcnow()
        for i in range(backlog_size):
            incident = await incident_repo.create(
                monitored_database_id=db_id,
                severity="HIGH",
                title=f"Scale test incident {i}",
                opened_at=now,
                last_evidence_at=now,
            )
            await delivery_repo.create(
                integration_id=integration.id,
                incident_id=incident.id,
                event_type="incident.created",
                dedupe_key=f"scale-integ-{i}",
                payload={"text": f"synthetic backlog entry {i}"},
            )
        await db.commit()

    async def fake_sender(**kwargs):
        return WebhookSendResult(status_code=200, response_snippet="ok")

    ticks = 0
    max_ticks = math.ceil(backlog_size / 50) + 1
    while ticks < max_ticks:
        async with SessionLocal() as db:
            service = IntegrationService(
                IntegrationRepository(db),
                IntegrationDeliveryRepository(db),
                MonitoredDatabaseRepository(db),
                LocalEncryptedSecretProvider(SecretRepository(db)),
                AuditService(AuditRepository(db)),
                sender=fake_sender,
            )
            await service.process_pending()
            await db.commit()
        ticks += 1

        async with SessionLocal() as db:
            remaining = await IntegrationDeliveryRepository(db).list_pending_or_retrying(limit=200)
        if not remaining:
            break

    assert ticks == math.ceil(backlog_size / 50)

    async with SessionLocal() as db:
        from sqlalchemy import select

        from app.models.integration import IntegrationDelivery

        statuses = (await db.execute(select(IntegrationDelivery.status))).scalars().all()
    assert len(statuses) == backlog_size
    assert all(s == INTEGRATION_STATUS_SENT for s in statuses)


async def test_ai_ask_under_concurrent_load_enforces_the_rate_limit_without_crashing(client, admin_headers):
    """Fires more concurrent requests than the per-user AI rate limit (10/min, set in
    app/api/routes/ai.py) allows, from the same authenticated user. The correctness property
    under test is concurrency-safety of the Redis INCR-based limiter (app/services/
    rate_limit_service.py) - exactly `limit` requests must succeed and the rest must get a
    clean 429, with no request hanging, crashing, or double-counting a race."""
    fake_llm = FakeLLMClient(response="Concurrent load test answer.")
    app.dependency_overrides[get_copilot_llm_client] = lambda: fake_llm
    try:
        concurrent_requests = 15
        responses = await asyncio.gather(
            *[
                client.post(
                    "/api/ai/ask",
                    headers=admin_headers,
                    json={"question": f"Why is the fleet slow? (request {i})"},
                )
                for i in range(concurrent_requests)
            ]
        )
    finally:
        app.dependency_overrides.pop(get_copilot_llm_client, None)

    status_codes = [r.status_code for r in responses]
    assert status_codes.count(200) == 10
    assert status_codes.count(429) == concurrent_requests - 10
    assert set(status_codes) == {200, 429}
    for resp in responses:
        if resp.status_code == 429:
            assert "Retry-After" in resp.headers
