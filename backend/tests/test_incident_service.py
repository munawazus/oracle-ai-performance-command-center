from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models.incident import STATUS_ACKNOWLEDGED, STATUS_OPEN, STATUS_RESOLVED
from app.repositories.analytics_repo import AnomalyRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.incident_repo import IncidentRepository
from app.services.audit_service import AuditService
from app.oracle.connector import OracleConnectError
from app.services.incident_service import CORRELATION_WINDOW, IncidentService, humanize_metric_key, humanize_reachability_category

DB_PAYLOAD = {
    "name": "INCIDENT01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}

BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


async def _register_database(client, admin_headers) -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_anomaly(db_id: str, *, detected_at, severity="LOW", metric_key="active_session_count"):
    async with SessionLocal() as db:
        repo = AnomalyRepository(db)
        anomaly = await repo.create(
            monitored_database_id=db_id,
            metric_key=metric_key,
            observed_value=999.0,
            baseline_mean=10.0,
            baseline_stddev=2.0,
            sample_count=25,
            severity=severity,
        )
        anomaly.detected_at = detected_at
        await db.commit()
        await db.refresh(anomaly)
        return anomaly


def test_humanize_metric_key():
    assert humanize_metric_key("active_session_count") == "Active session count"
    assert humanize_metric_key("wait_rate_centisec_per_sec:User I/O") == "User I/O wait time"
    assert humanize_metric_key("something_unrecognized") == "something_unrecognized"


def test_humanize_reachability_category():
    assert humanize_reachability_category(OracleConnectError.CATEGORY_UNREACHABLE) == "Database unreachable"
    assert humanize_reachability_category(OracleConnectError.CATEGORY_AUTH_FAILED) == "Database authentication failing"
    assert humanize_reachability_category("something_unrecognized") == "Database connection failing"


async def test_reachability_incident_opens_at_critical_severity_with_no_anomaly(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db))
        incident = await service.open_or_attach_reachability_incident(
            db_id, category=OracleConnectError.CATEGORY_UNREACHABLE, detected_at=BASE_TIME
        )
        await db.commit()

    assert incident.severity == "CRITICAL"
    assert incident.title == "Database unreachable"

    async with SessionLocal() as db:
        events = await IncidentRepository(db).list_events(incident.id)
    assert [e.event_type for e in events] == ["opened"]


async def test_reachability_incidents_within_window_attach_to_the_same_incident(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db))
        incident1 = await service.open_or_attach_reachability_incident(
            db_id, category=OracleConnectError.CATEGORY_UNREACHABLE, detected_at=BASE_TIME
        )
        await db.commit()

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db))
        incident2 = await service.open_or_attach_reachability_incident(
            db_id, category=OracleConnectError.CATEGORY_UNREACHABLE, detected_at=BASE_TIME + timedelta(minutes=5)
        )
        await db.commit()

    assert incident1.id == incident2.id
    async with SessionLocal() as db:
        events = await IncidentRepository(db).list_events(incident1.id)
    assert [e.event_type for e in events] == ["opened", "evidence_added"]


async def test_reachability_incident_outside_window_opens_a_new_one(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db))
        incident1 = await service.open_or_attach_reachability_incident(
            db_id, category=OracleConnectError.CATEGORY_UNREACHABLE, detected_at=BASE_TIME
        )
        await db.commit()

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db))
        incident2 = await service.open_or_attach_reachability_incident(
            db_id,
            category=OracleConnectError.CATEGORY_UNREACHABLE,
            detected_at=BASE_TIME + CORRELATION_WINDOW + timedelta(minutes=1),
        )
        await db.commit()

    assert incident1.id != incident2.id


async def test_two_anomalies_within_window_correlate_to_one_incident(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db))
        a1 = await _make_anomaly(db_id, detected_at=BASE_TIME, severity="LOW")
        incident1 = await service.correlate_anomaly(a1)
        await db.commit()

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db))
        a2 = await _make_anomaly(db_id, detected_at=BASE_TIME + timedelta(minutes=5), severity="LOW")
        incident2 = await service.correlate_anomaly(a2)
        await db.commit()

    assert incident1.id == incident2.id

    async with SessionLocal() as db:
        events = await IncidentRepository(db).list_events(incident1.id)
    event_types = [e.event_type for e in events]
    assert event_types == ["opened", "evidence_added"]


async def test_anomaly_outside_window_opens_a_new_incident(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db))
        a1 = await _make_anomaly(db_id, detected_at=BASE_TIME)
        incident1 = await service.correlate_anomaly(a1)
        await db.commit()

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db))
        a2 = await _make_anomaly(db_id, detected_at=BASE_TIME + CORRELATION_WINDOW + timedelta(minutes=1))
        incident2 = await service.correlate_anomaly(a2)
        await db.commit()

    assert incident1.id != incident2.id


async def test_higher_severity_evidence_escalates_the_incident(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db))
        a1 = await _make_anomaly(db_id, detected_at=BASE_TIME, severity="LOW")
        incident = await service.correlate_anomaly(a1)
        await db.commit()
    assert incident.severity == "LOW"

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db))
        a2 = await _make_anomaly(db_id, detected_at=BASE_TIME + timedelta(minutes=1), severity="CRITICAL")
        incident = await service.correlate_anomaly(a2)
        await db.commit()
    assert incident.severity == "CRITICAL"

    # a subsequent LOW anomaly must not de-escalate the incident back down
    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db))
        a3 = await _make_anomaly(db_id, detected_at=BASE_TIME + timedelta(minutes=2), severity="LOW")
        incident = await service.correlate_anomaly(a3)
        await db.commit()
    assert incident.severity == "CRITICAL"


async def test_resolved_incident_is_not_reopened_by_a_later_anomaly(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    me = await client.get("/api/auth/me", headers=admin_headers)
    admin_id = me.json()["id"]

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db))
        a1 = await _make_anomaly(db_id, detected_at=BASE_TIME)
        incident = await service.correlate_anomaly(a1)
        await IncidentRepository(db).resolve(incident.id, admin_id, "handled")
        await db.commit()

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db))
        a2 = await _make_anomaly(db_id, detected_at=BASE_TIME + timedelta(minutes=2))
        incident2 = await service.correlate_anomaly(a2)
        await db.commit()

    assert incident2.id != incident.id
    assert incident2.status == STATUS_OPEN


async def test_acknowledge_assign_resolve_lifecycle_and_audit_trail(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    async with SessionLocal() as db:
        raw_service = IncidentService(IncidentRepository(db))
        a1 = await _make_anomaly(db_id, detected_at=BASE_TIME)
        incident = await raw_service.correlate_anomaly(a1)
        await db.commit()
        incident_id = incident.id

    me = await client.get("/api/auth/me", headers=admin_headers)
    admin_id = me.json()["id"]

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db), AuditService(AuditRepository(db)))
        acked = await service.acknowledge(incident_id, actor_user_id=admin_id, ip_address="127.0.0.1")
        await db.commit()
    assert acked.status == STATUS_ACKNOWLEDGED
    assert acked.acknowledged_by_user_id == admin_id

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db), AuditService(AuditRepository(db)))
        assigned = await service.assign(incident_id, admin_id, actor_user_id=admin_id, ip_address="127.0.0.1")
        await db.commit()
    assert assigned.assigned_to_user_id == admin_id

    async with SessionLocal() as db:
        service = IncidentService(IncidentRepository(db), AuditService(AuditRepository(db)))
        resolved = await service.resolve(incident_id, actor_user_id=admin_id, note="Fixed it", ip_address="127.0.0.1")
        await db.commit()
    assert resolved.status == STATUS_RESOLVED
    assert resolved.resolution_note == "Fixed it"

    audit = await client.get("/api/audit?action_prefix=incident.", headers=admin_headers)
    actions = {e["action"] for e in audit.json()}
    assert {"incident.acknowledged", "incident.assigned", "incident.resolved"}.issubset(actions)
