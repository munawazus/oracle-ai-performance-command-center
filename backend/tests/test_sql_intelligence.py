"""No real Oracle instance is available for this suite - drives CollectorService's SQL job
and the SQL Intelligence API end-to-end against a fake connector/connection/cursor, same
pattern as test_collector.py (this repo's SQL Intelligence work has been separately verified
against a real Oracle 19c instance - see docs/ENTERPRISE_READINESS_PLAN.md D.1's Log entry).
"""

from sqlalchemy import select

from app.db import SessionLocal
from app.models.sql_telemetry import SqlPlanSnapshot, SqlStatement
from app.models.telemetry import CollectorStatus, SessionSample
from app.oracle.allowlist import BLOCKLISTED_VIEWS
from app.oracle.connector import OracleConnectError
from app.services.collector_service import CollectorService

DB_PAYLOAD = {
    "name": "SQLINTEL01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}

STAT_ROWS_ROUND_1 = [
    ("abc123sqlid", 111111, 10, 100_000, 80_000, 500, 5, 10, 10, 1, "APPUSER", "SELECT * FROM orders WHERE id = :1"),
    ("def456sqlid", 222222, 5, 50_000, 40_000, 200, 2, 5, 5, 1, "APPUSER", "UPDATE orders SET status = :1"),
]
STAT_ROWS_ROUND_2 = [
    ("abc123sqlid", 111111, 20, 250_000, 200_000, 900, 9, 20, 20, 1, "APPUSER", "SELECT * FROM orders WHERE id = :1"),
    ("def456sqlid", 222222, 8, 70_000, 55_000, 260, 3, 8, 8, 1, "APPUSER", "UPDATE orders SET status = :1"),
]
PLAN_ROWS = [
    ("abc123sqlid", 111111, 0, None, 0, 1, "SELECT STATEMENT", None, None, None, None, 5, None, None),
    ("abc123sqlid", 111111, 1, 0, 1, 1, "TABLE ACCESS", "FULL", "APPUSER", "ORDERS", "TABLE", 5, 100, 8000),
    ("def456sqlid", 222222, 0, None, 0, 1, "UPDATE STATEMENT", None, None, None, None, 3, None, None),
]


class FakeSqlCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, statement: str) -> None:
        self.conn.executed.append(statement)
        upper = statement.upper()
        if "V$SQL_PLAN" in upper:
            if self.conn.plan_query_fails:
                raise Exception("simulated: no SELECT on V$SQL_PLAN")
            self._rows = self.conn.plan_rows
        elif "V$SQL" in upper:
            if self.conn.stat_query_fails:
                raise Exception("simulated: no SELECT on V$SQL")
            self._rows = self.conn.stat_rows
        else:
            raise AssertionError(f"unexpected query in SQL intelligence test: {statement}")

    def fetchone(self):
        return None

    def fetchall(self):
        return self._rows


class FakeSqlConnection:
    def __init__(self, stat_rows=None, plan_rows=None, stat_query_fails=False, plan_query_fails=False):
        self.stat_rows = stat_rows if stat_rows is not None else STAT_ROWS_ROUND_1
        self.plan_rows = plan_rows if plan_rows is not None else PLAN_ROWS
        self.stat_query_fails = stat_query_fails
        self.plan_query_fails = plan_query_fails
        self.executed: list[str] = []
        self.closed = False

    def cursor(self):
        return FakeSqlCursor(self)

    def close(self):
        self.closed = True


def make_fake_sql_connector(**conn_kwargs):
    created: list[FakeSqlConnection] = []

    def _connector(_params):
        conn = FakeSqlConnection(**conn_kwargs)
        created.append(conn)
        return conn

    _connector.created = created
    return _connector


async def _register_database(client, admin_headers, name: str = DB_PAYLOAD["name"]) -> str:
    payload = dict(DB_PAYLOAD, name=name)
    resp = await client.post("/api/databases", headers=admin_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# -- collector -----------------------------------------------------------------------------


async def test_sql_collector_persists_statements_stats_and_plans(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = CollectorService(make_fake_sql_connector())

    await service.run_sql_once_for_database(db_id, top_n=10)

    async with SessionLocal() as db:
        statements = (
            (await db.execute(select(SqlStatement).where(SqlStatement.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        plans = (
            (await db.execute(select(SqlPlanSnapshot).where(SqlPlanSnapshot.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert {s.sql_id for s in statements} == {"abc123sqlid", "def456sqlid"}
    assert any(s.sql_text and "orders" in s.sql_text for s in statements)
    assert {p.sql_id for p in plans} == {"abc123sqlid", "def456sqlid"}
    plan_for_abc = next(p for p in plans if p.sql_id == "abc123sqlid")
    assert plan_for_abc.plan_line_count == 2
    assert plan_for_abc.plan_lines[1]["operation"] == "TABLE ACCESS"
    assert status.last_sql_success_at is not None
    assert status.last_sql_error_category is None


async def test_sql_collector_never_queries_blocklisted_views(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    connector = make_fake_sql_connector()
    service = CollectorService(connector)

    await service.run_sql_once_for_database(db_id, top_n=10)

    assert len(connector.created) == 1
    executed_sql = " ".join(connector.created[0].executed).upper()
    for blocked in BLOCKLISTED_VIEWS:
        assert blocked not in executed_sql
    assert connector.created[0].closed is True


async def test_sql_capture_text_false_tracks_statements_without_storing_text(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = CollectorService(make_fake_sql_connector())

    await service.run_sql_once_for_database(db_id, top_n=10, capture_text=False)

    async with SessionLocal() as db:
        statements = (
            (await db.execute(select(SqlStatement).where(SqlStatement.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
    assert len(statements) == 2
    assert all(s.sql_text is None for s in statements)


async def test_a_plan_probe_failure_still_lets_stats_persist(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = CollectorService(make_fake_sql_connector(plan_query_fails=True))

    await service.run_sql_once_for_database(db_id, top_n=10)

    async with SessionLocal() as db:
        statements = (
            (await db.execute(select(SqlStatement).where(SqlStatement.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        plans = (
            (await db.execute(select(SqlPlanSnapshot).where(SqlPlanSnapshot.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert len(statements) == 2  # stats persisted despite the missing V$SQL_PLAN grant
    assert plans == []
    assert status.last_sql_success_at is not None  # still counted a success - partial capability


async def test_a_stat_query_failure_records_error_without_persisting_anything(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = CollectorService(make_fake_sql_connector(stat_query_fails=True))

    await service.run_sql_once_for_database(db_id, top_n=10)

    async with SessionLocal() as db:
        statements = (
            (await db.execute(select(SqlStatement).where(SqlStatement.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert statements == []
    assert status.last_sql_error_category == "unknown_error"
    assert status.last_sql_success_at is None


async def test_sql_collector_connect_failure_does_not_affect_session_collection(client, admin_headers):
    """The core failure-isolation guarantee this design exists for: a SQL-collector-only
    failure (e.g. a missing grant) must never touch the main 15s collector's own tables."""
    db_id = await _register_database(client, admin_headers)

    from tests.test_collector import make_fake_connector

    main_collector = CollectorService(make_fake_connector())
    await main_collector.run_once_for_database(db_id)

    failing_sql_collector = CollectorService(
        lambda _params: (_ for _ in ()).throw(OracleConnectError(OracleConnectError.CATEGORY_UNKNOWN, "no grant"))
    )
    await failing_sql_collector.run_sql_once_for_database(db_id, top_n=10)  # must not raise

    async with SessionLocal() as db:
        sessions = (
            (await db.execute(select(SessionSample).where(SessionSample.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert len(sessions) == 2  # untouched by the SQL collector's failure
    assert status.consecutive_failures == 0  # the main collector's own failure counter, untouched
    assert status.last_sql_error_category == "unknown_error"  # the SQL collector's own, separate signal


async def test_plan_capture_on_change_one_row_per_distinct_plan_hash(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = CollectorService(make_fake_sql_connector())

    await service.run_sql_once_for_database(db_id, top_n=10)  # round 1: plan_hash 111111
    await service.run_sql_once_for_database(db_id, top_n=10)  # round 2: same plan, unchanged

    async with SessionLocal() as db:
        plans = (
            (
                await db.execute(
                    select(SqlPlanSnapshot).where(
                        SqlPlanSnapshot.monitored_database_id == db_id, SqlPlanSnapshot.sql_id == "abc123sqlid"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(plans) == 1  # capture-on-change: no new row for an unchanged plan hash

    # Now the plan changes (a real plan-hash-value change, e.g. a stats-driven re-optimization).
    changed_plan_rows = [
        ("abc123sqlid", 999999, 0, None, 0, 1, "SELECT STATEMENT", None, None, None, None, 50, None, None),
        ("abc123sqlid", 999999, 1, 0, 1, 1, "TABLE ACCESS", "FULL", "APPUSER", "ORDERS", "TABLE", 50, 100000, 8000000),
    ]
    changed_stat_rows = [
        (
            "abc123sqlid", 999999, 25, 5_000_000, 4_500_000, 90000, 900, 25, 25, 1, "APPUSER",
            "SELECT * FROM orders WHERE id = :1",
        ),
    ]
    service2 = CollectorService(make_fake_sql_connector(stat_rows=changed_stat_rows, plan_rows=changed_plan_rows))
    await service2.run_sql_once_for_database(db_id, top_n=10)

    async with SessionLocal() as db:
        plans_after = (
            (
                await db.execute(
                    select(SqlPlanSnapshot).where(
                        SqlPlanSnapshot.monitored_database_id == db_id, SqlPlanSnapshot.sql_id == "abc123sqlid"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert {p.plan_hash_value for p in plans_after} == {111111, 999999}  # both plans kept, real history


# -- API -----------------------------------------------------------------------------------


async def test_top_sql_is_honestly_unavailable_before_two_collection_rounds(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    resp = await client.get(f"/api/databases/{db_id}/top-sql", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "two" in body["reason"]
    assert body["rows"] == []


async def test_top_sql_returns_real_deltas_after_two_rounds(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = CollectorService(make_fake_sql_connector(stat_rows=STAT_ROWS_ROUND_1))
    await service.run_sql_once_for_database(db_id, top_n=10)
    service2 = CollectorService(make_fake_sql_connector(stat_rows=STAT_ROWS_ROUND_2))
    await service2.run_sql_once_for_database(db_id, top_n=10)

    resp = await client.get(f"/api/databases/{db_id}/top-sql", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["sort_by"] == "elapsed_time"
    top = body["rows"][0]
    assert top["sql_id"] == "abc123sqlid"  # 150,000us elapsed delta vs 20,000us for the other
    assert top["executions_delta"] == 10
    assert top["elapsed_time_us_delta"] == 150_000
    assert "orders" in top["sql_text"]


async def test_top_sql_sort_by_reorders_results(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await CollectorService(make_fake_sql_connector(stat_rows=STAT_ROWS_ROUND_1)).run_sql_once_for_database(
        db_id, top_n=10
    )
    await CollectorService(make_fake_sql_connector(stat_rows=STAT_ROWS_ROUND_2)).run_sql_once_for_database(
        db_id, top_n=10
    )

    resp = await client.get(f"/api/databases/{db_id}/top-sql?sort_by=executions", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["rows"][0]["sql_id"] == "abc123sqlid"  # +10 execs vs +3 for the other


async def test_top_sql_invalid_sort_by_is_422(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    resp = await client.get(f"/api/databases/{db_id}/top-sql?sort_by=not_a_real_field", headers=admin_headers)

    assert resp.status_code == 422


async def test_sql_detail_404s_for_an_unknown_database(client, admin_headers):
    resp = await client.get("/api/databases/does-not-exist/sql/abc123sqlid", headers=admin_headers)
    assert resp.status_code == 404


async def test_sql_detail_is_honestly_unavailable_for_an_unknown_sql_id(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    resp = await client.get(f"/api/databases/{db_id}/sql/nosuchsqlid", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["available"] is False


async def test_sql_detail_returns_statement_plans_and_recent_sessions(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await CollectorService(make_fake_sql_connector()).run_sql_once_for_database(db_id, top_n=10)

    resp = await client.get(f"/api/databases/{db_id}/sql/abc123sqlid", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert "orders" in body["sql_text"]
    assert len(body["plans"]) == 1
    assert body["plans"][0]["plan_hash_value"] == 111111
    assert body["plans"][0]["is_current"] is True


async def test_sql_plan_route_serves_the_persisted_snapshot_with_zero_oracle_connections(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    connector = make_fake_sql_connector()
    await CollectorService(connector).run_sql_once_for_database(db_id, top_n=10)
    connections_before = len(connector.created)

    resp = await client.get(f"/api/databases/{db_id}/sql/abc123sqlid/plan/111111", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert len(body["plan_lines"]) == 2
    assert body["plan_lines"][1]["object_name"] == "ORDERS"
    assert len(connector.created) == connections_before  # the read never opened a new Oracle connection


async def test_sql_plan_route_is_honestly_unavailable_for_an_uncaptured_plan(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    resp = await client.get(f"/api/databases/{db_id}/sql/abc123sqlid/plan/999999", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["available"] is False


async def test_sql_regressions_route_returns_open_events_by_default(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    from app.repositories.sql_repo import SqlTelemetryRepository

    async with SessionLocal() as db:
        await SqlTelemetryRepository(db).create_regression(
            db_id,
            sql_id="abc123sqlid",
            regression_kind="ELAPSED_PER_EXEC",
            severity="HIGH",
            baseline_elapsed_per_exec_us=1000.0,
            current_elapsed_per_exec_us=15000.0,
            ratio=15.0,
            baseline_executions=100,
            current_executions=20,
            baseline_plan_hash_value=111111,
            current_plan_hash_value=111111,
            detection_note="Elapsed time per execution went from 1000us to 15000us (15.0x).",
        )
        await db.commit()

    resp = await client.get(f"/api/databases/{db_id}/sql-regressions", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["sql_id"] == "abc123sqlid"
    assert body[0]["severity"] == "HIGH"
    assert body[0]["status"] == "OPEN"


async def test_viewer_can_read_sql_intelligence_routes(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await CollectorService(make_fake_sql_connector()).run_sql_once_for_database(db_id, top_n=10)

    await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "email": "sqlviewer@ocpcc.dev",
            "password": "ViewerPassw0rd!123",
            "display_name": "SQL Viewer",
            "roles": ["VIEWER"],
        },
    )
    login = await client.post(
        "/api/auth/login", json={"email": "sqlviewer@ocpcc.dev", "password": "ViewerPassw0rd!123"}
    )
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert (await client.get(f"/api/databases/{db_id}/top-sql", headers=viewer_headers)).status_code == 200
    assert (
        await client.get(f"/api/databases/{db_id}/sql/abc123sqlid", headers=viewer_headers)
    ).status_code == 200
    assert (
        await client.get(f"/api/databases/{db_id}/sql/abc123sqlid/plan/111111", headers=viewer_headers)
    ).status_code == 200
    assert (await client.get(f"/api/databases/{db_id}/sql-regressions", headers=viewer_headers)).status_code == 200


async def test_unauthenticated_request_is_401(client):
    resp = await client.get("/api/databases/anything/top-sql")
    assert resp.status_code == 401
