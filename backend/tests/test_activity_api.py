"""Real-DB integration tests for the Activity tab's two routes (unit/pure-logic coverage
lives in test_activity_service.py and test_activity_feed.py) - confirms the route wiring
itself (query params, repo fan-out, response shape) against real registered databases and
real inserted rows, same "register a throwaway database, insert real rows" technique
established by tests/test_investigation.py."""

from app.db import SessionLocal
from app.models.base import utcnow
from app.repositories.analytics_repo import AnomalyRepository
from app.repositories.capacity_repo import CapacityRepository

DB_PAYLOAD = {
    "name": "ACTIVITYAPI01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}


async def _register_database(client, admin_headers, *, name: str) -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json={**DB_PAYLOAD, "name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_fleet_pulse_endpoint_returns_a_row_per_active_database(client, admin_headers):
    db_id = await _register_database(client, admin_headers, name="ACTIVITYAPI-PULSE")

    resp = await client.get("/api/activity/fleet-pulse", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    row = next(r for r in rows if r["database_id"] == db_id)
    assert row["database_name"] == "ACTIVITYAPI-PULSE"
    assert row["overall_status"] in {"PASS", "WARN", "FAIL", "UNAVAILABLE"}


async def test_feed_endpoint_merges_real_anomalies_and_capacity_alerts(client, admin_headers):
    db_id = await _register_database(client, admin_headers, name="ACTIVITYAPI-FEED")

    async with SessionLocal() as db:
        await AnomalyRepository(db).create(
            monitored_database_id=db_id,
            metric_key="active_session_count",
            observed_value=500.0,
            baseline_mean=10.0,
            baseline_stddev=2.0,
            sample_count=25,
            severity="CRITICAL",
        )
        await CapacityRepository(db).create_alert(
            db_id,
            tablespace_name="DATA01",
            alert_kind="PCT_USED",
            severity="HIGH",
            status="OPEN",
            first_detected_at=utcnow(),
            last_detected_at=utcnow(),
            pct_used=95.0,
            projected_days_to_full=3.0,
            current_used_bytes=950,
            current_allocated_bytes=1000,
            current_max_bytes=1000,
            detection_note="95% used",
        )
        await db.commit()

    resp = await client.get(f"/api/activity/feed?database_ids={db_id}", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    events = resp.json()
    categories = {e["category"] for e in events}
    assert categories == {"anomaly", "capacity_alert"}
    assert all(e["database_id"] == db_id for e in events)
    assert all(e["database_name"] == "ACTIVITYAPI-FEED" for e in events)


async def test_feed_endpoint_severity_filter_applies_through_the_real_route(client, admin_headers):
    db_id = await _register_database(client, admin_headers, name="ACTIVITYAPI-SEVERITY")

    async with SessionLocal() as db:
        await AnomalyRepository(db).create(
            monitored_database_id=db_id,
            metric_key="active_session_count",
            observed_value=500.0,
            baseline_mean=10.0,
            baseline_stddev=2.0,
            sample_count=25,
            severity="LOW",
        )
        await db.commit()

    resp = await client.get(f"/api/activity/feed?database_ids={db_id}&severity=CRITICAL", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


async def test_feed_endpoint_requires_authentication(client):
    resp = await client.get("/api/activity/feed")
    assert resp.status_code == 401
