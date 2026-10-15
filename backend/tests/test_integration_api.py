from app.db import SessionLocal
from app.repositories.analytics_repo import AnomalyRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.integration_repo import IntegrationDeliveryRepository, IntegrationRepository
from app.repositories.secret_repo import SecretRepository
from app.services.audit_service import AuditService
from app.services.incident_service import IncidentService
from app.services.integration_service import IntegrationService
from app.services.secret_service import LocalEncryptedSecretProvider
from tests.test_integration_service import FakeWebhookSender

DB_PAYLOAD = {
    "name": "INTEGAPI01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}

INTEGRATION_PAYLOAD = {
    "type": "SLACK",
    "name": "Ops Slack",
    # A literal public IP, not a hostname - the SSRF guard's DNS lookup is a no-op for a
    # numeric address (resolved locally, no network I/O), so this stays fast and offline-safe
    # in tests while still exercising the exact same code path as a real hostname would.
    "target_url": "https://1.1.1.1/services/x/y/z",
    "secret": "super-secret-signing-key",
    "enabled": True,
    "trigger_severities": ["HIGH", "CRITICAL"],
}


async def _register_database(client, admin_headers) -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_integration(client, admin_headers, **overrides) -> dict:
    payload = {**INTEGRATION_PAYLOAD, **overrides}
    resp = await client.post("/api/integrations", headers=admin_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _viewer_headers(client, admin_headers, email="integviewer@ocpcc.dev"):
    await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"email": email, "password": "ViewerPassw0rd!123", "display_name": "Viewer", "roles": ["VIEWER"]},
    )
    login = await client.post("/api/auth/login", json={"email": email, "password": "ViewerPassw0rd!123"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _open_incident(db_id: str, severity="HIGH") -> str:
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
        return incident.id


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def test_create_and_list_integration(client, admin_headers):
    created = await _create_integration(client, admin_headers)
    assert created["has_secret"] is True
    assert "secret" not in created

    list_resp = await client.get("/api/integrations", headers=admin_headers)
    assert any(i["id"] == created["id"] for i in list_resp.json())


async def test_create_integration_never_leaks_secret_in_response(client, admin_headers):
    created = await _create_integration(client, admin_headers)
    assert INTEGRATION_PAYLOAD["secret"] not in str(created)


async def test_create_integration_rejects_invalid_type(client, admin_headers):
    resp = await client.post("/api/integrations", headers=admin_headers, json={**INTEGRATION_PAYLOAD, "type": "PAGERDUTY"})
    assert resp.status_code == 422


async def test_create_integration_rejects_non_http_url(client, admin_headers):
    resp = await client.post(
        "/api/integrations", headers=admin_headers, json={**INTEGRATION_PAYLOAD, "target_url": "file:///etc/passwd"}
    )
    assert resp.status_code == 422


async def test_create_integration_rejects_invalid_severity(client, admin_headers):
    resp = await client.post(
        "/api/integrations", headers=admin_headers, json={**INTEGRATION_PAYLOAD, "trigger_severities": ["URGENT"]}
    )
    assert resp.status_code == 422


async def test_create_integration_rejects_localhost_hostname(client, admin_headers):
    """The localhost fast path - blocked by hostname before any DNS lookup runs."""
    resp = await client.post(
        "/api/integrations", headers=admin_headers, json={**INTEGRATION_PAYLOAD, "target_url": "http://localhost:9000/hook"}
    )
    assert resp.status_code == 422
    assert "internal" in resp.json()["detail"].lower()


async def test_create_integration_rejects_private_ip_literal(client, admin_headers):
    """A literal private-range IP resolves locally (no DNS), so this exercises the SSRF
    guard's ipaddress check without any network dependency."""
    resp = await client.post(
        "/api/integrations", headers=admin_headers, json={**INTEGRATION_PAYLOAD, "target_url": "http://10.0.0.5/hook"}
    )
    assert resp.status_code == 422
    assert "private" in resp.json()["detail"].lower() or "reserved" in resp.json()["detail"].lower()


async def test_create_integration_rejects_loopback_ip_literal(client, admin_headers):
    resp = await client.post(
        "/api/integrations", headers=admin_headers, json={**INTEGRATION_PAYLOAD, "target_url": "http://127.0.0.1:8080/hook"}
    )
    assert resp.status_code == 422


async def test_create_integration_rejects_cloud_metadata_link_local_ip(client, admin_headers):
    """169.254.169.254 - the AWS/GCP/Azure instance-metadata address - is link-local and
    caught by the same is_link_local check as any other 169.254.0.0/16 address."""
    resp = await client.post(
        "/api/integrations", headers=admin_headers, json={**INTEGRATION_PAYLOAD, "target_url": "http://169.254.169.254/latest/meta-data/"}
    )
    assert resp.status_code == 422


async def test_update_integration_target_url_is_also_ssrf_checked(client, admin_headers):
    created = await _create_integration(client, admin_headers)
    resp = await client.patch(
        f"/api/integrations/{created['id']}", headers=admin_headers, json={"target_url": "http://192.168.1.1/hook"}
    )
    assert resp.status_code == 422


async def test_update_integration_can_disable_and_rotate_secret(client, admin_headers):
    created = await _create_integration(client, admin_headers)
    resp = await client.patch(
        f"/api/integrations/{created['id']}", headers=admin_headers, json={"enabled": False, "secret": "new-secret"}
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert resp.json()["has_secret"] is True
    assert "new-secret" not in str(resp.json())


async def test_delete_integration(client, admin_headers):
    created = await _create_integration(client, admin_headers)
    resp = await client.delete(f"/api/integrations/{created['id']}", headers=admin_headers)
    assert resp.status_code == 204

    list_resp = await client.get("/api/integrations", headers=admin_headers)
    assert not any(i["id"] == created["id"] for i in list_resp.json())


async def test_update_unknown_integration_404s(client, admin_headers):
    resp = await client.patch("/api/integrations/does-not-exist", headers=admin_headers, json={"enabled": False})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


async def test_integration_endpoints_require_authentication(client):
    resp = await client.get("/api/integrations")
    assert resp.status_code == 401


async def test_write_endpoints_require_notification_configure_permission(client, admin_headers):
    viewer_headers = await _viewer_headers(client, admin_headers)
    resp = await client.post("/api/integrations", headers=viewer_headers, json=INTEGRATION_PAYLOAD)
    assert resp.status_code == 403


async def test_deliveries_endpoint_requires_incident_view_permission(client, admin_headers):
    resp = await client.get("/api/integrations/deliveries")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Test-delivery endpoint
# ---------------------------------------------------------------------------


async def test_test_endpoint_reports_success_or_failure_in_body_never_500(client, admin_headers):
    # Port 1 is essentially never open on a real public host, so this reliably fails to
    # connect without depending on a specific unreachable-but-not-SSRF-blocked hostname -
    # a network failure is an expected, honestly-reported diagnostic outcome, not a server
    # error (same contract as database Test Connection).
    created = await _create_integration(client, admin_headers, target_url="https://1.1.1.1:1/hook")
    resp = await client.post(f"/api/integrations/{created['id']}/test", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["message"]


async def test_test_endpoint_unknown_integration_404s(client, admin_headers):
    resp = await client.post("/api/integrations/does-not-exist/test", headers=admin_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Deliveries
# ---------------------------------------------------------------------------


async def test_deliveries_filtered_by_incident(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    created = await _create_integration(client, admin_headers)
    incident_id = await _open_incident(db_id)

    async with SessionLocal() as db:
        service = IntegrationService(
            IntegrationRepository(db),
            IntegrationDeliveryRepository(db),
            MonitoredDatabaseRepository(db),
            LocalEncryptedSecretProvider(SecretRepository(db)),
            AuditService(AuditRepository(db)),
            sender=FakeWebhookSender(),
        )
        incident = await IncidentRepository(db).get(incident_id)
        await service.notify_if_threshold_reached(incident)
        await db.commit()

    resp = await client.get(f"/api/integrations/deliveries?incident_id={incident_id}", headers=admin_headers)
    assert resp.status_code == 200
    deliveries = resp.json()
    assert len(deliveries) == 1
    assert deliveries[0]["integration_id"] == created["id"]
    assert deliveries[0]["status"] == "PENDING"


async def test_deliveries_empty_list_for_integration_with_no_incidents(client, admin_headers):
    created = await _create_integration(client, admin_headers)
    resp = await client.get(f"/api/integrations/deliveries?integration_id={created['id']}", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == []
