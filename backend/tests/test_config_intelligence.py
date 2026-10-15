"""No real Oracle instance with Change Intelligence data is exercised in this suite beyond the
honest "not enough rounds yet" path - drives CollectorService's config job and the
Configuration API end-to-end against a fake connector/connection/cursor, same pattern as
test_capacity.py (this repo's "0 changes across two identical rounds" path and the
capability-probe grant check have been separately verified against a real Oracle 19c
instance - see docs/ENTERPRISE_READINESS_PLAN.md D.4's Log entry; the "a real change is
detected and classified correctly" path is proven only here, by design - see the same
Log entry).
"""

from sqlalchemy import select

from app.db import SessionLocal
from app.models.config_telemetry import ConfigParameterSample
from app.models.telemetry import CollectorStatus, SessionSample
from app.oracle.allowlist import BLOCKLISTED_VIEWS
from app.oracle.connector import OracleConnectError
from app.services.collector_service import CollectorService

DB_PAYLOAD = {
    "name": "CONFIG01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}

ROUND_1 = [("open_cursors", "300"), ("sga_target", "4000000000")]
ROUND_2_CHANGED = [("open_cursors", "500"), ("audit_trail", "DB")]  # sga_target reverted, audit_trail newly set


class FakeConfigCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, statement: str) -> None:
        self.conn.executed.append(statement)
        upper = statement.upper()
        if "V$SYSTEM_PARAMETER" in upper:
            if self.conn.query_fails:
                raise Exception("simulated: no SELECT on V$SYSTEM_PARAMETER")
            self._rows = self.conn.rows
        else:
            raise AssertionError(f"unexpected query in config test: {statement}")

    def fetchone(self):
        return None

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class FakeConfigConnection:
    def __init__(self, rows=None, query_fails=False):
        self.rows = rows if rows is not None else ROUND_1
        self.query_fails = query_fails
        self.executed: list[str] = []
        self.closed = False

    def cursor(self):
        return FakeConfigCursor(self)

    def close(self):
        self.closed = True


def make_fake_config_connector(**conn_kwargs):
    created: list[FakeConfigConnection] = []

    def _connector(_params):
        conn = FakeConfigConnection(**conn_kwargs)
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


async def test_config_collector_persists_non_default_parameters(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = CollectorService(make_fake_config_connector())

    await service.run_config_once_for_database(db_id)

    async with SessionLocal() as db:
        samples = (
            (await db.execute(select(ConfigParameterSample).where(ConfigParameterSample.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert {s.parameter_name for s in samples} == {"open_cursors", "sga_target"}
    assert status.last_config_success_at is not None
    assert status.last_config_error_category is None


async def test_config_collector_never_queries_blocklisted_views(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    connector = make_fake_config_connector()
    service = CollectorService(connector)

    await service.run_config_once_for_database(db_id)

    assert len(connector.created) == 1
    executed_sql = " ".join(connector.created[0].executed).upper()
    for blocked in BLOCKLISTED_VIEWS:
        assert blocked not in executed_sql
    assert connector.created[0].closed is True


async def test_config_probe_failure_records_error_without_persisting_anything(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = CollectorService(make_fake_config_connector(query_fails=True))

    await service.run_config_once_for_database(db_id)

    async with SessionLocal() as db:
        samples = (
            (await db.execute(select(ConfigParameterSample).where(ConfigParameterSample.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert samples == []
    assert status.last_config_error_category == "unknown_error"
    assert status.last_config_success_at is None


async def test_config_collector_connect_failure_does_not_affect_session_collection(client, admin_headers):
    """The core failure-isolation guarantee this design exists for: a config-collector-only
    failure (e.g. a missing grant) must never touch the main 15s collector's own tables."""
    db_id = await _register_database(client, admin_headers)

    from tests.test_collector import make_fake_connector

    main_collector = CollectorService(make_fake_connector())
    await main_collector.run_once_for_database(db_id)

    failing_config_collector = CollectorService(
        lambda _params: (_ for _ in ()).throw(OracleConnectError(OracleConnectError.CATEGORY_UNKNOWN, "no grant"))
    )
    await failing_config_collector.run_config_once_for_database(db_id)  # must not raise

    async with SessionLocal() as db:
        sessions = (
            (await db.execute(select(SessionSample).where(SessionSample.monitored_database_id == db_id)))
            .scalars()
            .all()
        )
        status = (
            await db.execute(select(CollectorStatus).where(CollectorStatus.monitored_database_id == db_id))
        ).scalar_one()

    assert len(sessions) == 2  # untouched by the config collector's failure
    assert status.consecutive_failures == 0  # the main collector's own failure counter, untouched
    assert status.last_config_error_category == "unknown_error"  # the config collector's own signal


# -- API -----------------------------------------------------------------------------------


async def test_configuration_is_honestly_unavailable_before_any_round(client, admin_headers):
    db_id = await _register_database(client, admin_headers)

    resp = await client.get(f"/api/databases/{db_id}/configuration", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["reason"] == "no configuration telemetry has been collected yet"


async def test_configuration_is_honestly_unavailable_after_a_single_round(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await CollectorService(make_fake_config_connector()).run_config_once_for_database(db_id)

    resp = await client.get(f"/api/databases/{db_id}/configuration", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "at least two" in body["reason"]


async def test_configuration_returns_no_changes_across_two_identical_rounds(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    connector = make_fake_config_connector()
    service = CollectorService(connector)
    await service.run_config_once_for_database(db_id)
    await service.run_config_once_for_database(db_id)

    resp = await client.get(f"/api/databases/{db_id}/configuration", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["changes"] == []


async def test_configuration_returns_classified_changes_between_two_rounds(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await CollectorService(make_fake_config_connector()).run_config_once_for_database(db_id)
    await CollectorService(make_fake_config_connector(rows=ROUND_2_CHANGED)).run_config_once_for_database(db_id)

    resp = await client.get(f"/api/databases/{db_id}/configuration", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    changes_by_name = {c["parameter_name"]: c for c in body["changes"]}
    assert changes_by_name["open_cursors"]["change_type"] == "changed"
    assert changes_by_name["open_cursors"]["previous_value"] == "300"
    assert changes_by_name["open_cursors"]["current_value"] == "500"
    assert changes_by_name["audit_trail"]["change_type"] == "newly_set"
    assert changes_by_name["sga_target"]["change_type"] == "reverted_to_default"


# -- history ---------------------------------------------------------------------------------


async def test_configuration_history_is_empty_before_two_rounds_exist(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await CollectorService(make_fake_config_connector()).run_config_once_for_database(db_id)

    resp = await client.get(f"/api/databases/{db_id}/configuration/history?hours=24", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json() == []


async def test_configuration_history_shows_a_change_and_its_later_reversion_as_two_events(client, admin_headers):
    """The real reason /configuration/history exists, distinct from /configuration: a
    parameter that changed and then reverted between two *older* rounds is invisible to
    /configuration (which only ever diffs the latest two rounds) but must show up here as
    two separate, correctly-classified events - proving history doesn't just re-expose the
    same latest-two-rounds view under a new URL."""
    db_id = await _register_database(client, admin_headers)
    service_round_1 = CollectorService(make_fake_config_connector())
    await service_round_1.run_config_once_for_database(db_id)
    await CollectorService(make_fake_config_connector(rows=ROUND_2_CHANGED)).run_config_once_for_database(db_id)
    await CollectorService(make_fake_config_connector()).run_config_once_for_database(db_id)  # back to ROUND_1

    resp = await client.get(f"/api/databases/{db_id}/configuration/history?hours=24", headers=admin_headers)

    assert resp.status_code == 200
    events = resp.json()
    open_cursors_events = [e for e in events if e["parameter_name"] == "open_cursors"]
    assert len(open_cursors_events) == 2
    # Most recent first.
    assert open_cursors_events[0]["previous_value"] == "500"
    assert open_cursors_events[0]["current_value"] == "300"
    assert open_cursors_events[1]["previous_value"] == "300"
    assert open_cursors_events[1]["current_value"] == "500"


async def test_configuration_history_respects_the_requested_window(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await CollectorService(make_fake_config_connector()).run_config_once_for_database(db_id)
    await CollectorService(make_fake_config_connector(rows=ROUND_2_CHANGED)).run_config_once_for_database(db_id)

    # A window that couldn't possibly include either real round (both just happened) - the
    # honest "nothing in this window" case, not "no telemetry at all".
    resp = await client.get(
        f"/api/databases/{db_id}/configuration/history?hours=1&until=2020-01-01T00:00:00Z",
        headers=admin_headers,
    )

    assert resp.status_code == 200
    assert resp.json() == []
