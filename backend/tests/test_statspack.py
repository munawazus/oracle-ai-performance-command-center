"""No real Oracle instance has Statspack installed in this environment (confirmed live
against orclpdb during design - see docs/ENTERPRISE_READINESS_PLAN.md D.3's Log entry) -
drives CollectorService's Statspack sync job and the Statspack API end-to-end against a fake
connector/connection/cursor, same pattern as test_capacity.py/test_sql_intelligence.py. Only
the honest "Statspack not installed" detection path is verified live (test_oracle_capability.py
already covers that via capability_probe.py, unmodified by this file).
"""

import re
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models.statspack_telemetry import StatspackSnapshot, StatspackSqlSample, StatspackSyncState
from app.models.telemetry import CollectorStatus, SessionSample
from app.oracle.allowlist import BLOCKLISTED_VIEWS
from app.oracle.connector import OracleConnectError
from app.repositories.database_repo import CapabilityRepository
from app.services.collector_service import CollectorService

DB_PAYLOAD = {
    "name": "STATSPACK01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}

SNAP_1 = (101, datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc))
SNAP_2 = (102, datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc))
ALL_SNAPSHOTS = [SNAP_1, SNAP_2]

# (snap_id, event, wait_class, total_waits, time_waited_micro)
WAIT_EVENTS_BY_SNAP = {
    101: [(101, "db file sequential read", "User I/O", 100, 500_000)],
    102: [(102, "db file sequential read", "User I/O", 150, 900_000)],
}
# (snap_id, sql_id, plan_hash_value, executions, elapsed_time, cpu_time, buffer_gets, disk_reads,
#  rows_processed, parse_calls)
SQL_BY_SNAP = {
    101: [(101, "abc123sqlid", 111, 10, 100_000, 80_000, 500, 5, 10, 10)],
    102: [(102, "abc123sqlid", 111, 20, 250_000, 200_000, 900, 9, 20, 20)],
}


def _extract_since(upper: str) -> int | None:
    match = re.search(r"SNAP_ID > (\d+)", upper)
    return int(match.group(1)) if match else None


def _extract_snap_ids(upper: str) -> list[int]:
    match = re.search(r"SNAP_ID IN \(([\d,]+)\)", upper)
    return [int(x) for x in match.group(1).split(",")] if match else []


class FakeStatspackCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, statement: str) -> None:
        self.conn.executed.append(statement)
        upper = statement.upper()
        if "STATS$SNAPSHOT" in upper:
            if self.conn.snapshot_query_fails:
                raise Exception("simulated: no SELECT on PERFSTAT.STATS$SNAPSHOT")
            since = _extract_since(upper)
            self._rows = [r for r in self.conn.all_snapshots if since is None or r[0] > since]
        elif "STATS$SYSTEM_EVENT" in upper:
            if self.conn.wait_event_query_fails:
                raise Exception("simulated: no SELECT on PERFSTAT.STATS$SYSTEM_EVENT")
            snap_ids = _extract_snap_ids(upper)
            self._rows = [row for sid in snap_ids for row in self.conn.wait_events_by_snap.get(sid, [])]
        elif "STATS$SQL_SUMMARY" in upper:
            if self.conn.sql_query_fails:
                raise Exception("simulated: no SELECT on PERFSTAT.STATS$SQL_SUMMARY")
            snap_ids = _extract_snap_ids(upper)
            self._rows = [row for sid in snap_ids for row in self.conn.sql_by_snap.get(sid, [])]
        else:
            raise AssertionError(f"unexpected query in Statspack test: {statement}")

    def fetchone(self):
        return None

    def fetchall(self):
        return self._rows


class FakeStatspackConnection:
    def __init__(
        self,
        all_snapshots=None,
        wait_events_by_snap=None,
        sql_by_snap=None,
        snapshot_query_fails=False,
        wait_event_query_fails=False,
        sql_query_fails=False,
    ):
        self.all_snapshots = all_snapshots if all_snapshots is not None else ALL_SNAPSHOTS
        self.wait_events_by_snap = wait_events_by_snap if wait_events_by_snap is not None else WAIT_EVENTS_BY_SNAP
        self.sql_by_snap = sql_by_snap if sql_by_snap is not None else SQL_BY_SNAP
        self.snapshot_query_fails = snapshot_query_fails
        self.wait_event_query_fails = wait_event_query_fails
        self.sql_query_fails = sql_query_fails
        self.executed: list[str] = []
        self.closed = False

    def cursor(self):
        return FakeStatspackCursor(self)

    def close(self):
        self.closed = True


def make_fake_statspack_connector(**conn_kwargs):
    created: list[FakeStatspackConnection] = []

    def _connector(_params):
        conn = FakeStatspackConnection(**conn_kwargs)
        created.append(conn)
        return conn

    _connector.created = created
    return _connector


async def _register_database(client, admin_headers, name: str = DB_PAYLOAD["name"]) -> str:
    payload = dict(DB_PAYLOAD, name=name)
    resp = await client.post("/api/databases", headers=admin_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _mark_statspack_available(db_id: str, *, available: bool = True) -> None:
    async with SessionLocal() as db:
        await CapabilityRepository(db).upsert(
            monitored_database_id=db_id,
            detected_version="19.0.0.0.0",
            detected_edition="EE",
            is_rac=False,
            statspack_available=available,
            privilege_report={},
        )
        await db.commit()


# -- collector -----------------------------------------------------------------------------


async def test_statspack_collector_skips_entirely_when_capability_never_probed(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    connector = make_fake_statspack_connector()
    service = CollectorService(connector)

    await service.run_statspack_once_for_database(db_id, max_snapshots=12)

    assert connector.created == []  # zero Oracle connections opened


async def test_statspack_collector_skips_entirely_when_statspack_not_available(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _mark_statspack_available(db_id, available=False)
    connector = make_fake_statspack_connector()
    service = CollectorService(connector)

    await service.run_statspack_once_for_database(db_id, max_snapshots=12)

    assert connector.created == []


async def test_statspack_collector_syncs_new_snapshots_wait_events_and_sql(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _mark_statspack_available(db_id)
    service = CollectorService(make_fake_statspack_connector())

    await service.run_statspack_once_for_database(db_id, max_snapshots=12)

    async with SessionLocal() as db:
        snapshots = (
            (await db.execute(select(StatspackSnapshot).where(StatspackSnapshot.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        sql_samples = (
            (await db.execute(select(StatspackSqlSample).where(StatspackSqlSample.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        sync_state = (
            await db.execute(select(StatspackSyncState).where(StatspackSyncState.monitored_database_id == db_id))
        ).scalar_one()
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert {s.snap_id for s in snapshots} == {101, 102}
    assert {s.sql_id for s in sql_samples} == {"abc123sqlid"}
    assert sync_state.last_synced_snap_id == 102
    assert status.last_statspack_success_at is not None
    assert status.last_statspack_error_category is None


async def test_statspack_collector_never_queries_blocklisted_views(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _mark_statspack_available(db_id)
    connector = make_fake_statspack_connector()
    service = CollectorService(connector)

    await service.run_statspack_once_for_database(db_id, max_snapshots=12)

    assert len(connector.created) == 1
    executed_sql = " ".join(connector.created[0].executed).upper()
    for blocked in BLOCKLISTED_VIEWS:
        assert blocked not in executed_sql
    assert connector.created[0].closed is True


async def test_statspack_collector_incremental_sync_uses_the_high_water_mark(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _mark_statspack_available(db_id)

    # Round 1: only snap 101 exists yet.
    await CollectorService(
        make_fake_statspack_connector(all_snapshots=[SNAP_1], wait_events_by_snap={101: WAIT_EVENTS_BY_SNAP[101]},
                                       sql_by_snap={101: SQL_BY_SNAP[101]})
    ).run_statspack_once_for_database(db_id, max_snapshots=12)

    async with SessionLocal() as db:
        sync_state = (
            await db.execute(select(StatspackSyncState).where(StatspackSyncState.monitored_database_id == db_id))
        ).scalar_one()
    assert sync_state.last_synced_snap_id == 101

    # Round 2: snap 102 has now appeared - the sync must only pull the new one.
    connector2 = make_fake_statspack_connector()
    await CollectorService(connector2).run_statspack_once_for_database(db_id, max_snapshots=12)

    async with SessionLocal() as db:
        snapshots = (
            (await db.execute(select(StatspackSnapshot).where(StatspackSnapshot.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
    assert {s.snap_id for s in snapshots} == {101, 102}  # 101 from round 1, 102 newly synced
    executed_sql = " ".join(connector2.created[0].executed).upper()
    assert "SNAP_ID > 101" in executed_sql  # confirms the high-water-mark was actually used


async def test_wait_event_probe_failure_still_lets_snapshots_and_sql_persist(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _mark_statspack_available(db_id)
    service = CollectorService(make_fake_statspack_connector(wait_event_query_fails=True))

    await service.run_statspack_once_for_database(db_id, max_snapshots=12)

    async with SessionLocal() as db:
        snapshots = (
            (await db.execute(select(StatspackSnapshot).where(StatspackSnapshot.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert len(snapshots) == 2  # snapshots persisted despite the missing STATS$SYSTEM_EVENT grant
    assert status.last_statspack_success_at is not None  # still counted a success - partial capability


async def test_sql_summary_probe_failure_still_lets_snapshots_and_wait_events_persist(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _mark_statspack_available(db_id)
    service = CollectorService(make_fake_statspack_connector(sql_query_fails=True))

    await service.run_statspack_once_for_database(db_id, max_snapshots=12)

    async with SessionLocal() as db:
        snapshots = (
            (await db.execute(select(StatspackSnapshot).where(StatspackSnapshot.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        sql_samples = (
            (await db.execute(select(StatspackSqlSample).where(StatspackSqlSample.monitored_database_id == db_id)))
            .scalars()
            .all()
        )

    assert len(snapshots) == 2
    assert sql_samples == []


async def test_snapshot_probe_failure_records_error_without_persisting_anything(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _mark_statspack_available(db_id)
    service = CollectorService(make_fake_statspack_connector(snapshot_query_fails=True))

    await service.run_statspack_once_for_database(db_id, max_snapshots=12)

    async with SessionLocal() as db:
        snapshots = (
            (await db.execute(select(StatspackSnapshot).where(StatspackSnapshot.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert snapshots == []
    assert status.last_statspack_error_category == "unknown_error"
    assert status.last_statspack_success_at is None


async def test_statspack_collector_connect_failure_does_not_affect_session_collection(client, admin_headers):
    """The core failure-isolation guarantee this design exists for: a Statspack-sync-only
    failure (e.g. a missing grant) must never touch the main 15s collector's own tables."""
    db_id = await _register_database(client, admin_headers)
    await _mark_statspack_available(db_id)

    from tests.test_collector import make_fake_connector

    main_collector = CollectorService(make_fake_connector())
    await main_collector.run_once_for_database(db_id)

    failing_statspack_collector = CollectorService(
        lambda _params: (_ for _ in ()).throw(OracleConnectError(OracleConnectError.CATEGORY_UNKNOWN, "no grant"))
    )
    await failing_statspack_collector.run_statspack_once_for_database(db_id, max_snapshots=12)  # must not raise

    async with SessionLocal() as db:
        sessions = (
            (await db.execute(select(SessionSample).where(SessionSample.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert len(sessions) == 2  # untouched by the Statspack sync's failure
    assert status.consecutive_failures == 0  # the main collector's own failure counter, untouched
    assert status.last_statspack_error_category == "unknown_error"  # the Statspack sync's own signal


# -- API -----------------------------------------------------------------------------------


async def test_statspack_summary_is_honestly_unavailable_when_not_installed(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    resp = await client.get(f"/api/databases/{db_id}/statspack", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "not installed" in body["reason"]


async def test_statspack_summary_is_honestly_unavailable_before_any_sync(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _mark_statspack_available(db_id)

    resp = await client.get(f"/api/databases/{db_id}/statspack", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "no snapshots" in body["reason"]


async def test_statspack_summary_is_honestly_unavailable_with_only_one_synced_snapshot(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _mark_statspack_available(db_id)
    await CollectorService(
        make_fake_statspack_connector(all_snapshots=[SNAP_1], wait_events_by_snap={101: WAIT_EVENTS_BY_SNAP[101]},
                                       sql_by_snap={101: SQL_BY_SNAP[101]})
    ).run_statspack_once_for_database(db_id, max_snapshots=12)

    resp = await client.get(f"/api/databases/{db_id}/statspack", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "only one" in body["reason"]


async def test_statspack_summary_returns_real_delta_after_two_synced_snapshots(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _mark_statspack_available(db_id)
    connector = make_fake_statspack_connector()
    await CollectorService(connector).run_statspack_once_for_database(db_id, max_snapshots=12)
    connections_before = len(connector.created)

    resp = await client.get(f"/api/databases/{db_id}/statspack", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["from_snapshot"]["snap_id"] == 101
    assert body["to_snapshot"]["snap_id"] == 102
    assert body["elapsed_hours"] == 1.0
    assert body["wait_classes"][0]["wait_class"] == "User I/O"
    assert body["top_sql"][0]["sql_id"] == "abc123sqlid"
    assert body["top_sql"][0]["executions_delta"] == 10
    assert len(connector.created) == connections_before  # the read never opened a new Oracle connection
