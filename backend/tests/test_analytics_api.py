DB_PAYLOAD = {
    "name": "ANOMALYAPI01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}


async def test_anomalies_endpoint_returns_empty_list_before_any_detection(client, admin_headers):
    create = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    db_id = create.json()["id"]

    resp = await client.get(f"/api/databases/{db_id}/anomalies", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_anomalies_endpoint_404s_for_unknown_database(client, admin_headers):
    resp = await client.get("/api/databases/does-not-exist/anomalies", headers=admin_headers)
    assert resp.status_code == 404


async def test_anomalies_endpoint_requires_database_view_permission(client):
    resp = await client.get("/api/databases/some-id/anomalies")
    assert resp.status_code == 401
