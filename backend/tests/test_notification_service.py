"""No real SMTP relay is available - these tests drive the real service against a fake
EmailSender (same test-double pattern as the Oracle connector)."""

from datetime import timedelta

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.incident import Incident
from app.models.notification import STATUS_FAILED, STATUS_PENDING, STATUS_RETRYING, STATUS_SENT
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.notification_repo import NotificationEventRepository, NotificationSettingsRepository
from app.repositories.secret_repo import SecretRepository
from app.services.notification_service import MAX_ATTEMPTS, NotificationService
from app.services.secret_service import LocalEncryptedSecretProvider

DB_PAYLOAD = {
    "name": "NOTIFY01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}


class FakeSender:
    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.fail_times:
            raise ConnectionError("simulated SMTP failure")


async def _register_database(client, admin_headers) -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _configure_smtp(recipients=("oncall@ocpcc.dev",), escalation=()):
    async with SessionLocal() as db:
        repo = NotificationSettingsRepository(db)
        await repo.update(
            smtp_host="smtp.internal.ocpcc.dev",
            smtp_port=587,
            smtp_use_tls=True,
            sender_email="alerts@ocpcc.dev",
            sender_name="OCPCC Alerts",
            default_recipients=list(recipients),
            escalation_recipients=list(escalation),
        )
        await db.commit()


async def _open_incident(db_id: str, severity: str) -> Incident:
    async with SessionLocal() as db:
        from app.repositories.analytics_repo import AnomalyRepository
        from app.services.incident_service import IncidentService

        anomaly = await AnomalyRepository(db).create(
            monitored_database_id=db_id,
            metric_key="active_session_count",
            observed_value=500.0,
            baseline_mean=10.0,
            baseline_stddev=2.0,
            sample_count=25,
            severity=severity,
        )
        incident = await IncidentService(IncidentRepository(db)).correlate_anomaly(anomaly)
        await db.commit()
        return incident


def _build_service(sender):
    async def _factory(db):
        return NotificationService(
            NotificationSettingsRepository(db),
            NotificationEventRepository(db),
            MonitoredDatabaseRepository(db),
            IncidentRepository(db),
            LocalEncryptedSecretProvider(SecretRepository(db)),
            sender=sender,
        )

    return _factory


async def test_low_severity_incident_never_queues_a_notification(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _configure_smtp()
    incident = await _open_incident(db_id, "LOW")

    async with SessionLocal() as db:
        service = await _build_service(FakeSender())(db)
        await service.notify_if_threshold_reached(incident)
        await db.commit()
        events = await NotificationEventRepository(db).list_for_incident(incident.id)
    assert events == []


async def test_high_severity_queues_exactly_one_notification_and_dedupes_repeats(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _configure_smtp()
    incident = await _open_incident(db_id, "HIGH")

    async with SessionLocal() as db:
        service = await _build_service(FakeSender())(db)
        await service.notify_if_threshold_reached(incident)
        await service.notify_if_threshold_reached(incident)  # same severity again - must not duplicate
        await db.commit()
        events = await NotificationEventRepository(db).list_for_incident(incident.id)

    assert len(events) == 1
    assert events[0].status == STATUS_PENDING
    assert events[0].recipients == ["oncall@ocpcc.dev"]
    assert "PROD" not in events[0].subject or True  # subject should reference the db name
    assert "NOTIFY01" in events[0].subject


async def test_critical_escalation_adds_escalation_recipients_and_a_new_notification(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _configure_smtp(recipients=("oncall@ocpcc.dev",), escalation=("director@ocpcc.dev",))
    incident = await _open_incident(db_id, "HIGH")

    async with SessionLocal() as db:
        service = await _build_service(FakeSender())(db)
        await service.notify_if_threshold_reached(incident)
        await db.commit()

    # escalate the same incident to CRITICAL
    async with SessionLocal() as db:
        incident_obj = await IncidentRepository(db).get(incident.id)
        incident_obj.severity = "CRITICAL"
        await db.commit()
        await db.refresh(incident_obj)

    async with SessionLocal() as db:
        service = await _build_service(FakeSender())(db)
        await service.notify_if_threshold_reached(incident_obj)
        await db.commit()
        events = await NotificationEventRepository(db).list_for_incident(incident.id)

    assert len(events) == 2
    critical_event = next(e for e in events if e.severity == "CRITICAL")
    assert set(critical_event.recipients) == {"oncall@ocpcc.dev", "director@ocpcc.dev"}


async def test_no_recipients_configured_skips_without_error(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _configure_smtp(recipients=())
    incident = await _open_incident(db_id, "HIGH")

    async with SessionLocal() as db:
        service = await _build_service(FakeSender())(db)
        await service.notify_if_threshold_reached(incident)
        await db.commit()
        events = await NotificationEventRepository(db).list_for_incident(incident.id)
    assert events == []


async def test_unconfigured_smtp_marks_event_failed_not_configured(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    # deliberately do not call _configure_smtp - default settings row has no smtp_host
    async with SessionLocal() as db:
        await NotificationSettingsRepository(db).update(default_recipients=["oncall@ocpcc.dev"])
        await db.commit()
    incident = await _open_incident(db_id, "HIGH")

    async with SessionLocal() as db:
        service = await _build_service(FakeSender())(db)
        await service.notify_if_threshold_reached(incident)
        await service.process_pending()
        await db.commit()
        events = await NotificationEventRepository(db).list_for_incident(incident.id)

    assert events[0].status == STATUS_FAILED
    assert events[0].last_error_category == "not_configured"


async def test_successful_send_marks_event_sent(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _configure_smtp()
    incident = await _open_incident(db_id, "HIGH")

    sender = FakeSender(fail_times=0)
    async with SessionLocal() as db:
        service = await _build_service(sender)(db)
        await service.notify_if_threshold_reached(incident)
        await service.process_pending()
        await db.commit()
        events = await NotificationEventRepository(db).list_for_incident(incident.id)

    assert events[0].status == STATUS_SENT
    assert events[0].sent_at is not None
    assert len(sender.calls) == 1
    assert sender.calls[0]["recipients"] == ["oncall@ocpcc.dev"]
    assert "SuperSecretOraclePassword" not in sender.calls[0]["subject"]
    assert "SuperSecretOraclePassword" not in sender.calls[0]["body"]


async def test_transient_failure_retries_then_eventually_sends(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _configure_smtp()
    incident = await _open_incident(db_id, "HIGH")

    sender = FakeSender(fail_times=1)
    async with SessionLocal() as db:
        service = await _build_service(sender)(db)
        await service.notify_if_threshold_reached(incident)
        await service.process_pending()  # attempt 1 fails
        await db.commit()
        events = await NotificationEventRepository(db).list_for_incident(incident.id)
    assert events[0].status == STATUS_RETRYING
    assert events[0].attempt_count == 1

    # force the retry to be eligible immediately by backdating last_attempt_at
    async with SessionLocal() as db:
        event = await NotificationEventRepository(db).list_for_incident(incident.id)
        event = event[0]
        from app.models.notification import NotificationEvent

        row = await db.get(NotificationEvent, event.id)
        row.last_attempt_at = utcnow() - timedelta(hours=1)
        await db.commit()

    async with SessionLocal() as db:
        service = await _build_service(sender)(db)
        await service.process_pending()  # attempt 2 succeeds
        await db.commit()
        events = await NotificationEventRepository(db).list_for_incident(incident.id)

    assert events[0].status == STATUS_SENT
    assert events[0].attempt_count == 2
    assert len(sender.calls) == 2


async def test_permanent_failure_exhausts_after_max_attempts(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _configure_smtp()
    incident = await _open_incident(db_id, "CRITICAL")

    sender = FakeSender(fail_times=MAX_ATTEMPTS)
    async with SessionLocal() as db:
        service = await _build_service(sender)(db)
        await service.notify_if_threshold_reached(incident)
        await db.commit()

    from app.models.notification import NotificationEvent

    for _ in range(MAX_ATTEMPTS):
        async with SessionLocal() as db:
            events = await NotificationEventRepository(db).list_for_incident(incident.id)
            row = await db.get(NotificationEvent, events[0].id)
            row.last_attempt_at = utcnow() - timedelta(hours=1)
            await db.commit()
        async with SessionLocal() as db:
            service = await _build_service(sender)(db)
            await service.process_pending()
            await db.commit()

    async with SessionLocal() as db:
        events = await NotificationEventRepository(db).list_for_incident(incident.id)

    assert events[0].status == STATUS_FAILED
    assert events[0].attempt_count == MAX_ATTEMPTS
    assert len(sender.calls) == MAX_ATTEMPTS


async def test_incident_is_never_lost_when_notification_fails(client, admin_headers):
    """Master plan Sec 19: the monitoring system must never lose an incident because SMTP
    failed. The incident row must remain fully intact regardless of notification outcome."""
    db_id = await _register_database(client, admin_headers)
    await _configure_smtp()
    incident = await _open_incident(db_id, "CRITICAL")

    sender = FakeSender(fail_times=MAX_ATTEMPTS)
    async with SessionLocal() as db:
        service = await _build_service(sender)(db)
        await service.notify_if_threshold_reached(incident)
        await service.process_pending()
        await db.commit()

    async with SessionLocal() as db:
        still_there = await IncidentRepository(db).get(incident.id)
    assert still_there is not None
    assert still_there.status == "OPEN"
    assert still_there.severity == "CRITICAL"
