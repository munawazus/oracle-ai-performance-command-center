"""Master Plan Phase 26 (DR/recovery): proves this platform's own failure/recovery
properties for the five scenarios the master plan names - DB (Oracle) unreachable,
collector restart, PostgreSQL restore, queue recovery, and notification provider outage.

No real Postgres/Redis/Oracle/SMTP outage can be induced in this sandboxed environment (the
same standing gap as every earlier phase - see docs/RUNBOOK.md's Known Gaps), so each test
proves the underlying property this platform's own code is responsible for - readiness
correctly reports and recovers from a dependency going away, a mid-batch crash cannot
duplicate work already done, a fleet-wide collector run isolates one database's failure from
the rest - using the same fake/monkeypatch test-double approach as every other phase.
"""

import asyncio

from app.collectors.scheduler import start_collector_scheduler, stop_collector_scheduler
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models.integration import STATUS_SENT as INTEGRATION_STATUS_SENT
from app.models.notification import STATUS_SENT as NOTIFICATION_STATUS_SENT
from app.models.telemetry import CollectorStatus, InstanceHealthSnapshot
from app.oracle.connector import OracleConnectError
from app.redis_client import get_redis
from app.repositories.audit_repo import AuditRepository
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.integration_repo import IntegrationDeliveryRepository, IntegrationRepository
from app.repositories.notification_repo import NotificationEventRepository, NotificationSettingsRepository
from app.repositories.secret_repo import SecretRepository
from app.services.audit_service import AuditService
from app.services.collector_service import CollectorService
from app.services.integration_service import IntegrationService
from app.services.notification_service import NotificationService
from app.services.secret_service import LocalEncryptedSecretProvider
from tests.test_collector import FakeConnection
from tests.test_integration_service import FakeWebhookSender
from tests.test_notification_service import FakeSender

# -- PostgreSQL / Redis readiness: report and recover from an outage -------------------


async def test_readiness_reports_and_recovers_from_a_postgres_outage(client, monkeypatch):
    import app.api.routes.health as health_module

    async def _broken_db_is_ready() -> bool:
        return False

    monkeypatch.setattr(health_module, "db_is_ready", _broken_db_is_ready)
    down = await client.get("/api/ready")
    assert down.status_code == 503
    body = down.json()
    assert body["status"] == "not_ready"
    assert body["dependencies"] == {"postgres": "unreachable", "redis": "ok"}

    monkeypatch.undo()  # "Postgres comes back" - no app restart required
    recovered = await client.get("/api/ready")
    assert recovered.status_code == 200
    assert recovered.json()["dependencies"] == {"postgres": "ok", "redis": "ok"}


async def test_readiness_reports_and_recovers_from_a_redis_outage(client):
    class BrokenRedis:
        async def ping(self):
            raise ConnectionError("simulated Redis outage")

    async def _broken_get_redis():
        return BrokenRedis()

    app.dependency_overrides[get_redis] = _broken_get_redis
    try:
        down = await client.get("/api/ready")
        assert down.status_code == 503
        assert down.json()["dependencies"] == {"postgres": "ok", "redis": "unreachable"}
    finally:
        app.dependency_overrides.pop(get_redis, None)  # "Redis comes back"

    recovered = await client.get("/api/ready")
    assert recovered.status_code == 200
    assert recovered.json()["dependencies"] == {"postgres": "ok", "redis": "ok"}


# -- Queue recovery: a mid-batch crash must never resend already-completed work --------


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


async def test_notification_queue_survives_a_mid_batch_crash_without_resending_sent_events(client, admin_headers):
    """Regression test for a real bug found while building this phase: process_pending()
    used to commit once after the whole batch, not after each event - a process killed
    between two events (a real SMTP send already happened for the first, but the commit
    recording that never landed) would resend the first event's email on the very next
    tick. Fixed by committing after every event; this proves it: a "crash" partway through
    a batch (simulated by raising after the 2nd of 3 events reaches the sender) still
    leaves those first 2 events durably SENT, so restarting process_pending() only ever
    resends the interrupted 3rd event, never repeats the first 2."""
    db_id = await _register_database(client, admin_headers, "DRNOTIFY01")

    async with SessionLocal() as db:
        await NotificationSettingsRepository(db).update(
            smtp_host="smtp.internal.ocpcc.dev",
            smtp_port=587,
            smtp_use_tls=True,
            sender_email="alerts@ocpcc.dev",
            sender_name="OCPCC Alerts",
            default_recipients=["oncall@ocpcc.dev"],
            escalation_recipients=[],
        )
        incident_repo = IncidentRepository(db)
        event_repo = NotificationEventRepository(db)
        from app.models.base import utcnow

        now = utcnow()
        for i in range(3):
            incident = await incident_repo.create(
                monitored_database_id=db_id, severity="HIGH", title=f"DR test incident {i}", opened_at=now, last_evidence_at=now
            )
            await event_repo.create(
                incident_id=incident.id,
                dedupe_key=f"dr-notify-{i}",
                severity="HIGH",
                recipients=["oncall@ocpcc.dev"],
                subject=f"[HIGH] DR test {i}",
                body_text="synthetic",
            )
        await db.commit()

    class CrashAfterNSends:
        """A sender that raises a process-fatal error (not caught anywhere, since real code
        being killed by the OS is not a Python exception at all) once it has already
        recorded N successful sends - simulating "the process died right after the Nth
        email actually went out over SMTP"."""

        def __init__(self, crash_after: int):
            self.crash_after = crash_after
            self.calls: list[dict] = []

        async def __call__(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) > self.crash_after:
                raise SystemExit("simulated process kill mid-batch")

    crashy_sender = CrashAfterNSends(crash_after=2)
    async with SessionLocal() as db:
        service = NotificationService(
            NotificationSettingsRepository(db),
            NotificationEventRepository(db),
            MonitoredDatabaseRepository(db),
            IncidentRepository(db),
            LocalEncryptedSecretProvider(SecretRepository(db)),
            sender=crashy_sender,
        )
        try:
            await service.process_pending()
        except SystemExit:
            pass  # the simulated crash - no `finally: await db.commit()` runs here either,
            # exactly like a real killed process never reaching its own commit

    assert len(crashy_sender.calls) == 3  # 2 sent, then the 3rd triggered the "crash"

    # "Restart": a brand-new session/service, as a fresh process would create.
    real_sender = FakeSender()
    async with SessionLocal() as db:
        service = NotificationService(
            NotificationSettingsRepository(db),
            NotificationEventRepository(db),
            MonitoredDatabaseRepository(db),
            IncidentRepository(db),
            LocalEncryptedSecretProvider(SecretRepository(db)),
            sender=real_sender,
        )
        await service.process_pending()
        await db.commit()

    # Only the interrupted 3rd event gets resent - the first 2 were already durably SENT
    # before the simulated crash, thanks to the per-item commit.
    assert len(real_sender.calls) == 1

    async with SessionLocal() as db:
        from sqlalchemy import select

        from app.models.notification import NotificationEvent

        events = (await db.execute(select(NotificationEvent).order_by(NotificationEvent.created_at))).scalars().all()
    assert [e.status for e in events] == [NOTIFICATION_STATUS_SENT] * 3
    # SystemExit isn't an Exception subclass, so _attempt_send's `except Exception` never
    # runs for the interrupted 3rd event either - exactly like a real process kill, which
    # runs no Python exception handler at all. Its attempt_count reflects only the one
    # real attempt after "restart", not a phantom failed attempt from the crash itself.
    assert [e.attempt_count for e in events] == [1, 1, 1]


async def test_integration_queue_survives_a_mid_batch_crash_without_resending_sent_deliveries(client, admin_headers):
    """Same property as the notification queue test above, for the independent Phase 23
    webhook/Slack/Teams outbox, which shares the identical per-batch-commit design (and
    therefore the identical bug, now identically fixed)."""
    db_id = await _register_database(client, admin_headers, "DRINTEG01")

    async with SessionLocal() as db:
        integration = await IntegrationRepository(db).create(
            integration_type="WEBHOOK",
            name="DR test webhook",
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
        for i in range(3):
            incident = await incident_repo.create(
                monitored_database_id=db_id, severity="HIGH", title=f"DR test incident {i}", opened_at=now, last_evidence_at=now
            )
            await delivery_repo.create(
                integration_id=integration.id,
                incident_id=incident.id,
                event_type="incident.created",
                dedupe_key=f"dr-integ-{i}",
                payload={"text": f"synthetic {i}"},
            )
        await db.commit()

    class CrashAfterNSends:
        def __init__(self, crash_after: int):
            self.crash_after = crash_after
            self.calls: list[dict] = []

        async def __call__(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) > self.crash_after:
                raise SystemExit("simulated process kill mid-batch")
            from app.integrations.webhook_sender import WebhookSendResult

            return WebhookSendResult(status_code=200, response_snippet="ok")

    crashy_sender = CrashAfterNSends(crash_after=2)
    async with SessionLocal() as db:
        service = IntegrationService(
            IntegrationRepository(db),
            IntegrationDeliveryRepository(db),
            MonitoredDatabaseRepository(db),
            LocalEncryptedSecretProvider(SecretRepository(db)),
            AuditService(AuditRepository(db)),
            sender=crashy_sender,
        )
        try:
            await service.process_pending()
        except SystemExit:
            pass

    assert len(crashy_sender.calls) == 3

    real_sender = FakeWebhookSender()
    async with SessionLocal() as db:
        service = IntegrationService(
            IntegrationRepository(db),
            IntegrationDeliveryRepository(db),
            MonitoredDatabaseRepository(db),
            LocalEncryptedSecretProvider(SecretRepository(db)),
            AuditService(AuditRepository(db)),
            sender=real_sender,
        )
        await service.process_pending()
        await db.commit()

    assert len(real_sender.calls) == 1

    async with SessionLocal() as db:
        from sqlalchemy import select

        from app.models.integration import IntegrationDelivery

        deliveries = (
            await db.execute(select(IntegrationDelivery).order_by(IntegrationDelivery.created_at))
        ).scalars().all()
    assert [d.status for d in deliveries] == [INTEGRATION_STATUS_SENT] * 3


# -- DB (Oracle) unreachable: automatic recovery once it becomes reachable again --------


async def test_collector_recovers_automatically_once_oracle_becomes_reachable_again(client, admin_headers):
    """No manual intervention (re-registering the database, restarting a job) should be
    required for the collector to resume once a previously-unreachable Oracle instance
    comes back - the next scheduled tick just needs to succeed on its own."""
    db_id = await _register_database(client, admin_headers, "DRORACLE01")

    call_count = {"n": 0}

    def flaky_then_healthy_connector(_params):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise OracleConnectError(OracleConnectError.CATEGORY_UNREACHABLE, "simulated outage")
        return FakeConnection()

    service = CollectorService(flaky_then_healthy_connector)

    await service.run_once_for_database(db_id)
    await service.run_once_for_database(db_id)

    async with SessionLocal() as db:
        from sqlalchemy import select

        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()
    assert status.consecutive_failures == 2
    assert status.last_success_at is None

    await service.run_once_for_database(db_id)  # Oracle is reachable again, no code changed

    async with SessionLocal() as db:
        from sqlalchemy import select

        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()
        health = (
            await db.execute(
                select(InstanceHealthSnapshot)
                .where(InstanceHealthSnapshot.monitored_database_id == db_id)
                .order_by(InstanceHealthSnapshot.sampled_at)
            )
        ).scalars().all()

    assert status.consecutive_failures == 0
    assert status.last_success_at is not None
    assert status.last_error_category is None
    assert [h.reachability for h in health] == ["unreachable", "unreachable", "up"]


async def test_fleet_collector_run_isolates_a_process_level_crash_in_one_database_from_the_rest(client, admin_headers):
    """Distinct from OracleConnectError handling (already covered elsewhere): here the
    unexpected failure is a bug/crash inside collection itself (e.g. a malformed row from
    Oracle), not a connection error - run_once_all_active must still isolate it exactly the
    same way, since each database's own collection cycle owns and commits its own DB
    session independently (per collector_service.py's own docstring)."""
    db1_id = await _register_database(client, admin_headers, "DRFLEET01")
    db2_id = await _register_database(client, admin_headers, "DRFLEET02")
    db3_id = await _register_database(client, admin_headers, "DRFLEET03")

    # Force the 2nd database (by name order, matching list_active_ids' ordering) to crash
    # deep inside sampling with an unexpected (non-Oracle) exception. collector_service.py
    # imports sample_sessions by name (`from ... import sample_sessions`), so the name must
    # be patched on that module, not on app.oracle.telemetry_probe where it's defined.
    from app.services import collector_service as collector_service_module

    original_sample_sessions = collector_service_module.sample_sessions

    def flaky_sample_sessions(conn):
        # db1 (call 1) and db3 (call 3) succeed, db2 (call 2) crashes.
        flaky_sample_sessions.calls += 1
        if flaky_sample_sessions.calls == 2:
            raise RuntimeError("simulated unexpected crash mid-sample")
        return original_sample_sessions(conn)

    flaky_sample_sessions.calls = 0
    collector_service_module.sample_sessions = flaky_sample_sessions
    try:
        service = CollectorService(lambda params: FakeConnection())
        await service.run_once_all_active()
    finally:
        collector_service_module.sample_sessions = original_sample_sessions

    async with SessionLocal() as db:
        from sqlalchemy import select

        statuses = {
            s.monitored_database_id: s
            for s in (await db.execute(select(CollectorStatus))).scalars().all()
        }

    assert statuses[db1_id].consecutive_failures == 0
    assert statuses[db1_id].last_success_at is not None
    assert statuses[db2_id].consecutive_failures == 1
    assert statuses[db2_id].last_error_category == "unknown_error"
    assert statuses[db3_id].consecutive_failures == 0
    assert statuses[db3_id].last_success_at is not None
    assert {db1_id, db2_id, db3_id} == set(statuses.keys())


# -- Collector restart: starting/stopping the scheduler repeatedly is safe --------------


async def test_collector_scheduler_start_stop_is_idempotent_and_safe_to_repeat(monkeypatch):
    """Simulates the platform process itself being restarted (container restart, deploy,
    crash-and-supervisor-relaunch): start -> stop -> start -> stop must never raise, never
    double-register a job, and never require any manual re-registration step - every tick
    re-queries "all active databases" fresh from Postgres (list_active_ids(), Phase 25's own
    fix), so no in-memory state is ever lost by a restart in the first place. COLLECTOR_ENABLED
    is forced on for just this test (the test suite otherwise runs with it off, per
    conftest.py, so no background collection job fires during unrelated tests)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "collector_enabled", True)

    scheduler1 = start_collector_scheduler()
    assert scheduler1 is not None
    assert scheduler1.running
    stop_collector_scheduler()
    await asyncio.sleep(0)  # AsyncIOScheduler.shutdown() is deferred via call_soon_threadsafe
    assert not scheduler1.running

    scheduler2 = start_collector_scheduler()
    assert scheduler2 is not None
    assert scheduler2.running
    assert scheduler2 is not scheduler1  # a real restart creates a fresh scheduler instance
    stop_collector_scheduler()
    await asyncio.sleep(0)
    assert not scheduler2.running
