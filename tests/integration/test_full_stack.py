"""End-to-end checks against a REAL PostgreSQL + Redis, proving the platform shell works
on the actual approved stack (backend/tests/ uses SQLite/fakeredis doubles for speed)."""


async def test_migrations_produced_a_working_schema(client, admin_headers):
    resp = await client.get("/api/auth/me", headers=admin_headers)
    assert resp.status_code == 200
    assert "ADMIN" in resp.json()["roles"]


async def test_readiness_reports_real_dependencies_ok(client):
    resp = await client.get("/api/ready")
    assert resp.status_code == 200
    assert resp.json()["dependencies"] == {"postgres": "ok", "redis": "ok"}


async def test_register_database_and_audit_trail_on_real_postgres(client, admin_headers):
    create = await client.post(
        "/api/databases",
        headers=admin_headers,
        json={
            "name": "INTEGRATION01",
            "host": "oracle.internal.example",
            "port": 1521,
            "service_name": "ORCLPDB1",
            "edition_hint": "SE2",
            "username": "monitoring_ro",
            "password": "RealPostgresBackedSecret!",
        },
    )
    assert create.status_code == 201
    body = create.json()
    assert "password" not in body

    audit = await client.get("/api/audit?action_prefix=database.created", headers=admin_headers)
    assert any(e["target_id"] == body["id"] for e in audit.json())


async def test_rbac_denies_cross_role_access_on_real_postgres(client, admin_headers):
    created = await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "email": "viewer@integration.dev",
            "password": "ViewerPassw0rd!123",
            "display_name": "Integration Viewer",
            "roles": ["VIEWER"],
        },
    )
    assert created.status_code == 201

    login = await client.post(
        "/api/auth/login", json={"email": "viewer@integration.dev", "password": "ViewerPassw0rd!123"}
    )
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = await client.get("/api/admin/users", headers=viewer_headers)
    assert resp.status_code == 403
