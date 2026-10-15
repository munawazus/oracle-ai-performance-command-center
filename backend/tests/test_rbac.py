ADMIN_EMAIL = "admin@bootstrap.dev"
ADMIN_PASSWORD = "AdminBootstrap123!"


async def _login(client, email, password):
    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_admin_can_reach_admin_only_route(client, admin_headers):
    resp = await client.get("/api/admin/users", headers=admin_headers)
    assert resp.status_code == 200


async def test_viewer_cannot_manage_users(client, admin_headers):
    create = await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "email": "viewer@ocpcc.dev",
            "password": "ViewerPassw0rd!123",
            "display_name": "Viewer One",
            "roles": ["VIEWER"],
        },
    )
    assert create.status_code == 201

    viewer_headers = await _login(client, "viewer@ocpcc.dev", "ViewerPassw0rd!123")

    resp = await client.get("/api/admin/users", headers=viewer_headers)
    assert resp.status_code == 403


async def test_viewer_can_view_but_not_add_databases(client, admin_headers):
    await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "email": "viewer2@ocpcc.dev",
            "password": "ViewerPassw0rd!123",
            "display_name": "Viewer Two",
            "roles": ["VIEWER"],
        },
    )
    viewer_headers = await _login(client, "viewer2@ocpcc.dev", "ViewerPassw0rd!123")

    view_resp = await client.get("/api/databases", headers=viewer_headers)
    assert view_resp.status_code == 200

    add_resp = await client.post(
        "/api/databases",
        headers=viewer_headers,
        json={
            "name": "PROD01",
            "host": "db.internal",
            "port": 1521,
            "service_name": "ORCLPDB1",
            "edition_hint": "SE2",
            "username": "monitoring_ro",
            "password": "SuperSecretOraclePassword!",
        },
    )
    assert add_resp.status_code == 403


async def test_unauthenticated_requests_are_rejected(client):
    resp = await client.get("/api/databases")
    assert resp.status_code == 401


async def test_authorization_failure_is_audited(client, admin_headers):
    await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "email": "viewer3@ocpcc.dev",
            "password": "ViewerPassw0rd!123",
            "display_name": "Viewer Three",
            "roles": ["VIEWER"],
        },
    )
    viewer_headers = await _login(client, "viewer3@ocpcc.dev", "ViewerPassw0rd!123")
    await client.get("/api/admin/users", headers=viewer_headers)

    audit_resp = await client.get("/api/audit?action_prefix=authz.denied", headers=admin_headers)
    assert audit_resp.status_code == 200
    events = audit_resp.json()
    assert any(e["action"] == "authz.denied" and e["actor_email"] == "viewer3@ocpcc.dev" for e in events)
