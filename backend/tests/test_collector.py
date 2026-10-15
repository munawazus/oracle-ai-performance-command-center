"""No real Oracle instance is available - these tests drive CollectorService end-to-end
(connect -> sample -> persist -> update collector_status) against a fake connector/
connection/cursor, same pattern as test_oracle_capability.py."""

from sqlalchemy import select

from app.db import SessionLocal
from app.models.telemetry import CollectorStatus, InstanceHealthSnapshot, SessionSample, WaitSnapshot
from app.oracle.allowlist import BLOCKLISTED_VIEWS
from app.oracle.connector import OracleConnectError
from app.services.collector_service import CollectorService

DB_PAYLOAD = {
    "name": "COLLECTOR01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}

SESSION_ROWS = [
    (101, 5001, "APPUSER", "abc123sqlid", "db file sequential read", "User I/O", "ACTIVE", None, "app01", "sqlplus", None, None, "WAITING", 12),
    (102, 5002, "APPUSER2", None, None, None, "INACTIVE", 101, "app02", "sqlplus", None, None, None, 3600),
]
WAIT_ROWS = [
    ("db file sequential read", "User I/O", 1000, 2500),
    ("log file sync", "Commit", 200, 800),
]


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._one = None
        self._rows: list[tuple] = []

    def execute(self, statement: str) -> None:
        self.conn.executed.append(statement)
        upper = statement.upper()
        if "V$SESSION" in upper and "COUNT(*)" in upper:
            self._one = (self.conn.active_session_count,)
        elif "V$SESSION" in upper:
            self._rows = self.conn.session_rows
        elif "V$SYSTEM_EVENT" in upper:
            self._rows = self.conn.wait_rows
        elif "V$INSTANCE" in upper:
            self._one = (self.conn.instance_status,)
        elif "V$SYSSTAT" in upper:
            self._one = (self.conn.cpu_centisecs_used,)
        else:
            raise AssertionError(f"unexpected query in collector test: {statement}")

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(
        self,
        session_rows=None,
        wait_rows=None,
        instance_status="OPEN",
        active_session_count=2,
        cpu_centisecs_used=12345,
    ):
        self.session_rows = session_rows if session_rows is not None else SESSION_ROWS
        self.wait_rows = wait_rows if wait_rows is not None else WAIT_ROWS
        self.instance_status = instance_status
        self.active_session_count = active_session_count
        self.cpu_centisecs_used = cpu_centisecs_used
        self.executed: list[str] = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def make_fake_connector(**conn_kwargs):
    created: list[FakeConnection] = []

    def _connector(_params):
        conn = FakeConnection(**conn_kwargs)
        created.append(conn)
        return conn

    _connector.created = created
    return _connector


def make_failing_connector(category: str, message: str = "simulated failure"):
    def _connector(_params):
        raise OracleConnectError(category, message)

    return _connector


async def _register_database(client, admin_headers, name: str = DB_PAYLOAD["name"]) -> str:
    payload = dict(DB_PAYLOAD, name=name)
    resp = await client.post("/api/databases", headers=admin_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_collector_persists_sessions_waits_and_health(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = CollectorService(make_fake_connector())

    await service.run_once_for_database(db_id)

    async with SessionLocal() as db:
        sessions = (
            await db.execute(select(SessionSample).where(SessionSample.monitored_database_id == db_id))
        ).scalars().all()
        waits = (
            await db.execute(select(WaitSnapshot).where(WaitSnapshot.monitored_database_id == db_id))
        ).scalars().all()
        health = (
            await db.execute(
                select(InstanceHealthSnapshot).where(InstanceHealthSnapshot.monitored_database_id == db_id)
            )
        ).scalars().all()
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert len(sessions) == 2
    assert {s.sid for s in sessions} == {101, 102}
    assert {s.sid: s.state for s in sessions} == {101: "WAITING", 102: None}
    assert len(waits) == 2
    assert len(health) == 1
    assert health[0].reachability == "up"
    assert health[0].instance_status == "OPEN"
    assert health[0].active_session_count == 2
    assert health[0].cpu_centisecs_used == 12345
    assert status.consecutive_failures == 0
    assert status.last_success_at is not None
    assert status.last_run_duration_ms is not None


async def test_collector_records_failure_without_crashing_or_leaking_raw_text(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = CollectorService(
        make_failing_connector(OracleConnectError.CATEGORY_UNREACHABLE, "TNS timeout to 10.0.0.9")
    )

    await service.run_once_for_database(db_id)  # must not raise

    async with SessionLocal() as db:
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()
        health = (
            await db.execute(
                select(InstanceHealthSnapshot).where(InstanceHealthSnapshot.monitored_database_id == db_id)
            )
        ).scalars().all()

    assert status.consecutive_failures == 1
    assert status.last_error_category == "unreachable"
    assert "10.0.0.9" not in (status.last_error_category or "")
    assert len(health) == 1
    assert health[0].reachability == "unreachable"
    assert health[0].instance_status is None


async def test_run_once_all_active_isolates_one_databases_failure_from_another(client, admin_headers):
    db1_id = await _register_database(client, admin_headers, name="COLLECTOR01")
    db2_id = await _register_database(client, admin_headers, name="COLLECTOR02")

    call_count = {"n": 0}

    def flaky_connector(params):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OracleConnectError(OracleConnectError.CATEGORY_UNKNOWN, "boom")
        return FakeConnection()

    service = CollectorService(flaky_connector)
    await service.run_once_all_active()  # must not raise even though the first db fails

    async with SessionLocal() as db:
        statuses = (await db.execute(select(CollectorStatus))).scalars().all()

    assert {s.monitored_database_id for s in statuses} == {db1_id, db2_id}
    assert sorted(s.consecutive_failures for s in statuses) == [0, 1]


async def test_collector_never_queries_blocklisted_views(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    connector = make_fake_connector()
    service = CollectorService(connector)

    await service.run_once_for_database(db_id)

    assert len(connector.created) == 1
    executed_sql = " ".join(connector.created[0].executed).upper()
    for blocked in BLOCKLISTED_VIEWS:
        assert blocked not in executed_sql
    assert connector.created[0].closed is True


async def test_collector_status_endpoint_before_and_after_a_run(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    before = await client.get(f"/api/databases/{db_id}/collector-status", headers=admin_headers)
    assert before.status_code == 200
    assert before.json() == {
        "last_run_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "consecutive_failures": 0,
        "last_error_category": None,
        "last_run_duration_ms": None,
        "last_sql_run_at": None,
        "last_sql_success_at": None,
        "last_sql_error_category": None,
        "last_capacity_run_at": None,
        "last_capacity_success_at": None,
        "last_capacity_error_category": None,
        "last_statspack_run_at": None,
        "last_statspack_success_at": None,
        "last_statspack_error_category": None,
        "last_config_run_at": None,
        "last_config_success_at": None,
        "last_config_error_category": None,
    }

    await CollectorService(make_fake_connector()).run_once_for_database(db_id)

    after = await client.get(f"/api/databases/{db_id}/collector-status", headers=admin_headers)
    body = after.json()
    assert body["last_run_at"] is not None
    assert body["last_success_at"] is not None
    assert body["consecutive_failures"] == 0
