from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.analytics.sql_metrics import compute_sql_deltas
from app.analytics.statspack_metrics import SnapshotPair, adapt_sql_samples, rate_per_hour

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_snapshot_pair_elapsed_hours_computes_real_gap():
    pair = SnapshotPair(from_snap_id=1, from_snap_time=NOW, to_snap_id=2, to_snap_time=NOW + timedelta(hours=3))

    assert pair.elapsed_hours == 3.0


def test_snapshot_pair_elapsed_hours_is_zero_for_a_non_positive_gap():
    pair = SnapshotPair(from_snap_id=2, from_snap_time=NOW, to_snap_id=1, to_snap_time=NOW - timedelta(hours=1))

    assert pair.elapsed_hours == 0.0


def test_rate_per_hour_divides_by_elapsed_hours():
    assert rate_per_hour(100.0, 4.0) == 25.0


def test_rate_per_hour_is_zero_for_a_non_positive_elapsed():
    assert rate_per_hour(100.0, 0.0) == 0.0
    assert rate_per_hour(100.0, -1.0) == 0.0


@dataclass
class _StatspackSqlSampleDouble:
    sql_id: str
    plan_hash_value: int
    executions: int
    elapsed_time_us: int
    cpu_time_us: int
    buffer_gets: int
    disk_reads: int
    rows_processed: int
    parse_calls: int


def test_adapt_sql_samples_fixes_child_cursor_count_at_one():
    row = _StatspackSqlSampleDouble(
        sql_id="abc123", plan_hash_value=111, executions=10, elapsed_time_us=1000, cpu_time_us=500,
        buffer_gets=200, disk_reads=5, rows_processed=100, parse_calls=1,
    )

    adapted = adapt_sql_samples([row])

    assert len(adapted) == 1
    assert adapted[0].child_cursor_count == 1
    assert adapted[0].sql_id == "abc123"


def test_adapted_sql_samples_are_reusable_by_the_existing_sql_delta_machinery():
    """The whole point of the adapter (Enterprise Readiness Plan D.3): no new delta logic
    needed for Statspack's own top-SQL summary - app/analytics/sql_metrics.compute_sql_deltas
    (built for D.1's SQL Intelligence) works unmodified against adapted Statspack rows."""
    previous = _StatspackSqlSampleDouble(
        sql_id="abc123", plan_hash_value=111, executions=100, elapsed_time_us=100_000, cpu_time_us=50_000,
        buffer_gets=1000, disk_reads=10, rows_processed=500, parse_calls=5,
    )
    current = _StatspackSqlSampleDouble(
        sql_id="abc123", plan_hash_value=111, executions=150, elapsed_time_us=250_000, cpu_time_us=120_000,
        buffer_gets=1800, disk_reads=15, rows_processed=800, parse_calls=6,
    )

    deltas = compute_sql_deltas(adapt_sql_samples([previous]), adapt_sql_samples([current]), elapsed_seconds=3600.0)

    assert len(deltas) == 1
    assert deltas[0].executions == 50
    assert deltas[0].elapsed_time_us == 150_000
