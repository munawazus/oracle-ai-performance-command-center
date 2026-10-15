from datetime import datetime, timezone

from app.db import SessionLocal
from app.repositories.analytics_repo import AnomalyRepository
from app.repositories.incident_repo import IncidentRepository
from app.services.incident_service import IncidentService

DB_PAYLOAD = {
    "name": "INCIDENTAPI01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}


async def _register_database(client, admin_headers) -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _open_incident(db_id: str) -> str:
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
        anomaly.detected_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        incident = await IncidentService(IncidentRepository(db)).correlate_anomaly(anomaly)
        await db.commit()
        return incident.id


async def _viewer_headers(client, admin_headers, email="incidentviewer@ocpcc.dev"):
    await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"email": email, "password": "ViewerPassw0rd!123", "display_name": "Viewer", "roles": ["VIEWER"]},
    )
    login = await client.post("/api/auth/login", json={"email": email, "password": "ViewerPassw0rd!123"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def test_list_and_get_incident(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    incident_id = await _open_incident(db_id)

    list_resp = await client.get("/api/incidents", headers=admin_headers)
    assert list_resp.status_code == 200
    assert any(i["id"] == incident_id for i in list_resp.json())

    filtered = await client.get(f"/api/incidents?monitored_database_id={db_id}", headers=admin_headers)
    assert all(i["monitored_database_id"] == db_id for i in filtered.json())

    get_resp = await client.get(f"/api/incidents/{incident_id}", headers=admin_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["severity"] == "HIGH"
    assert get_resp.json()["status"] == "OPEN"


async def test_get_missing_incident_404s(client, admin_headers):
    resp = await client.get("/api/incidents/does-not-exist", headers=admin_headers)
    assert resp.status_code == 404


async def test_timeline_shows_opened_event(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    incident_id = await _open_incident(db_id)

    resp = await client.get(f"/api/incidents/{incident_id}/timeline", headers=admin_headers)
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "opened"


async def test_viewer_can_view_but_not_manage_incidents(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    incident_id = await _open_incident(db_id)
    viewer_headers = await _viewer_headers(client, admin_headers)

    view_resp = await client.get(f"/api/incidents/{incident_id}", headers=viewer_headers)
    assert view_resp.status_code == 200

    ack_resp = await client.post(f"/api/incidents/{incident_id}/acknowledge", headers=viewer_headers)
    assert ack_resp.status_code == 403


async def test_dba_can_acknowledge_and_resolve_via_api(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    incident_id = await _open_incident(db_id)

    ack = await client.post(f"/api/incidents/{incident_id}/acknowledge", headers=admin_headers)
    assert ack.status_code == 200
    assert ack.json()["status"] == "ACKNOWLEDGED"

    resolve = await client.post(
        f"/api/incidents/{incident_id}/resolve", headers=admin_headers, json={"note": "Resolved via test"}
    )
    assert resolve.status_code == 200
    assert resolve.json()["status"] == "RESOLVED"
    assert resolve.json()["resolution_note"] == "Resolved via test"


async def test_assign_rejects_unknown_user(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    incident_id = await _open_incident(db_id)

    resp = await client.post(
        f"/api/incidents/{incident_id}/assign", headers=admin_headers, json={"assignee_user_id": "no-such-user"}
    )
    assert resp.status_code == 422


async def test_unauthenticated_requests_are_rejected(client):
    resp = await client.get("/api/incidents")
    assert resp.status_code == 401


async def test_assignable_users_route_is_not_shadowed_by_incident_id_route(client, admin_headers):
    # Regression check: /assignable-users must not be parsed as {incident_id}="assignable-users"
    # by the route registered above it.
    resp = await client.get("/api/incidents/assignable-users", headers=admin_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert any(u["email"] == "admin@bootstrap.dev" for u in resp.json())


async def test_assignable_users_requires_incident_manage(client, admin_headers):
    viewer_headers = await _viewer_headers(client, admin_headers, email="cantassign@ocpcc.dev")
    resp = await client.get("/api/incidents/assignable-users", headers=viewer_headers)
    assert resp.status_code == 403
