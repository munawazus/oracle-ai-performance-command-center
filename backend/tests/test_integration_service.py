"""No real webhook endpoint is available - these tests drive the real service against a fake
WebhookSender (same test-double pattern as the Oracle connector / EmailSender)."""

from datetime import timedelta

from app.db import SessionLocal
from app.integrations.webhook_sender import WebhookSendError, WebhookSendResult
from app.models.base import utcnow
from app.models.incident import Incident
from app.models.integration import STATUS_FAILED, STATUS_PENDING, STATUS_RETRYING, STATUS_SENT
from app.repositories.analytics_repo import AnomalyRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.integration_repo import IntegrationDeliveryRepository, IntegrationRepository
from app.repositories.secret_repo import SecretRepository
from app.services.audit_service import AuditService
from app.services.incident_service import IncidentService
from app.services.integration_service import MAX_ATTEMPTS, IntegrationService
from app.services.secret_service import LocalEncryptedSecretProvider

DB_PAYLOAD = {
    "name": "INTEGSVC01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}


class FakeWebhookSender:
    def __init__(self, fail_times: int = 0, status_code: int = 200):
        self.fail_times = fail_times
        self.status_code = status_code
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.fail_times:
            raise WebhookSendError("network_error", "simulated failure")
        return WebhookSendResult(status_code=self.status_code, response_snippet="ok")


async def _register_database(client, admin_headers) -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_integration(*, integration_type="WEBHOOK", trigger_severities=("HIGH", "CRITICAL")) -> str:
    async with SessionLocal() as db:
        integration = await IntegrationRepository(db).create(
            integration_type=integration_type,
            name="Test Integration",
            target_url="https://example.invalid/hook",
            secret_ref=None,
            enabled=True,
            trigger_severities=list(trigger_severities),
            created_by_user_id=None,
        )
        await db.commit()
        return integration.id


async def _open_incident(db_id: str, severity: str) -> Incident:
    async with SessionLocal() as db:
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
        return IntegrationService(
            IntegrationRepository(db),
            IntegrationDeliveryRepository(db),
            MonitoredDatabaseRepository(db),
            LocalEncryptedSecretProvider(SecretRepository(db)),
            AuditService(AuditRepository(db)),
            sender=sender,
        )

    return _factory


async def test_low_severity_incident_never_queues_a_delivery(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _create_integration()
    incident = await _open_incident(db_id, "LOW")

    async with SessionLocal() as db:
        service = await _build_service(FakeWebhookSender())(db)
        await service.notify_if_threshold_reached(incident)
        deliveries = await IntegrationDeliveryRepository(db).list_for_incident(incident.id)
    assert deliveries == []


async def test_high_severity_queues_exactly_one_delivery_and_dedupes_repeats(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _create_integration()
    incident = await _open_incident(db_id, "HIGH")

    async with SessionLocal() as db:
        service = await _build_service(FakeWebhookSender())(db)
        await service.notify_if_threshold_reached(incident)
        await service.notify_if_threshold_reached(incident)
        deliveries = await IntegrationDeliveryRepository(db).list_for_incident(incident.id)
    assert len(deliveries) == 1
    assert deliveries[0].status == STATUS_PENDING
    assert deliveries[0].payload["incident"]["id"] == incident.id


async def test_no_enabled_integrations_queues_nothing(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    incident = await _open_incident(db_id, "HIGH")

    async with SessionLocal() as db:
        service = await _build_service(FakeWebhookSender())(db)
        await service.notify_if_threshold_reached(incident)
        deliveries = await IntegrationDeliveryRepository(db).list_for_incident(incident.id)
    assert deliveries == []


async def test_disabled_integration_is_skipped(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    integration_id = await _create_integration()
    async with SessionLocal() as db:
        integration = await IntegrationRepository(db).get(integration_id)
        await IntegrationRepository(db).update(integration, enabled=False)
        await db.commit()
    incident = await _open_incident(db_id, "HIGH")

    async with SessionLocal() as db:
        service = await _build_service(FakeWebhookSender())(db)
        await service.notify_if_threshold_reached(incident)
        deliveries = await IntegrationDeliveryRepository(db).list_for_incident(incident.id)
    assert deliveries == []


async def test_integration_only_fires_for_its_configured_severities(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _create_integration(trigger_severities=("CRITICAL",))
    incident = await _open_incident(db_id, "HIGH")

    async with SessionLocal() as db:
        service = await _build_service(FakeWebhookSender())(db)
        await service.notify_if_threshold_reached(incident)
        deliveries = await IntegrationDeliveryRepository(db).list_for_incident(incident.id)
    assert deliveries == []


async def test_successful_send_marks_sent_and_records_response(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _create_integration()
    incident = await _open_incident(db_id, "HIGH")
    sender = FakeWebhookSender(status_code=204)

    async with SessionLocal() as db:
        service = await _build_service(sender)(db)
        await service.notify_if_threshold_reached(incident)
        await service.process_pending()
        deliveries = await IntegrationDeliveryRepository(db).list_for_incident(incident.id)

    assert len(sender.calls) == 1
    assert deliveries[0].status == STATUS_SENT
    assert deliveries[0].response_status_code == 204
    assert deliveries[0].sent_at is not None


async def test_transient_failure_retries_then_eventually_sends(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _create_integration()
    incident = await _open_incident(db_id, "HIGH")
    sender = FakeWebhookSender(fail_times=2)

    async with SessionLocal() as db:
        service = await _build_service(sender)(db)
        await service.notify_if_threshold_reached(incident)
        await service.process_pending()
        deliveries = await IntegrationDeliveryRepository(db).list_for_incident(incident.id)
        assert deliveries[0].status == STATUS_RETRYING
        assert deliveries[0].attempt_count == 1

        # Backdate to bypass real backoff waiting, same trick as test_notification_service.py.
        delivery = deliveries[0]
        delivery.last_attempt_at = utcnow() - timedelta(hours=1)
        await db.commit()

    async with SessionLocal() as db:
        service = await _build_service(sender)(db)
        await service.process_pending()
        deliveries = await IntegrationDeliveryRepository(db).list_for_incident(incident.id)
        assert deliveries[0].status == STATUS_RETRYING
        assert deliveries[0].attempt_count == 2

        delivery = deliveries[0]
        delivery.last_attempt_at = utcnow() - timedelta(hours=1)
        await db.commit()

    async with SessionLocal() as db:
        service = await _build_service(sender)(db)
        await service.process_pending()
        deliveries = await IntegrationDeliveryRepository(db).list_for_incident(incident.id)

    assert deliveries[0].status == STATUS_SENT
    assert len(sender.calls) == 3


async def test_exhausted_after_max_attempts_marks_failed(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _create_integration()
    incident = await _open_incident(db_id, "HIGH")
    sender = FakeWebhookSender(fail_times=MAX_ATTEMPTS + 5)

    async with SessionLocal() as db:
        service = await _build_service(sender)(db)
        await service.notify_if_threshold_reached(incident)
        await db.commit()

    for _ in range(MAX_ATTEMPTS):
        async with SessionLocal() as db:
            service = await _build_service(sender)(db)
            await service.process_pending()
            deliveries = await IntegrationDeliveryRepository(db).list_for_incident(incident.id)
            delivery = deliveries[0]
            delivery.last_attempt_at = utcnow() - timedelta(hours=1)
            await db.commit()

    async with SessionLocal() as db:
        deliveries = await IntegrationDeliveryRepository(db).list_for_incident(incident.id)
    assert deliveries[0].status == STATUS_FAILED
    assert deliveries[0].attempt_count == MAX_ATTEMPTS


async def test_incident_survives_integration_delivery_failure(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _create_integration()
    incident = await _open_incident(db_id, "CRITICAL")
    sender = FakeWebhookSender(fail_times=99)

    async with SessionLocal() as db:
        service = await _build_service(sender)(db)
        await service.notify_if_threshold_reached(incident)
        await service.process_pending()

    async with SessionLocal() as db:
        stored = await IncidentRepository(db).get(incident.id)
    assert stored is not None
    assert stored.status == "OPEN"


async def test_critical_and_high_produce_distinct_deliveries_on_escalation(client, admin_headers):
    """Same dedup-by-severity reasoning as email: a CRITICAL escalation still fires its own
    delivery even though HIGH already fired one, since the dedupe key includes severity."""
    db_id = await _register_database(client, admin_headers)
    await _create_integration()

    async with SessionLocal() as db:
        anomaly = await AnomalyRepository(db).create(
            monitored_database_id=db_id,
            metric_key="active_session_count",
            observed_value=500.0,
            baseline_mean=10.0,
            baseline_stddev=2.0,
            sample_count=25,
            severity="HIGH",
        )
        incident = await IncidentService(IncidentRepository(db)).correlate_anomaly(anomaly)
        await db.commit()
        incident_id = incident.id

    async with SessionLocal() as db:
        service = await _build_service(FakeWebhookSender())(db)
        await service.notify_if_threshold_reached(await IncidentRepository(db).get(incident_id))
        await db.commit()

    async with SessionLocal() as db:
        anomaly2 = await AnomalyRepository(db).create(
            monitored_database_id=db_id,
            metric_key="active_session_count",
            observed_value=900.0,
            baseline_mean=10.0,
            baseline_stddev=2.0,
            sample_count=30,
            severity="CRITICAL",
        )
        escalated = await IncidentService(IncidentRepository(db)).correlate_anomaly(anomaly2)
        assert escalated.id == incident_id
        assert escalated.severity == "CRITICAL"
        await db.commit()

    async with SessionLocal() as db:
        service = await _build_service(FakeWebhookSender())(db)
        await service.notify_if_threshold_reached(await IncidentRepository(db).get(incident_id))
        deliveries = await IntegrationDeliveryRepository(db).list_for_incident(incident_id)

    assert len(deliveries) == 2
