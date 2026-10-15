"""Activity tab Phase 1: the fleet vitals pulse strip. `_capacity_trend` is pure and
directly unit-tested with no DB; `fleet_vitals` is exercised against real registered
databases/telemetry (same "register a throwaway database, insert real rows" technique
established by tests/test_investigation.py and tests/test_rca_service.py)."""

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.capacity_telemetry import CapacityAlertEvent
from app.oracle.telemetry_probe import SessionSampleRow
from app.repositories.analytics_repo import AnomalyRepository
from app.repositories.capacity_repo import CapacityRepository
from app.repositories.telemetry_repo import TelemetryRepository
from app.services.activity_service import _capacity_trend, fleet_vitals
from app.services.incident_service import IncidentService
from app.repositories.incident_repo import IncidentRepository

DB_PAYLOAD = {
    "name": "ACTIVITY01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}


def _alert(severity: str) -> CapacityAlertEvent:
    return CapacityAlertEvent(
        monitored_database_id="db-1",
        tablespace_name="DATA01",
        alert_kind="PCT_USED",
        severity=severity,
        status="OPEN",
        first_detected_at=utcnow(),
        last_detected_at=utcnow(),
        pct_used=95.0,
        detection_note="test",
    )


# -- _capacity_trend (pure) ----------------------------------------------------------------


def test_capacity_trend_is_stable_with_no_open_alerts():
    assert _capacity_trend([]) == "stable"


def test_capacity_trend_is_warning_with_a_low_severity_open_alert():
    assert _capacity_trend([_alert("MEDIUM")]) == "warning"


def test_capacity_trend_is_critical_with_a_high_severity_open_alert():
    assert _capacity_trend([_alert("HIGH")]) == "critical"
    assert _capacity_trend([_alert("MEDIUM"), _alert("CRITICAL")]) == "critical"


# -- fleet_vitals (real DB) ----------------------------------------------------------------


async def _register_database(client, admin_headers, *, name: str) -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json={**DB_PAYLOAD, "name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_fleet_vitals_is_empty_with_no_active_databases(client, admin_headers):
    async with SessionLocal() as db:
        rows = await fleet_vitals(db, now=utcnow())
    assert rows == []


async def test_fleet_vitals_computes_a_real_row_with_blocking_and_an_open_incident(client, admin_headers):
    db_id = await _register_database(client, admin_headers, name="ACTIVITY-BLOCKING")

    async with SessionLocal() as db:
        rows = [
            SessionSampleRow(300, 1, "BATCH_JOB", None, None, None, "ACTIVE", None, "batch01", "batchproc", None, None),
            SessionSampleRow(
                301, 2, "APP_USER", "actvblksql01", "enq: TX - row lock contention", "Application",
                "ACTIVE", 300, "app01", "sqlplus", None, None,
            ),
        ]
        await TelemetryRepository(db).insert_session_samples(db_id, utcnow(), rows)

        anomaly = await AnomalyRepository(db).create(
            monitored_database_id=db_id,
            metric_key="active_session_count",
            observed_value=500.0,
            baseline_mean=10.0,
            baseline_stddev=2.0,
            sample_count=25,
            severity="HIGH",
        )
        await IncidentService(IncidentRepository(db)).correlate_anomaly(anomaly)
        await db.commit()

    async with SessionLocal() as db:
        rows = await fleet_vitals(db, now=utcnow())

    row = next(r for r in rows if r.database_id == db_id)
    assert row.database_name == "ACTIVITY-BLOCKING"
    assert row.blocking_present is True
    assert row.open_incident_count == 1
    assert row.overall_status in {"WARN", "FAIL"}  # a real blocking chain + HIGH anomaly, never a silent PASS


async def test_fleet_vitals_covers_multiple_databases_independently(client, admin_headers):
    quiet_id = await _register_database(client, admin_headers, name="ACTIVITY-QUIET")
    noisy_id = await _register_database(client, admin_headers, name="ACTIVITY-NOISY")

    async with SessionLocal() as db:
        await CapacityRepository(db).create_alert(
            noisy_id,
            tablespace_name="DATA01",
            alert_kind="PCT_USED",
            severity="CRITICAL",
            status="OPEN",
            first_detected_at=utcnow(),
            last_detected_at=utcnow(),
            pct_used=99.0,
            projected_days_to_full=1.0,
            current_used_bytes=990,
            current_allocated_bytes=1000,
            current_max_bytes=1000,
            detection_note="99% used",
        )
        await db.commit()

    async with SessionLocal() as db:
        rows = await fleet_vitals(db, now=utcnow())

    quiet_row = next(r for r in rows if r.database_id == quiet_id)
    noisy_row = next(r for r in rows if r.database_id == noisy_id)
    assert quiet_row.capacity_trend == "stable"
    assert quiet_row.open_incident_count == 0
    assert noisy_row.capacity_trend == "critical"
