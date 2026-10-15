"""No real Oracle instance is available for this suite - drives CollectorService's capacity
job and the Capacity API end-to-end against a fake connector/connection/cursor, same pattern
as test_sql_intelligence.py (this repo's capacity collector work has been separately verified
against a real Oracle 19c instance - see docs/ENTERPRISE_READINESS_PLAN.md D.2's Log entry).
"""

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models.capacity_telemetry import TablespaceSample
from app.models.telemetry import CollectorStatus, SessionSample
from app.oracle.allowlist import BLOCKLISTED_VIEWS
from app.oracle.connector import OracleConnectError
from app.services.capacity_alert_service import CapacityAlertService
from app.services.collector_service import CollectorService

DB_PAYLOAD = {
    "name": "CAPACITY01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}

_UNLIMITED_THRESHOLD = 100 * 1024**4

# (tablespace_name, contents, status, allocated_bytes, max_bytes, unconstrained_file_count, file_count, free_bytes)
PERMANENT_ROWS_ROUND_1 = [
    ("SYSTEM", "PERMANENT", "ONLINE", 1000, 34_359_721_984, 0, 1, 500),  # used = 500
    ("UNDOTBS1", "UNDO", "ONLINE", 2000, 34_359_721_984, 0, 1, 1900),  # used = 100
]
PERMANENT_ROWS_ROUND_2 = [
    ("SYSTEM", "PERMANENT", "ONLINE", 1000, 34_359_721_984, 0, 1, 300),  # used = 700, grew
    ("UNDOTBS1", "UNDO", "ONLINE", 2000, 34_359_721_984, 0, 1, 1900),
]
# (tablespace_name, contents, status, allocated_bytes, max_bytes, unconstrained_file_count, file_count, used_bytes)
TEMP_ROWS = [
    ("TEMP", "TEMPORARY", "ONLINE", 5000, 34_359_721_984, 0, 1, 2500),
]
UNLIMITED_PERMANENT_ROWS = [
    ("USERS", "PERMANENT", "ONLINE", 1000, _UNLIMITED_THRESHOLD, 1, 1, 900),
]


class FakeCapacityCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, statement: str) -> None:
        self.conn.executed.append(statement)
        upper = statement.upper()
        if "DBA_FREE_SPACE" in upper:
            if self.conn.permanent_query_fails:
                raise Exception("simulated: no SELECT on DBA_FREE_SPACE")
            self._rows = self.conn.permanent_rows
        elif "V$TEMP_SPACE_HEADER" in upper:
            if self.conn.temp_query_fails:
                raise Exception("simulated: no SELECT on V$TEMP_SPACE_HEADER")
            self._rows = self.conn.temp_rows
        else:
            raise AssertionError(f"unexpected query in capacity test: {statement}")

    def fetchone(self):
        return None

    def fetchall(self):
        return self._rows


class FakeCapacityConnection:
    def __init__(self, permanent_rows=None, temp_rows=None, permanent_query_fails=False, temp_query_fails=False):
        self.permanent_rows = permanent_rows if permanent_rows is not None else PERMANENT_ROWS_ROUND_1
        self.temp_rows = temp_rows if temp_rows is not None else TEMP_ROWS
        self.permanent_query_fails = permanent_query_fails
        self.temp_query_fails = temp_query_fails
        self.executed: list[str] = []
        self.closed = False

    def cursor(self):
        return FakeCapacityCursor(self)

    def close(self):
        self.closed = True


def make_fake_capacity_connector(**conn_kwargs):
    created: list[FakeCapacityConnection] = []

    def _connector(_params):
        conn = FakeCapacityConnection(**conn_kwargs)
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


async def test_capacity_collector_persists_permanent_undo_and_temp_tablespaces(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = CollectorService(make_fake_capacity_connector())

    await service.run_capacity_once_for_database(db_id)

    async with SessionLocal() as db:
        samples = (
            (await db.execute(select(TablespaceSample).where(TablespaceSample.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert {s.tablespace_name for s in samples} == {"SYSTEM", "UNDOTBS1", "TEMP"}
    system = next(s for s in samples if s.tablespace_name == "SYSTEM")
    assert system.contents == "PERMANENT"
    assert system.allocated_bytes == 1000
    assert system.used_bytes == 500  # allocated - free
    temp = next(s for s in samples if s.tablespace_name == "TEMP")
    assert temp.contents == "TEMPORARY"
    assert temp.used_bytes == 2500  # taken directly from V$TEMP_SPACE_HEADER, no subtraction
    assert status.last_capacity_success_at is not None
    assert status.last_capacity_error_category is None


async def test_capacity_collector_never_queries_blocklisted_views(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    connector = make_fake_capacity_connector()
    service = CollectorService(connector)

    await service.run_capacity_once_for_database(db_id)

    assert len(connector.created) == 1
    executed_sql = " ".join(connector.created[0].executed).upper()
    for blocked in BLOCKLISTED_VIEWS:
        assert blocked not in executed_sql
    assert connector.created[0].closed is True


async def test_capacity_reports_no_known_ceiling_for_unconstrained_autoextend(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = CollectorService(make_fake_capacity_connector(permanent_rows=UNLIMITED_PERMANENT_ROWS))

    await service.run_capacity_once_for_database(db_id)

    async with SessionLocal() as db:
        sample = (
            await db.execute(select(TablespaceSample).where(TablespaceSample.tablespace_name == "USERS"))
        ).scalar_one()
    assert sample.max_bytes is None  # honestly "no known ceiling", never the raw 100TB sentinel


async def test_temp_probe_failure_still_lets_permanent_and_undo_persist(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = CollectorService(make_fake_capacity_connector(temp_query_fails=True))

    await service.run_capacity_once_for_database(db_id)

    async with SessionLocal() as db:
        samples = (
            (await db.execute(select(TablespaceSample).where(TablespaceSample.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert {s.tablespace_name for s in samples} == {"SYSTEM", "UNDOTBS1"}  # TEMP missing, rest persisted
    assert status.last_capacity_success_at is not None  # still counted a success - partial capability


async def test_permanent_probe_failure_records_error_without_persisting_anything(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = CollectorService(make_fake_capacity_connector(permanent_query_fails=True))

    await service.run_capacity_once_for_database(db_id)

    async with SessionLocal() as db:
        samples = (
            (await db.execute(select(TablespaceSample).where(TablespaceSample.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert samples == []
    assert status.last_capacity_error_category == "unknown_error"
    assert status.last_capacity_success_at is None


async def test_capacity_collector_connect_failure_does_not_affect_session_collection(client, admin_headers):
    """The core failure-isolation guarantee this design exists for: a capacity-collector-only
    failure (e.g. a missing grant) must never touch the main 15s collector's own tables."""
    db_id = await _register_database(client, admin_headers)

    from tests.test_collector import make_fake_connector

    main_collector = CollectorService(make_fake_connector())
    await main_collector.run_once_for_database(db_id)

    failing_capacity_collector = CollectorService(
        lambda _params: (_ for _ in ()).throw(OracleConnectError(OracleConnectError.CATEGORY_UNKNOWN, "no grant"))
    )
    await failing_capacity_collector.run_capacity_once_for_database(db_id)  # must not raise

    async with SessionLocal() as db:
        sessions = (
            (await db.execute(select(SessionSample).where(SessionSample.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert len(sessions) == 2  # untouched by the capacity collector's failure
    assert status.consecutive_failures == 0  # the main collector's own failure counter, untouched
    assert status.last_capacity_error_category == "unknown_error"  # the capacity collector's own signal


# -- API -----------------------------------------------------------------------------------


async def test_capacity_overview_is_honestly_unavailable_before_any_round(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    resp = await client.get(f"/api/databases/{db_id}/capacity", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["rows"] == []


async def test_capacity_overview_is_available_after_a_single_round(client, admin_headers):
    """Unlike top-sql, capacity's current-state view needs only one round - %used is a
    snapshot fact, not a delta between cumulative counters."""
    db_id = await _register_database(client, admin_headers)
    await CollectorService(make_fake_capacity_connector()).run_capacity_once_for_database(db_id)

    resp = await client.get(f"/api/databases/{db_id}/capacity", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    system = next(r for r in body["rows"] if r["tablespace_name"] == "SYSTEM")
    assert system["pct_used"] == 50.0
    assert system["growth_available"] is False  # one round is not enough for a trend yet


async def test_capacity_overview_growth_trend_after_enough_rounds(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    for _ in range(3):
        await CollectorService(make_fake_capacity_connector(permanent_rows=PERMANENT_ROWS_ROUND_2)).run_capacity_once_for_database(
            db_id
        )

    resp = await client.get(f"/api/databases/{db_id}/capacity", headers=admin_headers)

    assert resp.status_code == 200
    system = next(r for r in resp.json()["rows"] if r["tablespace_name"] == "SYSTEM")
    assert system["growth_available"] is True


async def test_tablespace_detail_is_honestly_unavailable_for_an_unknown_tablespace(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    resp = await client.get(f"/api/databases/{db_id}/capacity/NOSUCHTS", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["available"] is False


async def test_tablespace_detail_returns_usage_and_history(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    connector = make_fake_capacity_connector()
    await CollectorService(connector).run_capacity_once_for_database(db_id)
    connections_before = len(connector.created)

    resp = await client.get(f"/api/databases/{db_id}/capacity/SYSTEM", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["usage"]["pct_used"] == 50.0
    assert len(body["history"]) == 1
    assert len(connector.created) == connections_before  # the read never opened a new Oracle connection


async def test_capacity_alerts_route_is_empty_before_any_detection(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    resp = await client.get(f"/api/databases/{db_id}/capacity-alerts", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json() == []


# -- per-database alert thresholds ----------------------------------------------------------


async def test_capacity_thresholds_before_any_override_shows_only_the_global_defaults(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    settings = get_settings()

    resp = await client.get(f"/api/databases/{db_id}/capacity-thresholds", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["warn_pct_used"] is None
    assert body["critical_pct_used"] is None
    assert body["warn_days_to_full"] is None
    assert body["critical_days_to_full"] is None
    assert body["default_warn_pct_used"] == settings.capacity_warn_pct_used
    assert body["default_critical_pct_used"] == settings.capacity_critical_pct_used
    assert body["default_warn_days_to_full"] == settings.capacity_warn_days_to_full
    assert body["default_critical_days_to_full"] == settings.capacity_critical_days_to_full


async def test_put_capacity_thresholds_persists_and_round_trips(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    put_resp = await client.put(
        f"/api/databases/{db_id}/capacity-thresholds",
        headers=admin_headers,
        json={
            "warn_pct_used": 70.0,
            "critical_pct_used": 90.0,
            "warn_days_to_full": 14.0,
            "critical_days_to_full": 3.0,
        },
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["warn_pct_used"] == 70.0

    get_resp = await client.get(f"/api/databases/{db_id}/capacity-thresholds", headers=admin_headers)
    body = get_resp.json()
    assert body["warn_pct_used"] == 70.0
    assert body["critical_pct_used"] == 90.0
    assert body["warn_days_to_full"] == 14.0
    assert body["critical_days_to_full"] == 3.0


async def test_put_capacity_thresholds_can_clear_an_override_back_to_the_default(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await client.put(
        f"/api/databases/{db_id}/capacity-thresholds", headers=admin_headers, json={"warn_pct_used": 70.0}
    )

    # PUT (not PATCH) semantics - omitted fields are null, and null is applied, not skipped.
    clear_resp = await client.put(f"/api/databases/{db_id}/capacity-thresholds", headers=admin_headers, json={})
    assert clear_resp.status_code == 200
    assert clear_resp.json()["warn_pct_used"] is None

    get_resp = await client.get(f"/api/databases/{db_id}/capacity-thresholds", headers=admin_headers)
    assert get_resp.json()["warn_pct_used"] is None


async def test_put_capacity_thresholds_rejects_an_inverted_pct_pair(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    resp = await client.put(
        f"/api/databases/{db_id}/capacity-thresholds",
        headers=admin_headers,
        json={"warn_pct_used": 90.0, "critical_pct_used": 80.0},
    )

    assert resp.status_code == 422


async def test_put_capacity_thresholds_rejects_an_inverted_days_pair(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    resp = await client.put(
        f"/api/databases/{db_id}/capacity-thresholds",
        headers=admin_headers,
        json={"warn_days_to_full": 5.0, "critical_days_to_full": 10.0},
    )

    assert resp.status_code == 422


async def test_viewer_can_read_thresholds_but_not_set_them(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "email": "capacityviewer@ocpcc.dev",
            "password": "ViewerPassw0rd!123",
            "display_name": "Capacity Viewer",
            "roles": ["VIEWER"],
        },
    )
    login = await client.post(
        "/api/auth/login", json={"email": "capacityviewer@ocpcc.dev", "password": "ViewerPassw0rd!123"}
    )
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    read_resp = await client.get(f"/api/databases/{db_id}/capacity-thresholds", headers=viewer_headers)
    assert read_resp.status_code == 200

    write_resp = await client.put(
        f"/api/databases/{db_id}/capacity-thresholds", headers=viewer_headers, json={"warn_pct_used": 1.0}
    )
    assert write_resp.status_code == 403


async def test_capacity_alert_service_uses_a_per_database_threshold_override(client, admin_headers):
    """USERS (UNLIMITED_PERMANENT_ROWS) sits at a real, honest 10% used with no known max size
    (unconstrained autoextend) - well under the global 85% warn threshold, so the fleet-wide
    default would never flag it. A 5% override should make this specific database flag it
    anyway - proving the override actually reaches detection, not just that the API accepts
    and stores a number nobody reads. Deliberately uses a no-known-max-size fixture so this
    test exercises the override alone, not the separate max-size-vs-allocated preference
    question (covered by test_capacity_alerts.py's own dedicated tests)."""
    db_id = await _register_database(client, admin_headers)
    await CollectorService(
        make_fake_capacity_connector(permanent_rows=UNLIMITED_PERMANENT_ROWS)
    ).run_capacity_once_for_database(db_id)

    await CapacityAlertService().run_once_for_database(db_id)
    default_alerts = await client.get(f"/api/databases/{db_id}/capacity-alerts", headers=admin_headers)
    assert default_alerts.json() == []  # 10% real usage never crosses the 85% global default

    await client.put(
        f"/api/databases/{db_id}/capacity-thresholds", headers=admin_headers, json={"warn_pct_used": 5.0}
    )
    await CapacityAlertService().run_once_for_database(db_id)

    override_alerts = await client.get(f"/api/databases/{db_id}/capacity-alerts", headers=admin_headers)
    findings = override_alerts.json()
    system_finding = next(f for f in findings if f["tablespace_name"] == "USERS")
    assert system_finding["alert_kind"] == "PCT_USED"
    assert system_finding["severity"] == "HIGH"
