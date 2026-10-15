"""Regression suite for Enterprise Readiness Plan F.1: every one of the 7 places this
platform opens a real Oracle connection must run off the event loop, since python-oracledb's
`connect()`/cursor operations/`close()` are all synchronous. Before the fix, a slow/unreachable
database's connection attempt froze the ENTIRE application - proved live by registering
unreachable-host databases and watching an unrelated `GET /api/health` call take 3-8 seconds
instead of milliseconds (see docs/ENTERPRISE_READINESS_PLAN.md F.1's Log entry).

Each test below reproduces that exact failure mode deterministically and fast: a fake
connector sleeps for a couple of seconds (standing in for a real slow/hanging TCP connect,
never a real network call) while a background "heartbeat" coroutine repeatedly re-schedules
itself on the same event loop throughout. If the event loop is ever blocked for a real
stretch of wall-clock time - which a bare `time.sleep()` inside any of these fake connectors
genuinely does today, confirmed directly - the heartbeat's own re-scheduling stalls for that
same stretch, showing up as one large gap between consecutive heartbeat samples. This is
deliberately NOT "race the slow call against one single concurrent request": interleaving
order between two one-shot coroutines is not guaranteed (the fast one can simply finish
during the slow one's own early `await`s, before it ever reaches its blocking section,
passing even on unfixed code) - a heartbeat that keeps sampling for the slow call's entire
duration cannot miss the freeze no matter when in that duration it happens.

Reuses every existing fake connector unmodified (test_collector.py/test_sql_intelligence.py/
test_capacity.py/test_statspack.py/test_config_intelligence.py/test_oracle_capability.py) -
this suite only adds a thin sleep-then-delegate wrapper, never new fake Oracle-shaped data.
"""

import asyncio
import time

from app.main import app
from app.oracle.connector import get_oracle_connector
from app.services.collector_service import CollectorService

from tests.test_capacity import make_fake_capacity_connector
from tests.test_collector import make_fake_connector as make_fake_main_connector
from tests.test_config_intelligence import make_fake_config_connector
from tests.test_oracle_capability import make_fake_connector as make_fake_capability_connector
from tests.test_sql_intelligence import make_fake_sql_connector
from tests.test_statspack import _mark_statspack_available, make_fake_statspack_connector

# Long enough that a blocked event loop (one heartbeat gap ~= DELAY_SECONDS) is never
# confused with normal scheduling jitter (a few ms), short enough that 7 of these don't make
# the suite slow. Real bug reproduced with a real 5s TCP timeout; this is a faithful stand-in.
DELAY_SECONDS = 2.0
HEARTBEAT_INTERVAL_SECONDS = 0.05
# A gap this much larger than the heartbeat's own interval can only mean the event loop went
# unresponsive for a real stretch of wall-clock time - generous enough to never be flaky,
# tight enough that it could only pass if the blocking call genuinely ran off-loop.
MAX_HEARTBEAT_GAP_SECONDS = 0.5


def make_slow_connector(delay_seconds, wrapped_connector):
    """Wraps any existing fake connector so opening the connection takes `delay_seconds` -
    simulating a slow/hanging real TCP connect without a real network. `time.sleep`, not
    `asyncio.sleep`: the whole point is that this fake connector is exactly as blocking/
    synchronous as the real `oracledb.connect()` it stands in for."""

    def _connector(params):
        time.sleep(delay_seconds)
        return wrapped_connector(params)

    _connector.created = wrapped_connector.created
    return _connector


async def _max_heartbeat_gap_during(coro) -> float:
    """Runs `coro` to completion while a background heartbeat repeatedly re-schedules itself
    on the same event loop; returns the largest gap ever observed between two consecutive
    heartbeats. A blocked event loop shows up as one large gap, no matter when during
    `coro`'s lifetime the block happens."""
    gaps: list[float] = []
    stop = False

    async def _heartbeat() -> None:
        last = time.perf_counter()
        while not stop:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    heartbeat_task = asyncio.ensure_future(_heartbeat())
    try:
        await coro
    finally:
        stop = True
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

    return max(gaps) if gaps else 0.0


async def _register(client, admin_headers, payload: dict) -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_main_collector_slow_connect_does_not_block_event_loop(client, admin_headers):
    db_id = await _register(
        client, admin_headers,
        {"name": "CONC_MAIN01", "host": "h", "port": 1521, "service_name": "s", "edition_hint": "SE2",
         "username": "u", "password": "SuperSecretOraclePassword!"},
    )
    service = CollectorService(make_slow_connector(DELAY_SECONDS, make_fake_main_connector()))

    max_gap = await _max_heartbeat_gap_during(service.run_once_for_database(db_id))

    assert max_gap < MAX_HEARTBEAT_GAP_SECONDS, (
        f"event loop was unresponsive for {max_gap:.2f}s while the main collector held a "
        "slow connection - every other request would have frozen too"
    )


async def test_sql_collector_slow_connect_does_not_block_event_loop(client, admin_headers):
    db_id = await _register(
        client, admin_headers,
        {"name": "CONC_SQL01", "host": "h", "port": 1521, "service_name": "s", "edition_hint": "SE2",
         "username": "u", "password": "SuperSecretOraclePassword!"},
    )
    service = CollectorService(make_slow_connector(DELAY_SECONDS, make_fake_sql_connector()))

    max_gap = await _max_heartbeat_gap_during(service.run_sql_once_for_database(db_id, top_n=50))

    assert max_gap < MAX_HEARTBEAT_GAP_SECONDS, (
        f"event loop was unresponsive for {max_gap:.2f}s while the SQL collector held a "
        "slow connection - every other request would have frozen too"
    )


async def test_capacity_collector_slow_connect_does_not_block_event_loop(client, admin_headers):
    db_id = await _register(
        client, admin_headers,
        {"name": "CONC_CAP01", "host": "h", "port": 1521, "service_name": "s", "edition_hint": "SE2",
         "username": "u", "password": "SuperSecretOraclePassword!"},
    )
    service = CollectorService(make_slow_connector(DELAY_SECONDS, make_fake_capacity_connector()))

    max_gap = await _max_heartbeat_gap_during(service.run_capacity_once_for_database(db_id))

    assert max_gap < MAX_HEARTBEAT_GAP_SECONDS, (
        f"event loop was unresponsive for {max_gap:.2f}s while the capacity collector held a "
        "slow connection - every other request would have frozen too"
    )


async def test_statspack_collector_slow_connect_does_not_block_event_loop(client, admin_headers):
    db_id = await _register(
        client, admin_headers,
        {"name": "CONC_STATSPACK01", "host": "h", "port": 1521, "service_name": "s", "edition_hint": "SE2",
         "username": "u", "password": "SuperSecretOraclePassword!"},
    )
    await _mark_statspack_available(db_id)
    service = CollectorService(make_slow_connector(DELAY_SECONDS, make_fake_statspack_connector()))

    max_gap = await _max_heartbeat_gap_during(
        service.run_statspack_once_for_database(db_id, max_snapshots=12)
    )

    assert max_gap < MAX_HEARTBEAT_GAP_SECONDS, (
        f"event loop was unresponsive for {max_gap:.2f}s while the Statspack collector held a "
        "slow connection - every other request would have frozen too"
    )


async def test_config_collector_slow_connect_does_not_block_event_loop(client, admin_headers):
    db_id = await _register(
        client, admin_headers,
        {"name": "CONC_CONFIG01", "host": "h", "port": 1521, "service_name": "s", "edition_hint": "SE2",
         "username": "u", "password": "SuperSecretOraclePassword!"},
    )
    service = CollectorService(make_slow_connector(DELAY_SECONDS, make_fake_config_connector()))

    max_gap = await _max_heartbeat_gap_during(service.run_config_once_for_database(db_id))

    assert max_gap < MAX_HEARTBEAT_GAP_SECONDS, (
        f"event loop was unresponsive for {max_gap:.2f}s while the config collector held a "
        "slow connection - every other request would have frozen too"
    )


async def test_test_connection_route_slow_connect_does_not_block_event_loop(client, admin_headers):
    db_id = await _register(
        client, admin_headers,
        {"name": "CONC_TESTCONN01", "host": "h", "port": 1521, "service_name": "s", "edition_hint": "SE2",
         "username": "u", "password": "SuperSecretOraclePassword!"},
    )
    connector = make_slow_connector(DELAY_SECONDS, make_fake_capability_connector())
    app.dependency_overrides[get_oracle_connector] = lambda: connector
    try:
        max_gap = await _max_heartbeat_gap_during(
            client.post(f"/api/databases/{db_id}/test-connection", headers=admin_headers)
        )
    finally:
        app.dependency_overrides.pop(get_oracle_connector, None)

    assert max_gap < MAX_HEARTBEAT_GAP_SECONDS, (
        f"event loop was unresponsive for {max_gap:.2f}s while Test Connection held a slow "
        "connection - every other request would have frozen too"
    )


async def test_detect_capabilities_route_slow_connect_does_not_block_event_loop(client, admin_headers):
    db_id = await _register(
        client, admin_headers,
        {"name": "CONC_DETECTCAP01", "host": "h", "port": 1521, "service_name": "s", "edition_hint": "SE2",
         "username": "u", "password": "SuperSecretOraclePassword!"},
    )
    connector = make_slow_connector(DELAY_SECONDS, make_fake_capability_connector())
    app.dependency_overrides[get_oracle_connector] = lambda: connector
    try:
        max_gap = await _max_heartbeat_gap_during(
            client.post(f"/api/databases/{db_id}/detect-capabilities", headers=admin_headers)
        )
    finally:
        app.dependency_overrides.pop(get_oracle_connector, None)

    assert max_gap < MAX_HEARTBEAT_GAP_SECONDS, (
        f"event loop was unresponsive for {max_gap:.2f}s while Detect Capabilities held a "
        "slow connection - every other request would have frozen too"
    )
