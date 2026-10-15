import json

DB_PAYLOAD = {
    "name": "PROD01",
    "host": "db.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}


async def test_create_database_never_returns_credentials(client, admin_headers):
    resp = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "PROD01"
    assert "password" not in body
    assert "username" not in body
    assert "secret_ref" not in body
    assert "SuperSecretOraclePassword" not in json.dumps(body)


async def test_duplicate_name_conflicts(client, admin_headers):
    await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    resp = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    assert resp.status_code == 409


async def test_list_and_get_database(client, admin_headers):
    create = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    db_id = create.json()["id"]

    list_resp = await client.get("/api/databases", headers=admin_headers)
    assert list_resp.status_code == 200
    assert any(d["id"] == db_id for d in list_resp.json())

    get_resp = await client.get(f"/api/databases/{db_id}", headers=admin_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["host"] == DB_PAYLOAD["host"]


async def test_get_missing_database_404(client, admin_headers):
    resp = await client.get("/api/databases/does-not-exist", headers=admin_headers)
    assert resp.status_code == 404


async def test_update_database_metadata(client, admin_headers):
    create = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    db_id = create.json()["id"]

    update = await client.patch(
        f"/api/databases/{db_id}", headers=admin_headers, json={"is_active": False, "port": 1522}
    )
    assert update.status_code == 200
    assert update.json()["is_active"] is False
    assert update.json()["port"] == 1522


async def test_rotate_credential_password(client, admin_headers):
    create = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    db_id = create.json()["id"]

    update = await client.patch(
        f"/api/databases/{db_id}", headers=admin_headers, json={"password": "RotatedPassword!456"}
    )
    assert update.status_code == 200
    assert "password" not in update.json()


async def test_delete_database(client, admin_headers):
    create = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    db_id = create.json()["id"]

    delete = await client.delete(f"/api/databases/{db_id}", headers=admin_headers)
    assert delete.status_code == 204

    get_resp = await client.get(f"/api/databases/{db_id}", headers=admin_headers)
    assert get_resp.status_code == 404


async def test_invalid_port_rejected(client, admin_headers):
    payload = dict(DB_PAYLOAD, port=99999)
    resp = await client.post("/api/databases", headers=admin_headers, json=payload)
    assert resp.status_code == 422


async def test_invalid_edition_hint_rejected(client, admin_headers):
    payload = dict(DB_PAYLOAD, edition_hint="EXADATA")
    resp = await client.post("/api/databases", headers=admin_headers, json=payload)
    assert resp.status_code == 422
