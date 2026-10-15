from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models.base import utcnow
from app.oracle.telemetry_probe import InstanceHealthSample, SessionSampleRow, WaitSnapshotRow
from app.repositories.analytics_repo import BaselineRepository
from app.repositories.telemetry_repo import TelemetryRepository

DB_PAYLOAD = {
    "name": "DASHBOARD01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _register_database(client, admin_headers) -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_sessions_endpoint_before_any_telemetry_is_honest(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    resp = await client.get(f"/api/databases/{db_id}/sessions", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == {
        "available": False,
        "reason": "no session telemetry collected yet",
        "sampled_at": None,
        "session_count": 0,
        "sessions": [],
    }


async def test_sessions_endpoint_returns_latest_round(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_session_samples(
            db_id, BASE_TIME, [SessionSampleRow(101, 1, "OLD", None, None, None, "ACTIVE", None, None, None, None, None)]
        )
        await repo.insert_session_samples(
            db_id,
            BASE_TIME + timedelta(minutes=1),
            [SessionSampleRow(202, 2, "NEW", "sql1", "db file sequential read", "User I/O", "ACTIVE", None, "host1", "sqlplus", None, None)],
        )
        await db.commit()

    resp = await client.get(f"/api/databases/{db_id}/sessions", headers=admin_headers)
    body = resp.json()
    assert body["available"] is True
    assert body["session_count"] == 1
    assert body["sessions"][0]["username"] == "NEW"


async def test_sessions_endpoint_returns_a_real_state_value(client, admin_headers):
    """Finding 7 (docs/QA_LIVE_FINDINGS.md): a session can be genuinely on CPU right now while
    its stale event/wait_class still show its last (sub-second) wait - only V$SESSION.STATE
    distinguishes the two. Confirms `state` round-trips end to end, not just that the field
    exists."""
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_session_samples(
            db_id,
            BASE_TIME,
            [
                SessionSampleRow(
                    sid=303,
                    serial_num=3,
                    username="APP_USER",
                    sql_id=None,
                    event="SQL*Net message from client",
                    wait_class="Idle",
                    status="ACTIVE",
                    blocking_session=None,
                    machine="host1",
                    program="python",
                    module=None,
                    action=None,
                    state="WAITED SHORT TIME",
                )
            ],
        )
        await db.commit()

    resp = await client.get(f"/api/databases/{db_id}/sessions", headers=admin_headers)
    body = resp.json()
    assert body["sessions"][0]["state"] == "WAITED SHORT TIME"


async def test_sessions_endpoint_returns_a_real_last_call_et_value(client, admin_headers):
    """A session waiting 2 seconds and one stuck for 2 hours previously rendered identically -
    only V$SESSION.LAST_CALL_ET distinguishes them. Confirms `last_call_et` round-trips end to
    end, not just that the field exists."""
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_session_samples(
            db_id,
            BASE_TIME,
            [
                SessionSampleRow(
                    sid=304,
                    serial_num=4,
                    username="APP_USER",
                    sql_id=None,
                    event="enq: TX - row lock contention",
                    wait_class="Application",
                    status="ACTIVE",
                    blocking_session=None,
                    machine="host1",
                    program="python",
                    module=None,
                    action=None,
                    state="WAITING",
                    last_call_et=7200,
                )
            ],
        )
        await db.commit()

    resp = await client.get(f"/api/databases/{db_id}/sessions", headers=admin_headers)
    body = resp.json()
    assert body["sessions"][0]["last_call_et"] == 7200


def _session_row(sid: int, username: str = "APP_USER") -> SessionSampleRow:
    return SessionSampleRow(
        sid=sid, serial_num=1, username=username, sql_id=None, event=None, wait_class=None,
        status="ACTIVE", blocking_session=None, machine="host1", program="python", module=None,
        action=None,
    )


async def test_recent_session_samples_never_truncates_the_newest_round(client, admin_headers):
    """A flat ORDER BY ... LIMIT would silently cut an oversized round to an arbitrary subset
    (every row in a round shares the same sampled_at, so there's no secondary ordering to make
    that cut deterministic) - the newest round must always come back whole, regardless of a
    small `limit`."""
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_session_samples(db_id, BASE_TIME, [_session_row(sid) for sid in range(1, 6)])  # 5 rows
        await db.commit()

    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        result = await repo.recent_session_samples(db_id, limit=3)

    assert len(result) == 5
    assert {r.sid for r in result} == {1, 2, 3, 4, 5}


async def test_recent_session_samples_still_bounds_older_rounds_by_the_remaining_budget(client, admin_headers):
    """The newest round's guaranteed completeness doesn't turn `limit` into a no-op for
    genuine history - older rounds still respect whatever budget the newest round left."""
    db_id = await _register_database(client, admin_headers)
    older_time = BASE_TIME
    newer_time = BASE_TIME + timedelta(seconds=15)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_session_samples(db_id, older_time, [_session_row(sid) for sid in range(101, 103)])  # 2 rows
        await repo.insert_session_samples(db_id, newer_time, [_session_row(sid) for sid in range(201, 204)])  # 3 rows
        await db.commit()

    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        result = await repo.recent_session_samples(db_id, limit=4)

    # SQLite (this test suite's DB) round-trips sampled_at as naive regardless of what was
    # stored as timezone-aware - normalize before comparing, same reasoning as elsewhere in
    # this suite.
    newer_naive = newer_time.replace(tzinfo=None)
    older_naive = older_time.replace(tzinfo=None)
    newest_round = [r for r in result if r.sampled_at == newer_naive]
    older_round = [r for r in result if r.sampled_at == older_naive]
    assert len(newest_round) == 3  # the full newest round, never cut
    assert len(older_round) == 1  # only 1 of 2 older rows fit the remaining budget (4 - 3)
    assert len(result) == 4


async def test_recent_session_samples_respects_until_for_which_round_counts_as_newest(client, admin_headers):
    """Report builders pass a real past `until` (the report period's end) - "the newest round"
    must mean newest within that window, not the database's true current round."""
    db_id = await _register_database(client, admin_headers)
    in_window_time = BASE_TIME
    after_window_time = BASE_TIME + timedelta(days=1)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_session_samples(db_id, in_window_time, [_session_row(301)])
        await repo.insert_session_samples(db_id, after_window_time, [_session_row(401)])
        await db.commit()

    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        result = await repo.recent_session_samples(db_id, limit=500, until=BASE_TIME + timedelta(hours=1))

    assert {r.sid for r in result} == {301}


async def test_waits_endpoint_needs_two_rounds(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    resp = await client.get(f"/api/databases/{db_id}/waits", headers=admin_headers)
    body = resp.json()
    assert body["available"] is False
    assert body["wait_classes"] == []


async def test_waits_endpoint_computes_rate(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_wait_snapshots(
            db_id, BASE_TIME, [WaitSnapshotRow(event="db file sequential read", wait_class="User I/O", total_waits=10, time_waited_centisecs=1000)]
        )
        await repo.insert_wait_snapshots(
            db_id,
            BASE_TIME + timedelta(seconds=60),
            [WaitSnapshotRow(event="db file sequential read", wait_class="User I/O", total_waits=20, time_waited_centisecs=4600)],
        )
        await db.commit()

    resp = await client.get(f"/api/databases/{db_id}/waits", headers=admin_headers)
    body = resp.json()
    assert body["available"] is True
    assert body["wait_classes"] == [{"wait_class": "User I/O", "centisec_waited_per_sec": 60.0}]


async def test_wait_history_endpoint_needs_two_rounds(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    resp = await client.get(f"/api/databases/{db_id}/waits/history?hours=1", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_wait_history_endpoint_computes_a_rate_per_round_oldest_first(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    now = utcnow()
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        for i, total in enumerate([10, 20, 40]):
            await repo.insert_wait_snapshots(
                db_id,
                now - timedelta(minutes=3 - i),
                [WaitSnapshotRow(event="db file sequential read", wait_class="User I/O", total_waits=total, time_waited_centisecs=total * 100)],
            )
        await db.commit()

    resp = await client.get(f"/api/databases/{db_id}/waits/history?hours=1", headers=admin_headers)
    body = resp.json()
    # 3 rounds -> 2 consecutive-pair points, oldest first.
    assert len(body) == 2
    assert body[0]["wait_classes"] == [{"wait_class": "User I/O", "centisec_waited_per_sec": round(1000 / 60, 2)}]
    assert body[1]["wait_classes"] == [{"wait_class": "User I/O", "centisec_waited_per_sec": round(2000 / 60, 2)}]


async def test_wait_history_spans_the_whole_window_when_rounds_exceed_limit(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    now = utcnow()
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        for i in range(10):
            await repo.insert_wait_snapshots(
                db_id,
                now - timedelta(minutes=10 - i),
                [WaitSnapshotRow(event="db file sequential read", wait_class="User I/O", total_waits=i + 1, time_waited_centisecs=(i + 1) * 100)],
            )
        await db.commit()

    resp = await client.get(f"/api/databases/{db_id}/waits/history?hours=1&limit=3", headers=admin_headers)
    body = resp.json()
    # Rounds are 1 minute apart, spanning from -10min to -1min. Naively keeping only the 3
    # *most recent* rounds (the bug this fix closes) would pair them into points no older than
    # ~-2min, clustered at the tail. With the fix, evenly-spread rounds (kept at -10/-5/-1min)
    # pair into points including one at -5min - proving the window's older half wasn't dropped.
    assert len(body) == 2
    timestamps = [datetime.fromisoformat(point["sampled_at"].replace("Z", "+00:00")) for point in body]
    assert min(timestamps) <= now - timedelta(minutes=4, seconds=30)


async def test_cpu_history_endpoint_needs_two_rounds(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    resp = await client.get(f"/api/databases/{db_id}/cpu/history?hours=1", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_cpu_history_endpoint_computes_a_rate_per_round_oldest_first(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    now = utcnow()
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        # +6000 centisecs (60s of CPU) then +12000 centisecs, each over a 60s round.
        for i, cpu_cs in enumerate([100_000, 106_000, 118_000]):
            await repo.insert_instance_health(
                db_id,
                now - timedelta(minutes=3 - i),
                "up",
                InstanceHealthSample(status="OPEN", active_session_count=1, cpu_centisecs_used=cpu_cs),
            )
        await db.commit()

    resp = await client.get(f"/api/databases/{db_id}/cpu/history?hours=1", headers=admin_headers)
    body = resp.json()
    assert len(body) == 2
    assert body[0]["cpu_centisec_used_per_sec"] == round(6_000 / 60, 2)
    assert body[1]["cpu_centisec_used_per_sec"] == round(12_000 / 60, 2)


async def test_cpu_history_endpoint_skips_a_reset_counter_instead_of_reporting_negative(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    now = utcnow()
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        for i, cpu_cs in enumerate([100_000, 200]):  # second round "reset" lower than the first
            await repo.insert_instance_health(
                db_id,
                now - timedelta(minutes=2 - i),
                "up",
                InstanceHealthSample(status="OPEN", active_session_count=1, cpu_centisecs_used=cpu_cs),
            )
        await db.commit()

    resp = await client.get(f"/api/databases/{db_id}/cpu/history?hours=1", headers=admin_headers)
    assert resp.json() == []


async def test_blocking_endpoint_pairs_blocker_and_blocked(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
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
        await db.commit()

    resp = await client.get(f"/api/databases/{db_id}/blocking", headers=admin_headers)
    body = resp.json()
    assert body["available"] is True
    assert len(body["blocking_chains"]) == 1
    assert body["blocking_chains"][0]["blocker_username"] == "BLOCKER"


async def test_health_history_endpoint_orders_oldest_first(client, admin_headers):
    # The `hours` filter is relative to "now", so this test uses recent timestamps rather
    # than the fixed historical BASE_TIME used elsewhere in this file.
    db_id = await _register_database(client, admin_headers)
    now = utcnow()
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_instance_health(
            db_id, now - timedelta(minutes=2), "up", InstanceHealthSample(status="OPEN", active_session_count=5)
        )
        await repo.insert_instance_health(
            db_id, now - timedelta(minutes=1), "up", InstanceHealthSample(status="OPEN", active_session_count=8)
        )
        await db.commit()

    resp = await client.get(f"/api/databases/{db_id}/health-history?hours=1", headers=admin_headers)
    body = resp.json()
    assert len(body) == 2
    assert body[0]["active_session_count"] == 5
    assert body[1]["active_session_count"] == 8


async def test_health_history_timestamps_are_marked_utc_not_naive(client, admin_headers):
    """Regression test for a bug found via live QA testing: SQLite round-trips a
    timezone-aware datetime as naive, and Pydantic's default serialization of a naive
    datetime omits any UTC marker - a browser's `new Date(iso)` then silently misreads it as
    local time (observed live: a "5h ago" label that was actually under a minute old, 5
    hours being exactly the viewer's UTC offset). ApiModel now forces a "Z" suffix on every
    datetime field (app/schemas/common.py) - this proves it survives a real round trip
    through SQLite and the real API response, not just an isolated Pydantic model."""
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_instance_health(
            db_id, utcnow(), "up", InstanceHealthSample(status="OPEN", active_session_count=1)
        )
        await db.commit()

    resp = await client.get(f"/api/databases/{db_id}/health-history?hours=1", headers=admin_headers)
    body = resp.json()
    assert len(body) == 1
    assert body[0]["sampled_at"].endswith("Z")


async def test_compare_endpoint_before_any_telemetry_is_honest(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    resp = await client.get(
        f"/api/databases/{db_id}/metrics/compare", params={"metric_key": "active_session_count"}, headers=admin_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["reason"] == "No current telemetry for this metric yet."


async def test_compare_endpoint_for_a_metric_not_currently_reporting_is_honest(client, admin_headers):
    # Only active_session_count telemetry exists - no wait_snapshots at all - so a wait-class
    # metric_key must come back honestly unavailable, not a 500 or a fabricated zero. Also
    # exercises a metric_key containing a literal "/" (a real Oracle wait class name, "User
    # I/O") - metric_key is a query param specifically so this can't be mistaken for an extra
    # path segment.
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_instance_health(db_id, utcnow(), "up", InstanceHealthSample(status="OPEN", active_session_count=5))
        await db.commit()

    resp = await client.get(
        f"/api/databases/{db_id}/metrics/compare",
        params={"metric_key": "wait_rate_centisec_per_sec:User I/O"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["available"] is False


async def test_compare_endpoint_has_a_current_value_but_no_baseline_yet(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_instance_health(db_id, utcnow(), "up", InstanceHealthSample(status="OPEN", active_session_count=12))
        await db.commit()

    resp = await client.get(
        f"/api/databases/{db_id}/metrics/compare",
        params={"metric_key": "active_session_count", "hour_of_day": 5},
        headers=admin_headers,
    )
    body = resp.json()
    assert body["available"] is True
    assert body["observed_value"] == 12
    assert body["sample_count"] == 0
    assert body["baseline_mean"] is None
    assert body["severity"] is None
    assert body["min_samples_for_anomaly"] == 20


async def test_compare_endpoint_flags_a_real_deviation_using_the_real_classifier(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_instance_health(db_id, utcnow(), "up", InstanceHealthSample(status="OPEN", active_session_count=40))
        # mean=10, m2=20 over 20 samples -> variance=1, stddev=1 - a current value of 40 is
        # 30 standard deviations out, deep into the real classifier's ">= 6" CRITICAL band.
        await BaselineRepository(db).upsert(db_id, "active_session_count", 5, 20, 10.0, 20.0)
        await db.commit()

    resp = await client.get(
        f"/api/databases/{db_id}/metrics/compare",
        params={"metric_key": "active_session_count", "hour_of_day": 5},
        headers=admin_headers,
    )
    body = resp.json()
    assert body["available"] is True
    assert body["observed_value"] == 40
    assert body["baseline_mean"] == 10.0
    assert body["baseline_stddev"] == 1.0
    assert body["sample_count"] == 20
    assert body["z_score"] == 30.0
    assert body["severity"] == "CRITICAL"


async def test_compare_endpoint_rejects_out_of_range_hour_of_day(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    resp = await client.get(
        f"/api/databases/{db_id}/metrics/compare",
        params={"metric_key": "active_session_count", "hour_of_day": 24},
        headers=admin_headers,
    )
    assert resp.status_code == 422


async def test_dashboard_endpoints_require_database_view_permission(client):
    resp = await client.get("/api/databases/some-id/sessions")
    assert resp.status_code == 401


async def test_dashboard_endpoints_404_for_unknown_database(client, admin_headers):
    resp = await client.get("/api/databases/does-not-exist/sessions", headers=admin_headers)
    assert resp.status_code == 404
