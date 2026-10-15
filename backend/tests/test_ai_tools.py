"""Tool layer tests - pure reads of this platform's own Postgres/SQLite repository, no
Oracle connection and no LLM involved at all."""

from datetime import datetime, timedelta, timezone

from app.ai import tools
from app.db import SessionLocal
from app.oracle.telemetry_probe import SessionSampleRow, WaitSnapshotRow
from app.repositories.telemetry_repo import TelemetryRepository

DB_PAYLOAD = {
    "name": "AITOOLS01",
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


async def test_get_database_health_reports_missing_data_honestly(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        result = await tools.get_database_health(db, db_id)

    assert result["available"] is True
    assert result["database"]["name"] == "AITOOLS01"
    assert result["capabilities"]["available"] is False
    assert result["collector_status"]["available"] is False
    assert result["latest_instance_health"]["available"] is False


async def test_get_database_health_missing_database_is_honest_not_an_exception():
    async with SessionLocal() as db:
        result = await tools.get_database_health(db, "does-not-exist")
    assert result == {"available": False, "reason": "database not found"}


async def test_get_active_sessions_returns_only_the_latest_round(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_session_samples(
            db_id,
            BASE_TIME,
            [SessionSampleRow(101, 1, "OLDUSER", None, None, None, "ACTIVE", None, None, None, None, None)],
        )
        await repo.insert_session_samples(
            db_id,
            BASE_TIME + timedelta(minutes=1),
            [SessionSampleRow(202, 2, "NEWUSER", "abc123", "db file sequential read", "User I/O", "ACTIVE", None, "host1", "sqlplus", None, None)],
        )
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.get_active_sessions(db, db_id)

    assert result["available"] is True
    assert result["session_count"] == 1
    assert result["sessions"][0]["username"] == "NEWUSER"


async def test_get_active_sessions_no_telemetry_is_honest(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        result = await tools.get_active_sessions(db, db_id)
    assert result == {"available": False, "reason": "no session telemetry collected yet"}


async def test_get_wait_profile_needs_two_rounds(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_wait_snapshots(
            db_id, BASE_TIME, [WaitSnapshotRow(event="db file sequential read", wait_class="User I/O", total_waits=10, time_waited_centisecs=100)]
        )
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.get_wait_profile(db, db_id)
    assert result["available"] is False


async def test_get_wait_profile_computes_rate_from_two_rounds(client, admin_headers):
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

    async with SessionLocal() as db:
        result = await tools.get_wait_profile(db, db_id)

    assert result["available"] is True
    assert result["wait_classes"] == [{"wait_class": "User I/O", "centisec_waited_per_sec": 60.0}]


async def test_get_blocking_chain_pairs_blocker_and_blocked(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_session_samples(
            db_id,
            BASE_TIME,
            [
                SessionSampleRow(101, 1, "BLOCKER", "sql1", None, None, "ACTIVE", None, "host1", "sqlplus", None, None),
                SessionSampleRow(102, 2, "VICTIM", "sql2", "enq: TX - row lock contention", "Application", "ACTIVE", 101, "host2", "sqlplus", None, None),
            ],
        )
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.get_blocking_chain(db, db_id)

    assert result["available"] is True
    assert len(result["blocking_chains"]) == 1
    chain = result["blocking_chains"][0]
    assert chain["blocked_sid"] == 102
    assert chain["blocker_sid"] == 101
    assert chain["blocker_username"] == "BLOCKER"
    assert chain["blocker_in_this_sample"] is True


async def test_get_blocking_chain_empty_when_nothing_blocked(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_session_samples(
            db_id, BASE_TIME, [SessionSampleRow(101, 1, "SOLO", None, None, None, "ACTIVE", None, None, None, None, None)]
        )
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.get_blocking_chain(db, db_id)
    assert result["available"] is True
    assert result["blocking_chains"] == []
    assert result["sampled_at"].startswith("2026-01-01T00:00:00")


async def test_get_incident_missing_is_honest():
    async with SessionLocal() as db:
        result = await tools.get_incident(db, "does-not-exist")
    assert result == {"available": False, "reason": "incident not found"}


async def test_compare_baseline_periods_missing_data_is_honest(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        result = await tools.compare_baseline_periods(db, db_id, "active_session_count", 2, 14)

    assert result["period_a"]["available"] is False
    assert result["period_b"]["available"] is False


async def test_compare_baseline_periods_reports_real_stats(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        from app.repositories.analytics_repo import BaselineRepository

        await BaselineRepository(db).upsert(db_id, "active_session_count", 2, sample_count=30, mean=10.0, m2=90.0)
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.compare_baseline_periods(db, db_id, "active_session_count", 2, 14)

    assert result["period_a"]["available"] is True
    assert result["period_a"]["mean"] == 10.0
    assert result["period_a"]["sample_count"] == 30
    assert result["period_b"]["available"] is False


async def test_get_top_sql_is_honestly_unavailable_before_any_sql_telemetry(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        result = await tools.get_top_sql(db, db_id)

    assert result == {
        "available": False,
        "reason": "fewer than two SQL collection rounds - no per-interval delta can be computed yet",
    }


async def test_get_top_sql_falls_back_quietly_on_an_invalid_sort_by_never_raises(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        result = await tools.get_top_sql(db, db_id, sort_by="not_a_real_llm_hallucinated_field")

    assert result["available"] is False  # no telemetry yet either way - the point is it doesn't raise


async def test_get_top_sql_returns_real_deltas(client, admin_headers):
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
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.get_top_sql(db, db_id)

    assert result["available"] is True
    assert result["statements"][0]["sql_id"] == "abc123sqlid"
    assert result["statements"][0]["executions_delta"] == 10
    assert result["statements"][0]["sql_text"] == "SELECT 1"


async def test_get_sql_plan_is_honestly_unavailable_for_an_unknown_statement(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        result = await tools.get_sql_plan(db, db_id, "nosuchsqlid")

    assert result == {"available": False, "reason": "no SQL telemetry collected for this statement yet"}


async def test_get_sql_plan_returns_real_plan_history(client, admin_headers):
    from app.oracle.sql_probe import SqlPlanLineRow, SqlPlanRow, SqlStatRow
    from app.repositories.sql_repo import SqlTelemetryRepository

    db_id = await _register_database(client, admin_headers)
    stat_row = SqlStatRow("abc123sqlid", 111, 10, 100_000, 80_000, 500, 5, 10, 10, 1, "APPUSER", "SELECT 1")
    plan_row = SqlPlanRow(
        "abc123sqlid",
        111,
        (SqlPlanLineRow(0, None, 0, 1, "SELECT STATEMENT", None, None, None, None, 5, None, None),),
    )
    async with SessionLocal() as db:
        repo = SqlTelemetryRepository(db)
        await repo.upsert_statements(db_id, BASE_TIME, [stat_row])
        await repo.upsert_plan_snapshots(db_id, BASE_TIME, [plan_row])
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.get_sql_plan(db, db_id, "abc123sqlid")

    assert result["available"] is True
    assert result["plan_count"] == 1
    assert result["plan_changed"] is False
    assert result["plans"][0]["plan_hash_value"] == 111


async def test_get_sql_regressions_is_honestly_empty_before_any_detection(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        result = await tools.get_sql_regressions(db, db_id)

    assert result == {"available": True, "open_regression_count": 0, "regressions": []}


async def test_get_sql_regressions_returns_real_open_events(client, admin_headers):
    from app.repositories.sql_repo import SqlTelemetryRepository

    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        await SqlTelemetryRepository(db).create_regression(
            db_id,
            sql_id="abc123sqlid",
            regression_kind="ELAPSED_PER_EXEC",
            severity="HIGH",
            detection_note="Elapsed time per execution went from 1000us to 15000us (15.0x).",
        )
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.get_sql_regressions(db, db_id)

    assert result["open_regression_count"] == 1
    assert result["regressions"][0]["sql_id"] == "abc123sqlid"
    assert result["regressions"][0]["severity"] == "HIGH"


async def test_get_tablespace_trend_is_honestly_unavailable_before_any_capacity_telemetry(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        result = await tools.get_tablespace_trend(db, db_id)

    assert result == {"available": False, "reason": "no capacity telemetry collected yet for this database"}


async def test_get_tablespace_trend_returns_all_tablespaces_sorted_by_pct_used(client, admin_headers):
    from app.oracle.tablespace_probe import TablespaceRow
    from app.repositories.capacity_repo import CapacityRepository

    db_id = await _register_database(client, admin_headers)
    rows = [
        TablespaceRow("SYSTEM", "PERMANENT", "ONLINE", 1000, 100, 2000, 1),  # 10% used
        TablespaceRow("USERS", "PERMANENT", "ONLINE", 1000, 900, 2000, 1),  # 90% used
    ]
    async with SessionLocal() as db:
        await CapacityRepository(db).insert_tablespace_samples(db_id, BASE_TIME, rows)
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.get_tablespace_trend(db, db_id)

    assert result["available"] is True
    assert result["tablespace_count"] == 2
    assert result["tablespaces"][0]["tablespace_name"] == "USERS"  # worst first


async def test_get_tablespace_trend_returns_one_tablespace_detail_when_named(client, admin_headers):
    from app.oracle.tablespace_probe import TablespaceRow
    from app.repositories.capacity_repo import CapacityRepository

    db_id = await _register_database(client, admin_headers)
    rows = [TablespaceRow("SYSTEM", "PERMANENT", "ONLINE", 1000, 500, 2000, 1)]
    async with SessionLocal() as db:
        await CapacityRepository(db).insert_tablespace_samples(db_id, BASE_TIME, rows)
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.get_tablespace_trend(db, db_id, tablespace_name="SYSTEM")

    assert result["available"] is True
    assert result["tablespace_name"] == "SYSTEM"
    assert result["pct_used"] == 50.0
    assert result["open_alerts"] == []


async def test_get_tablespace_trend_falls_back_to_all_tablespaces_for_an_unknown_name_never_raises(
    client, admin_headers
):
    from app.oracle.tablespace_probe import TablespaceRow
    from app.repositories.capacity_repo import CapacityRepository

    db_id = await _register_database(client, admin_headers)
    rows = [TablespaceRow("SYSTEM", "PERMANENT", "ONLINE", 1000, 500, 2000, 1)]
    async with SessionLocal() as db:
        await CapacityRepository(db).insert_tablespace_samples(db_id, BASE_TIME, rows)
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.get_tablespace_trend(db, db_id, tablespace_name="NOT_A_REAL_LLM_HALLUCINATED_NAME")

    assert result["available"] is True  # no telemetry-shaped exception, just the summary view
    assert "tablespaces" in result


async def test_get_statspack_summary_is_honestly_unavailable_when_not_installed(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        result = await tools.get_statspack_summary(db, db_id)

    assert result == {"available": False, "reason": "Statspack is not installed on this database"}


async def test_get_statspack_summary_is_honestly_unavailable_before_any_sync(client, admin_headers):
    from app.repositories.database_repo import CapabilityRepository

    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        await CapabilityRepository(db).upsert(
            monitored_database_id=db_id, detected_version="19.0.0.0.0", detected_edition="EE",
            is_rac=False, statspack_available=True, privilege_report={},
        )
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.get_statspack_summary(db, db_id)

    assert result["available"] is False
    assert "no snapshots" in result["reason"]


async def test_get_statspack_summary_returns_real_delta_after_two_synced_snapshots(client, admin_headers):
    from app.oracle.statspack_probe import SnapshotRow, SqlRow, WaitEventRow
    from app.repositories.database_repo import CapabilityRepository
    from app.repositories.statspack_repo import StatspackRepository

    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        await CapabilityRepository(db).upsert(
            monitored_database_id=db_id, detected_version="19.0.0.0.0", detected_edition="EE",
            is_rac=False, statspack_available=True, privilege_report={},
        )
        repo = StatspackRepository(db)
        snap_1_time = BASE_TIME
        snap_2_time = BASE_TIME + timedelta(hours=1)
        await repo.insert_snapshots(
            db_id, BASE_TIME,
            [SnapshotRow(101, snap_1_time), SnapshotRow(102, snap_2_time)],
        )
        await repo.insert_wait_event_samples(
            db_id,
            [
                WaitEventRow(101, "db file sequential read", "User I/O", 100, 5000),
                WaitEventRow(102, "db file sequential read", "User I/O", 150, 9000),
            ],
        )
        await repo.insert_sql_samples(
            db_id,
            [
                SqlRow(101, "abc123sqlid", 111, 10, 100_000, 80_000, 500, 5, 10, 10),
                SqlRow(102, "abc123sqlid", 111, 20, 250_000, 200_000, 900, 9, 20, 20),
            ],
        )
        await repo.upsert_sync_state(db_id, 102, snap_2_time)
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.get_statspack_summary(db, db_id)

    assert result["available"] is True
    assert result["from_snap_id"] == 101
    assert result["to_snap_id"] == 102
    assert result["wait_classes"][0]["wait_class"] == "User I/O"
    assert result["top_sql"][0]["sql_id"] == "abc123sqlid"
    assert result["top_sql"][0]["executions_delta"] == 10


async def test_get_change_history_is_honestly_unavailable_before_any_round(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        result = await tools.get_change_history(db, db_id)

    assert result == {"available": False, "reason": "no configuration telemetry has been collected yet"}


async def test_get_change_history_is_honestly_unavailable_after_a_single_round(client, admin_headers):
    from app.oracle.config_probe import ParameterRow
    from app.repositories.config_repo import ConfigTelemetryRepository

    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        await ConfigTelemetryRepository(db).insert_parameter_samples(
            db_id, BASE_TIME, [ParameterRow("open_cursors", "300")]
        )
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.get_change_history(db, db_id)

    assert result["available"] is False
    assert "at least two" in result["reason"]


async def test_get_change_history_returns_classified_changes_after_two_rounds(client, admin_headers):
    from app.oracle.config_probe import ParameterRow
    from app.repositories.config_repo import ConfigTelemetryRepository

    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = ConfigTelemetryRepository(db)
        await repo.insert_parameter_samples(db_id, BASE_TIME, [ParameterRow("open_cursors", "300")])
        await repo.insert_parameter_samples(
            db_id, BASE_TIME + timedelta(hours=1), [ParameterRow("open_cursors", "500")]
        )
        await db.commit()

    async with SessionLocal() as db:
        result = await tools.get_change_history(db, db_id)

    assert result["available"] is True
    assert result["changes"] == [
        {
            "parameter_name": "open_cursors",
            "previous_value": "300",
            "current_value": "500",
            "change_type": "changed",
        }
    ]
