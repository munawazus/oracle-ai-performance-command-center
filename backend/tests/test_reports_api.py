import asyncio
import json
from datetime import datetime, timedelta, timezone

from app.ai.gateway import get_llm_client
from app.db import SessionLocal
from app.main import app
from app.oracle.telemetry_probe import InstanceHealthSample, SessionSampleRow, WaitSnapshotRow
from app.repositories.analytics_repo import AnomalyRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.notification_repo import NotificationEventRepository
from app.repositories.telemetry_repo import TelemetryRepository
from app.services.incident_service import IncidentService
from tests.test_copilot_service import FakeLLMClient

DB_PAYLOAD = {
    "name": "REPORTAPI01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}

BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


async def _register_database(client, admin_headers, name=None) -> str:
    payload = dict(DB_PAYLOAD)
    if name:
        payload["name"] = name
    resp = await client.post("/api/databases", headers=admin_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _viewer_headers(client, admin_headers, email="reportviewer@ocpcc.dev"):
    await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"email": email, "password": "ViewerPassw0rd!123", "display_name": "Viewer", "roles": ["VIEWER"]},
    )
    login = await client.post("/api/auth/login", json={"email": email, "password": "ViewerPassw0rd!123"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _seed_full_telemetry(db_id: str) -> None:
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_session_samples(
            db_id,
            BASE_TIME,
            [
                SessionSampleRow(101, 1, "BLOCKER", "sql1", None, None, "ACTIVE", None, "host1", "sqlplus", None, None),
                SessionSampleRow(102, 2, "VICTIM", "sql2", "enq: TX", "Application", "ACTIVE", 101, "host2", "sqlplus", None, None),
            ],
        )
        await repo.insert_wait_snapshots(
            db_id,
            BASE_TIME - timedelta(minutes=1),
            [WaitSnapshotRow(event="db file sequential read", wait_class="User I/O", total_waits=10, time_waited_centisecs=1000)],
        )
        await repo.insert_wait_snapshots(
            db_id,
            BASE_TIME,
            [WaitSnapshotRow(event="db file sequential read", wait_class="User I/O", total_waits=20, time_waited_centisecs=4600)],
        )
        await repo.insert_instance_health(db_id, BASE_TIME - timedelta(minutes=5), "up", InstanceHealthSample(status="OPEN", active_session_count=8))
        await repo.insert_instance_health(db_id, BASE_TIME, "up", InstanceHealthSample(status="OPEN", active_session_count=12))
        await db.commit()


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
        anomaly.detected_at = BASE_TIME
        incident = await IncidentService(IncidentRepository(db)).correlate_anomaly(anomaly)
        await db.commit()
        return incident.id


def _period(client_payload: dict) -> dict:
    payload = dict(client_payload)
    payload["period_start"] = (BASE_TIME - timedelta(hours=1)).isoformat()
    payload["period_end"] = (BASE_TIME + timedelta(hours=1)).isoformat()
    return payload


# ---------------------------------------------------------------------------
# Generation - Daily DBA report
# ---------------------------------------------------------------------------


async def test_daily_dba_report_generation_with_real_data(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _seed_full_telemetry(db_id)
    incident_id = await _open_incident(db_id)

    resp = await client.post(
        "/api/reports", headers=admin_headers, json=_period({"report_type": "DAILY_DBA", "database_id": db_id})
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["data_available"] is True
    assert body["report_type"] == "DAILY_DBA"
    content = body["content"]
    assert content["active_incidents"]["open_count"] == 1
    assert content["active_incidents"]["items"][0]["id"] == incident_id
    assert content["important_anomalies"]["count_in_period"] >= 1
    assert content["wait_summary"]["available"] is True
    assert content["blocking_summary"]["available"] is True
    assert len(content["blocking_summary"]["blocking_chains"]) == 1
    assert content["top_sql_summary"]["available"] is False  # no SQL telemetry seeded in this test
    assert content["sql_regression_summary"] == {"available": True, "open_regression_count": 0, "regressions": []}
    assert content["capacity_summary"]["available"] is False
    assert len(content["health_check_results"]["checks"]) == 11
    assert content["recommended_investigation_items"]


async def test_daily_dba_report_with_no_data_is_honest_not_fake(client, admin_headers):
    db_id = await _register_database(client, admin_headers, name="EMPTYDB01")

    resp = await client.post("/api/reports", headers=admin_headers, json={"report_type": "DAILY_DBA", "database_id": db_id})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["data_available"] is True  # the report itself generates fine
    content = body["content"]
    assert content["active_incidents"]["open_count"] == 0
    assert content["important_anomalies"]["count_in_period"] == 0
    assert content["wait_summary"]["available"] is False
    assert content["blocking_summary"]["available"] is False
    assert "No instance health telemetry was collected" in " ".join(body["limitations"])


# ---------------------------------------------------------------------------
# Generation - Incident report
# ---------------------------------------------------------------------------


async def test_incident_report_generation_includes_evidence_timeline_and_notifications(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    incident_id = await _open_incident(db_id)

    async with SessionLocal() as db:
        await NotificationEventRepository(db).create(
            incident_id=incident_id,
            dedupe_key=f"{incident_id}:HIGH",
            severity="HIGH",
            recipients=["oncall@ocpcc.dev"],
            subject="[HIGH] test",
            body_text="body",
        )
        await db.commit()

    resp = await client.post("/api/reports", headers=admin_headers, json={"report_type": "INCIDENT", "incident_id": incident_id})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["incident_id"] == incident_id
    content = body["content"]
    assert content["incident"]["id"] == incident_id
    assert content["incident"]["severity"] == "HIGH"
    assert len(content["timeline"]) == 1
    assert content["timeline"][0]["event_type"] == "opened"
    assert len(content["evidence"]) == 1
    assert len(content["notification_history"]) == 1
    assert content["notification_history"][0]["recipients"] == ["oncall@ocpcc.dev"]
    assert content["rca"]["status"] == "not_requested"


async def test_incident_report_correlates_sql_from_session_samples_in_the_incident_window(client, admin_headers):
    """Enterprise Readiness Plan D.1: affected_sql_session_workload is a real temporal
    correlation now, not the old permanent 'Phase 9 not implemented' stub."""
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        await TelemetryRepository(db).insert_session_samples(
            db_id,
            BASE_TIME,
            [SessionSampleRow(101, 1, "APPUSER", "abc123sqlid", None, None, "ACTIVE", None, "host1", "sqlplus", None, None)],
        )
        await db.commit()
    incident_id = await _open_incident(db_id)  # opened_at == BASE_TIME, see _open_incident

    resp = await client.post("/api/reports", headers=admin_headers, json={"report_type": "INCIDENT", "incident_id": incident_id})
    assert resp.status_code == 201, resp.text
    workload = resp.json()["content"]["affected_sql_session_workload"]
    assert workload["available"] is True
    assert workload["distinct_sql_id_count"] == 1
    assert workload["statements"][0]["sql_id"] == "abc123sqlid"


async def test_incident_report_requires_incident_id(client, admin_headers):
    resp = await client.post("/api/reports", headers=admin_headers, json={"report_type": "INCIDENT"})
    assert resp.status_code == 422


async def test_incident_report_404_for_unknown_incident(client, admin_headers):
    resp = await client.post(
        "/api/reports", headers=admin_headers, json={"report_type": "INCIDENT", "incident_id": "does-not-exist"}
    )
    assert resp.status_code == 404


async def test_incident_report_includes_rca_when_requested_and_ai_enabled(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    incident_id = await _open_incident(db_id)

    rca_json = json.dumps(
        {
            "probable_root_cause": "Session spike",
            "confidence": "medium",
            "evidence_summary": "Active session count exceeded baseline.",
            "secondary_contributors": [],
            "alternative_hypotheses": [],
            "recommended_investigation": ["Check application connection pool."],
            "recommended_action": [],
            "risk": None,
            "data_completeness_note": "Evidence is limited to platform telemetry.",
        }
    )
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient(response=rca_json)
    try:
        resp = await client.post(
            "/api/reports",
            headers=admin_headers,
            json={"report_type": "INCIDENT", "incident_id": incident_id, "include_rca": True},
        )
    finally:
        app.dependency_overrides.pop(get_llm_client, None)

    assert resp.status_code == 201, resp.text
    rca = resp.json()["content"]["rca"]
    assert rca["status"] == "included"
    assert rca["result"]["probable_root_cause"] == "Session spike"
    assert "hypothesis" in rca["note"].lower()


async def test_incident_report_notes_ai_disabled_when_not_requested(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    incident_id = await _open_incident(db_id)

    resp = await client.post("/api/reports", headers=admin_headers, json={"report_type": "INCIDENT", "incident_id": incident_id})
    assert resp.status_code == 201
    body = resp.json()
    assert body["content"]["rca"]["status"] == "not_requested"
    assert any("AI is disabled" in lim or "not requested" in lim for lim in body["limitations"])


# ---------------------------------------------------------------------------
# Generation - Performance report
# ---------------------------------------------------------------------------


async def test_performance_report_generation_with_real_data(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _seed_full_telemetry(db_id)

    resp = await client.post(
        "/api/reports", headers=admin_headers, json=_period({"report_type": "PERFORMANCE", "database_id": db_id})
    )
    assert resp.status_code == 201, resp.text
    content = resp.json()["content"]
    assert content["cpu"]["available"] is False
    assert content["io"]["available"] is False
    assert content["waits"]["available"] is True
    assert len(content["historical_comparison"]["active_session_count_timeseries"]) == 2


async def test_performance_report_with_no_data_in_period(client, admin_headers):
    db_id = await _register_database(client, admin_headers, name="EMPTYDB02")
    resp = await client.post("/api/reports", headers=admin_headers, json={"report_type": "PERFORMANCE", "database_id": db_id})
    assert resp.status_code == 201
    content = resp.json()["content"]
    assert content["historical_comparison"]["active_session_count_timeseries"] == []


# ---------------------------------------------------------------------------
# Generation - SQL regression (Enterprise Readiness Plan D.1) / Capacity (still unbuilt)
# ---------------------------------------------------------------------------


async def test_sql_regression_report_is_honestly_unavailable_with_no_sql_telemetry(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    resp = await client.post("/api/reports", headers=admin_headers, json={"report_type": "SQL_REGRESSION", "database_id": db_id})
    assert resp.status_code == 201
    body = resp.json()
    assert body["data_available"] is False
    assert "No SQL statistics have been collected yet" in body["data_completeness_note"]
    assert body["content"]["sections"] == {}


async def test_sql_regression_report_returns_real_data_once_sql_telemetry_exists(client, admin_headers):
    from app.oracle.sql_probe import SqlStatRow
    from app.repositories.sql_repo import SqlTelemetryRepository

    db_id = await _register_database(client, admin_headers)
    row_1 = SqlStatRow("abc123sqlid", 111, 10, 100_000, 80_000, 500, 5, 10, 10, 1, "APPUSER", "SELECT 1")
    row_2 = SqlStatRow("abc123sqlid", 111, 20, 250_000, 200_000, 900, 9, 20, 20, 1, "APPUSER", "SELECT 1")
    async with SessionLocal() as db:
        repo = SqlTelemetryRepository(db)
        await repo.upsert_statements(db_id, BASE_TIME, [row_1])
        await repo.insert_stat_samples(db_id, BASE_TIME, [row_1])
        await repo.insert_stat_samples(db_id, BASE_TIME + timedelta(seconds=300), [row_2])
        await repo.create_regression(
            db_id, sql_id="abc123sqlid", regression_kind="ELAPSED_PER_EXEC", severity="HIGH",
            detection_note="Elapsed time per execution went from 1000us to 15000us (15.0x).",
        )
        await db.commit()

    resp = await client.post("/api/reports", headers=admin_headers, json={"report_type": "SQL_REGRESSION", "database_id": db_id})
    assert resp.status_code == 201
    body = resp.json()
    assert body["data_available"] is True
    content = body["content"]
    assert content["top_sql"]["available"] is True
    assert content["top_sql"]["statements"][0]["sql_id"] == "abc123sqlid"
    assert len(content["open_regressions"]) == 1
    assert content["open_regressions"][0]["severity"] == "HIGH"


async def test_capacity_report_is_honestly_unavailable_with_no_capacity_telemetry(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    resp = await client.post("/api/reports", headers=admin_headers, json={"report_type": "CAPACITY", "database_id": db_id})
    assert resp.status_code == 201
    body = resp.json()
    assert body["data_available"] is False
    assert body["content"]["sections"] == {}


async def test_capacity_report_returns_real_data_once_capacity_telemetry_exists(client, admin_headers):
    from app.oracle.tablespace_probe import TablespaceRow
    from app.repositories.capacity_repo import CapacityRepository

    db_id = await _register_database(client, admin_headers)
    row = TablespaceRow("SYSTEM", "PERMANENT", "ONLINE", 1000, 950, 2000, 1)
    async with SessionLocal() as db:
        repo = CapacityRepository(db)
        await repo.insert_tablespace_samples(db_id, BASE_TIME, [row])
        await repo.create_alert(
            db_id, tablespace_name="SYSTEM", alert_kind="PCT_USED", severity="CRITICAL",
            pct_used=95.0, projected_days_to_full=None, current_used_bytes=950,
            current_allocated_bytes=1000, current_max_bytes=2000,
            detection_note="Tablespace SYSTEM is 95.0% used (950 of 1000 allocated bytes).",
        )
        await db.commit()

    resp = await client.post("/api/reports", headers=admin_headers, json={"report_type": "CAPACITY", "database_id": db_id})
    assert resp.status_code == 201
    body = resp.json()
    assert body["data_available"] is True
    content = body["content"]
    assert content["tablespaces"]["available"] is True
    assert content["tablespaces"]["tablespaces"][0]["tablespace_name"] == "SYSTEM"
    assert len(content["open_alerts"]) == 1
    assert content["open_alerts"][0]["severity"] == "CRITICAL"


# ---------------------------------------------------------------------------
# Generation - Health report
# ---------------------------------------------------------------------------


async def test_health_report_generation_shows_mixed_statuses(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _seed_full_telemetry(db_id)

    resp = await client.post("/api/reports", headers=admin_headers, json={"report_type": "HEALTH", "database_id": db_id})
    assert resp.status_code == 201
    content = resp.json()["content"]
    categories = {c["category"]: c["status"] for c in content["categories"]}
    assert categories["locking"] == "WARN"  # seeded blocking chain
    assert categories["sql_regression"] == "UNAVAILABLE"
    assert categories["storage"] == "UNAVAILABLE"
    assert content["overall"]["categories_total"] == 11


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_database_scoped_report_requires_database_id(client, admin_headers):
    resp = await client.post("/api/reports", headers=admin_headers, json={"report_type": "HEALTH"})
    assert resp.status_code == 422


async def test_generate_404_for_unknown_database_id(client, admin_headers):
    resp = await client.post(
        "/api/reports", headers=admin_headers, json={"report_type": "DAILY_DBA", "database_id": "does-not-exist"}
    )
    assert resp.status_code == 404


async def test_invalid_date_range_rejected(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    resp = await client.post(
        "/api/reports",
        headers=admin_headers,
        json={
            "report_type": "DAILY_DBA",
            "database_id": db_id,
            "period_start": BASE_TIME.isoformat(),
            "period_end": (BASE_TIME - timedelta(hours=1)).isoformat(),
        },
    )
    assert resp.status_code == 422


async def test_period_exceeding_max_days_rejected(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    resp = await client.post(
        "/api/reports",
        headers=admin_headers,
        json={
            "report_type": "PERFORMANCE",
            "database_id": db_id,
            "period_start": (BASE_TIME - timedelta(days=60)).isoformat(),
            "period_end": BASE_TIME.isoformat(),
        },
    )
    assert resp.status_code == 422


async def test_unknown_report_type_rejected_by_schema(client, admin_headers):
    resp = await client.post("/api/reports", headers=admin_headers, json={"report_type": "NOT_A_REAL_TYPE"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# RBAC / auth
# ---------------------------------------------------------------------------


async def test_generate_requires_authentication(client):
    resp = await client.post("/api/reports", json={"report_type": "DAILY_DBA", "database_id": "x"})
    assert resp.status_code == 401


async def test_generate_requires_report_create_permission(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    viewer_headers = await _viewer_headers(client, admin_headers)
    resp = await client.post("/api/reports", headers=viewer_headers, json={"report_type": "DAILY_DBA", "database_id": db_id})
    assert resp.status_code == 403


async def test_view_and_list_require_report_create_permission(client, admin_headers):
    viewer_headers = await _viewer_headers(client, admin_headers, email="listviewer@ocpcc.dev")
    resp = await client.get("/api/reports", headers=viewer_headers)
    assert resp.status_code == 403
    resp2 = await client.get("/api/reports/some-id", headers=viewer_headers)
    assert resp2.status_code == 403


async def test_export_requires_report_create_permission(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    gen = await client.post("/api/reports", headers=admin_headers, json={"report_type": "HEALTH", "database_id": db_id})
    report_id = gen.json()["id"]
    viewer_headers = await _viewer_headers(client, admin_headers, email="exportviewer@ocpcc.dev")
    resp = await client.get(f"/api/reports/{report_id}/export?format=csv", headers=viewer_headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# List / view / filter
# ---------------------------------------------------------------------------


async def test_list_reports_filters_by_type_and_database(client, admin_headers):
    db_id_a = await _register_database(client, admin_headers, name="FILTERDBA01")
    db_id_b = await _register_database(client, admin_headers, name="FILTERDBA02")
    await client.post("/api/reports", headers=admin_headers, json={"report_type": "HEALTH", "database_id": db_id_a})
    await client.post("/api/reports", headers=admin_headers, json={"report_type": "CAPACITY", "database_id": db_id_a})
    await client.post("/api/reports", headers=admin_headers, json={"report_type": "HEALTH", "database_id": db_id_b})

    by_type = await client.get("/api/reports?report_type=HEALTH", headers=admin_headers)
    assert all(r["report_type"] == "HEALTH" for r in by_type.json())
    assert len(by_type.json()) == 2

    by_db = await client.get(f"/api/reports?database_id={db_id_a}", headers=admin_headers)
    assert all(r["monitored_database_id"] == db_id_a for r in by_db.json())
    assert len(by_db.json()) == 2

    combined = await client.get(f"/api/reports?report_type=HEALTH&database_id={db_id_a}", headers=admin_headers)
    assert len(combined.json()) == 1


async def test_get_report_returns_full_content(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    gen = await client.post("/api/reports", headers=admin_headers, json={"report_type": "HEALTH", "database_id": db_id})
    report_id = gen.json()["id"]

    resp = await client.get(f"/api/reports/{report_id}", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["content"]["categories"]


async def test_get_unknown_report_404s(client, admin_headers):
    resp = await client.get("/api/reports/does-not-exist", headers=admin_headers)
    assert resp.status_code == 404


async def test_list_reports_with_sql_injection_like_filter_is_inert(client, admin_headers):
    resp = await client.get("/api/reports?report_type=%27%3B%20DROP%20TABLE%20reports%3B--", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == []
    # the table must still exist and be queryable afterwards
    resp2 = await client.get("/api/reports", headers=admin_headers)
    assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


async def test_export_csv_returns_csv_bytes_with_content_disposition(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    gen = await client.post("/api/reports", headers=admin_headers, json={"report_type": "HEALTH", "database_id": db_id})
    report_id = gen.json()["id"]

    resp = await client.get(f"/api/reports/{report_id}/export?format=csv", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert report_id in resp.headers["content-disposition"]
    text = resp.content.decode("utf-8")
    assert "report_id" in text
    assert "-- Health checks --" in text


async def test_export_pdf_returns_pdf_bytes(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    gen = await client.post("/api/reports", headers=admin_headers, json={"report_type": "HEALTH", "database_id": db_id})
    report_id = gen.json()["id"]

    resp = await client.get(f"/api/reports/{report_id}/export?format=pdf", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


async def test_export_unsupported_format_rejected(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    gen = await client.post("/api/reports", headers=admin_headers, json={"report_type": "HEALTH", "database_id": db_id})
    report_id = gen.json()["id"]

    resp = await client.get(f"/api/reports/{report_id}/export?format=xml", headers=admin_headers)
    assert resp.status_code == 422


async def test_export_unknown_report_id_404s(client, admin_headers):
    resp = await client.get("/api/reports/does-not-exist/export?format=csv", headers=admin_headers)
    assert resp.status_code == 404


async def test_export_with_path_traversal_style_report_id_404s_safely(client, admin_headers):
    resp = await client.get("/api/reports/..%2F..%2Fetc%2Fpasswd/export?format=csv", headers=admin_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


async def test_report_content_never_leaks_database_password(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _seed_full_telemetry(db_id)
    await _open_incident(db_id)

    resp = await client.post(
        "/api/reports", headers=admin_headers, json=_period({"report_type": "DAILY_DBA", "database_id": db_id})
    )
    assert DB_PAYLOAD["password"] not in resp.text

    export_resp = await client.get(
        f"/api/reports/{resp.json()['id']}/export?format=pdf", headers=admin_headers
    )
    assert DB_PAYLOAD["password"].encode() not in export_resp.content


async def test_incident_title_with_markup_characters_exports_to_pdf_without_crashing(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    incident_id = await _open_incident(db_id)

    resolve_resp = await client.post(
        f"/api/incidents/{incident_id}/resolve",
        headers=admin_headers,
        json={"note": '<b onmouseover=alert(1)>injected</b> & "quoted"'},
    )
    assert resolve_resp.status_code == 200

    gen = await client.post("/api/reports", headers=admin_headers, json={"report_type": "INCIDENT", "incident_id": incident_id})
    assert gen.status_code == 201
    report_id = gen.json()["id"]

    pdf_resp = await client.get(f"/api/reports/{report_id}/export?format=pdf", headers=admin_headers)
    assert pdf_resp.status_code == 200
    assert pdf_resp.content.startswith(b"%PDF")

    csv_resp = await client.get(f"/api/reports/{report_id}/export?format=csv", headers=admin_headers)
    assert csv_resp.status_code == 200


# ---------------------------------------------------------------------------
# Auditability
# ---------------------------------------------------------------------------


async def test_generate_and_export_produce_audit_events(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    gen = await client.post("/api/reports", headers=admin_headers, json={"report_type": "HEALTH", "database_id": db_id})
    report_id = gen.json()["id"]
    await client.get(f"/api/reports/{report_id}/export?format=csv", headers=admin_headers)

    async with SessionLocal() as db:
        events = await AuditRepository(db).list_events(limit=100, action_prefix="report.")
    actions = {e.action for e in events}
    assert "report.generated" in actions
    assert "report.exported" in actions
    generated_event = next(e for e in events if e.action == "report.generated")
    assert generated_event.target_id == report_id
    assert generated_event.success is True


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


async def test_daily_dba_report_content_is_deterministic_for_unchanged_data(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _seed_full_telemetry(db_id)
    await _open_incident(db_id)

    payload = _period({"report_type": "DAILY_DBA", "database_id": db_id})
    first = await client.post("/api/reports", headers=admin_headers, json=payload)
    second = await client.post("/api/reports", headers=admin_headers, json=payload)

    assert first.json()["content"] == second.json()["content"]
    assert first.json()["id"] != second.json()["id"]


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


async def test_concurrent_generation_produces_independent_reports(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    payload = {"report_type": "HEALTH", "database_id": db_id}

    responses = await asyncio.gather(
        *[client.post("/api/reports", headers=admin_headers, json=payload) for _ in range(5)]
    )
    assert all(r.status_code == 201 for r in responses)
    ids = {r.json()["id"] for r in responses}
    assert len(ids) == 5
