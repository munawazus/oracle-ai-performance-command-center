async def _register_database(client, admin_headers, name, tags=None):
    resp = await client.post(
        "/api/databases",
        headers=admin_headers,
        json={
            "name": name,
            "host": "oracle.internal.ocpcc.dev",
            "port": 1521,
            "service_name": "ORCLPDB1",
            "edition_hint": "SE2",
            "username": "monitoring_ro",
            "password": "SuperSecretOraclePassword!",
            "tags": tags or [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_group(client, admin_headers, name, description=""):
    resp = await client.post("/api/database-groups", headers=admin_headers, json={"name": name, "description": description})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _viewer_headers(client, admin_headers, email="fleetviewer@ocpcc.dev"):
    await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"email": email, "password": "ViewerPassw0rd!123", "display_name": "Viewer", "roles": ["VIEWER"]},
    )
    login = await client.post("/api/auth/login", json={"email": email, "password": "ViewerPassw0rd!123"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


async def test_create_database_normalizes_tags(client, admin_headers):
    db = await _register_database(client, admin_headers, "TAGDB01", tags=["  Prod  ", "PROD", "east-region", ""])
    assert db["tags"] == ["prod", "east-region"]


async def test_update_database_replaces_tags(client, admin_headers):
    db = await _register_database(client, admin_headers, "TAGDB02", tags=["prod"])
    resp = await client.patch(f"/api/databases/{db['id']}", headers=admin_headers, json={"tags": ["staging", "PCI"]})
    assert resp.status_code == 200
    assert sorted(resp.json()["tags"]) == ["pci", "staging"]


async def test_update_database_can_clear_all_tags(client, admin_headers):
    db = await _register_database(client, admin_headers, "TAGDB03", tags=["prod"])
    resp = await client.patch(f"/api/databases/{db['id']}", headers=admin_headers, json={"tags": []})
    assert resp.status_code == 200
    assert resp.json()["tags"] == []


async def test_too_many_tags_rejected(client, admin_headers):
    resp = await client.post(
        "/api/databases",
        headers=admin_headers,
        json={
            "name": "TAGDB04",
            "host": "h",
            "port": 1521,
            "service_name": "s",
            "edition_hint": "SE2",
            "username": "u",
            "password": "SuperSecretOraclePassword!",
            "tags": [f"tag{i}" for i in range(25)],
        },
    )
    assert resp.status_code == 422


async def test_list_databases_filters_by_tag(client, admin_headers):
    await _register_database(client, admin_headers, "FILTAG01", tags=["prod"])
    await _register_database(client, admin_headers, "FILTAG02", tags=["staging"])

    resp = await client.get("/api/databases?tag=prod", headers=admin_headers)
    names = [d["name"] for d in resp.json()]
    assert names == ["FILTAG01"]


# ---------------------------------------------------------------------------
# Groups CRUD
# ---------------------------------------------------------------------------


async def test_create_list_and_update_group(client, admin_headers):
    group = await _create_group(client, admin_headers, "Production", "Prod fleet")
    assert group["database_count"] == 0

    list_resp = await client.get("/api/database-groups", headers=admin_headers)
    assert any(g["id"] == group["id"] for g in list_resp.json())

    update_resp = await client.patch(
        f"/api/database-groups/{group['id']}", headers=admin_headers, json={"description": "Updated desc"}
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["description"] == "Updated desc"
    assert update_resp.json()["name"] == "Production"


async def test_duplicate_group_name_rejected(client, admin_headers):
    await _create_group(client, admin_headers, "Duplicate")
    resp = await client.post("/api/database-groups", headers=admin_headers, json={"name": "Duplicate"})
    assert resp.status_code == 409


async def test_delete_group(client, admin_headers):
    group = await _create_group(client, admin_headers, "ToDelete")
    resp = await client.delete(f"/api/database-groups/{group['id']}", headers=admin_headers)
    assert resp.status_code == 204

    list_resp = await client.get("/api/database-groups", headers=admin_headers)
    assert not any(g["id"] == group["id"] for g in list_resp.json())


async def test_update_unknown_group_404s(client, admin_headers):
    resp = await client.patch("/api/database-groups/does-not-exist", headers=admin_headers, json={"name": "X"})
    assert resp.status_code == 404


async def test_group_write_endpoints_require_permission(client, admin_headers):
    viewer_headers = await _viewer_headers(client, admin_headers)
    resp = await client.post("/api/database-groups", headers=viewer_headers, json={"name": "NoPerm"})
    assert resp.status_code == 403


async def test_group_endpoints_require_authentication(client):
    resp = await client.get("/api/database-groups")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


async def test_assign_and_read_group_memberships(client, admin_headers):
    db = await _register_database(client, admin_headers, "MEMDB01")
    group_a = await _create_group(client, admin_headers, "GroupA")
    group_b = await _create_group(client, admin_headers, "GroupB")

    resp = await client.put(
        f"/api/databases/{db['id']}/groups", headers=admin_headers, json={"group_ids": [group_a["id"], group_b["id"]]}
    )
    assert resp.status_code == 200
    assert sorted(resp.json()) == sorted([group_a["id"], group_b["id"]])

    read_resp = await client.get(f"/api/databases/{db['id']}/groups", headers=admin_headers)
    assert sorted(read_resp.json()) == sorted([group_a["id"], group_b["id"]])

    group_list = await client.get("/api/database-groups", headers=admin_headers)
    counts = {g["id"]: g["database_count"] for g in group_list.json()}
    assert counts[group_a["id"]] == 1
    assert counts[group_b["id"]] == 1


async def test_set_memberships_replaces_the_full_set(client, admin_headers):
    db = await _register_database(client, admin_headers, "MEMDB02")
    group_a = await _create_group(client, admin_headers, "ReplaceA")
    group_b = await _create_group(client, admin_headers, "ReplaceB")

    await client.put(f"/api/databases/{db['id']}/groups", headers=admin_headers, json={"group_ids": [group_a["id"]]})
    resp = await client.put(f"/api/databases/{db['id']}/groups", headers=admin_headers, json={"group_ids": [group_b["id"]]})
    assert resp.json() == [group_b["id"]]


async def test_assign_unknown_group_id_rejected(client, admin_headers):
    db = await _register_database(client, admin_headers, "MEMDB03")
    resp = await client.put(f"/api/databases/{db['id']}/groups", headers=admin_headers, json={"group_ids": ["not-a-group"]})
    assert resp.status_code == 422


async def test_assign_groups_to_unknown_database_404s(client, admin_headers):
    resp = await client.put("/api/databases/does-not-exist/groups", headers=admin_headers, json={"group_ids": []})
    assert resp.status_code == 404


async def test_deleting_a_group_removes_its_memberships(client, admin_headers):
    db = await _register_database(client, admin_headers, "MEMDB04")
    group = await _create_group(client, admin_headers, "ToDeleteWithMembers")
    await client.put(f"/api/databases/{db['id']}/groups", headers=admin_headers, json={"group_ids": [group["id"]]})

    await client.delete(f"/api/database-groups/{group['id']}", headers=admin_headers)

    read_resp = await client.get(f"/api/databases/{db['id']}/groups", headers=admin_headers)
    assert read_resp.status_code == 200
    assert read_resp.json() == []


async def test_list_databases_filters_by_group(client, admin_headers):
    db_a = await _register_database(client, admin_headers, "GRPFIL01")
    await _register_database(client, admin_headers, "GRPFIL02")
    group = await _create_group(client, admin_headers, "FilterGroup")
    await client.put(f"/api/databases/{db_a['id']}/groups", headers=admin_headers, json={"group_ids": [group["id"]]})

    resp = await client.get(f"/api/databases?group_id={group['id']}", headers=admin_headers)
    names = [d["name"] for d in resp.json()]
    assert names == ["GRPFIL01"]


# ---------------------------------------------------------------------------
# Bulk onboarding
# ---------------------------------------------------------------------------


async def test_bulk_create_all_succeed(client, admin_headers):
    resp = await client.post(
        "/api/databases/bulk",
        headers=admin_headers,
        json={
            "databases": [
                {
                    "name": "BULK01",
                    "host": "h1",
                    "port": 1521,
                    "service_name": "s1",
                    "edition_hint": "SE2",
                    "username": "u",
                    "password": "SuperSecretOraclePassword!",
                },
                {
                    "name": "BULK02",
                    "host": "h2",
                    "port": 1521,
                    "service_name": "s2",
                    "edition_hint": "SE2",
                    "username": "u",
                    "password": "SuperSecretOraclePassword!",
                    "tags": ["prod"],
                },
            ]
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created_count"] == 2
    assert body["failed_count"] == 0
    assert all(r["status"] == "created" for r in body["results"])
    assert body["results"][1]["database"]["tags"] == ["prod"]


async def test_bulk_create_partial_failure_does_not_abort_batch(client, admin_headers):
    await _register_database(client, admin_headers, "BULKDUP")

    resp = await client.post(
        "/api/databases/bulk",
        headers=admin_headers,
        json={
            "databases": [
                {
                    "name": "BULKDUP",  # duplicate - will fail
                    "host": "h1",
                    "port": 1521,
                    "service_name": "s1",
                    "edition_hint": "SE2",
                    "username": "u",
                    "password": "SuperSecretOraclePassword!",
                },
                {
                    "name": "BULKOK",  # unique - should still succeed
                    "host": "h2",
                    "port": 1521,
                    "service_name": "s2",
                    "edition_hint": "SE2",
                    "username": "u",
                    "password": "SuperSecretOraclePassword!",
                },
            ]
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created_count"] == 1
    assert body["failed_count"] == 1
    assert body["results"][0]["status"] == "failed"
    assert body["results"][0]["database"] is None
    assert "already registered" in body["results"][0]["error"]
    assert body["results"][1]["status"] == "created"

    list_resp = await client.get("/api/databases", headers=admin_headers)
    names = [d["name"] for d in list_resp.json()]
    assert names.count("BULKOK") == 1


async def test_bulk_create_empty_list_rejected(client, admin_headers):
    resp = await client.post("/api/databases/bulk", headers=admin_headers, json={"databases": []})
    assert resp.status_code == 422


async def test_bulk_create_over_max_batch_size_rejected(client, admin_headers):
    item = {
        "host": "h",
        "port": 1521,
        "service_name": "s",
        "edition_hint": "SE2",
        "username": "u",
        "password": "SuperSecretOraclePassword!",
    }
    payload = {"databases": [{**item, "name": f"BULKMANY{i}"} for i in range(101)]}
    resp = await client.post("/api/databases/bulk", headers=admin_headers, json=payload)
    assert resp.status_code == 422


async def test_bulk_create_requires_database_add_permission(client, admin_headers):
    viewer_headers = await _viewer_headers(client, admin_headers, email="bulkviewer@ocpcc.dev")
    resp = await client.post(
        "/api/databases/bulk",
        headers=viewer_headers,
        json={
            "databases": [
                {
                    "name": "SHOULDFAIL",
                    "host": "h",
                    "port": 1521,
                    "service_name": "s",
                    "edition_hint": "SE2",
                    "username": "u",
                    "password": "SuperSecretOraclePassword!",
                }
            ]
        },
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Global incident view - fleet filters
# ---------------------------------------------------------------------------


async def test_incidents_filtered_by_group(client, admin_headers):
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.repositories.analytics_repo import AnomalyRepository
    from app.repositories.incident_repo import IncidentRepository
    from app.services.incident_service import IncidentService

    db_in_group = await _register_database(client, admin_headers, "INCFLEET01")
    db_outside = await _register_database(client, admin_headers, "INCFLEET02")
    group = await _create_group(client, admin_headers, "IncidentFleet")
    await client.put(f"/api/databases/{db_in_group['id']}/groups", headers=admin_headers, json={"group_ids": [group["id"]]})

    async def _open(db_id):
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

    incident_in_group = await _open(db_in_group["id"])
    await _open(db_outside["id"])

    resp = await client.get(f"/api/incidents?group_id={group['id']}", headers=admin_headers)
    ids = [i["id"] for i in resp.json()]
    assert ids == [incident_in_group]


async def test_incidents_filtered_by_tag(client, admin_headers):
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.repositories.analytics_repo import AnomalyRepository
    from app.repositories.incident_repo import IncidentRepository
    from app.services.incident_service import IncidentService

    tagged = await _register_database(client, admin_headers, "INCTAG01", tags=["pci"])
    untagged = await _register_database(client, admin_headers, "INCTAG02")

    async def _open(db_id):
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

    incident_tagged = await _open(tagged["id"])
    await _open(untagged["id"])

    resp = await client.get("/api/incidents?tag=pci", headers=admin_headers)
    ids = [i["id"] for i in resp.json()]
    assert ids == [incident_tagged]


async def test_incidents_group_filter_matching_nothing_returns_empty_not_error(client, admin_headers):
    group = await _create_group(client, admin_headers, "EmptyFleetGroup")
    resp = await client.get(f"/api/incidents?group_id={group['id']}", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == []
