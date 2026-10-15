ADMIN_EMAIL = "admin@bootstrap.dev"
ADMIN_PASSWORD = "AdminBootstrap123!"


async def test_login_success_creates_audit_event(client, admin_headers):
    resp = await client.get("/api/audit?action_prefix=auth.login.success", headers=admin_headers)
    assert resp.status_code == 200
    events = resp.json()
    assert any(e["actor_email"] == ADMIN_EMAIL and e["success"] is True for e in events)


async def test_login_failure_creates_audit_event(client, admin_headers):
    await client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})

    resp = await client.get("/api/audit?action_prefix=auth.login.failure", headers=admin_headers)
    events = resp.json()
    assert any(e["success"] is False for e in events)


async def test_audit_list_requires_permission(client):
    login = await client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    await client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "operator@ocpcc.dev",
            "password": "OperatorPassw0rd!1",
            "display_name": "Operator",
            "roles": ["OPERATOR"],
        },
    )
    op_login = await client.post(
        "/api/auth/login", json={"email": "operator@ocpcc.dev", "password": "OperatorPassw0rd!1"}
    )
    op_headers = {"Authorization": f"Bearer {op_login.json()['access_token']}"}

    resp = await client.get("/api/audit", headers=op_headers)
    assert resp.status_code == 403


async def test_database_creation_is_audited_without_secret(client, admin_headers):
    await client.post(
        "/api/databases",
        headers=admin_headers,
        json={
            "name": "AUDIT01",
            "host": "db.internal",
            "port": 1521,
            "service_name": "ORCLPDB1",
            "edition_hint": "SE2",
            "username": "monitoring_ro",
            "password": "TopSecretOraclePassword!",
        },
    )

    resp = await client.get("/api/audit?action_prefix=database.credential_set", headers=admin_headers)
    events = resp.json()
    assert len(events) >= 1
    for event in events:
        assert "TopSecretOraclePassword" not in str(event)
        assert "password" not in (event["event_metadata"] or {})
