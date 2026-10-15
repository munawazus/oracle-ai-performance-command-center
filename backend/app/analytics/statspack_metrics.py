"""Statspack delta computation (master plan Phase 11, Enterprise Readiness Plan D.3) - thin
by design: STATS$SYSTEM_EVENT/STATS$SQL_SUMMARY already store raw cumulative-since-startup
counters per snapshot, exactly like V$SYSTEM_EVENT/V$SQL do for this platform's own 15s/300s
collectors, so the existing delta machinery in app/analytics/metrics.py (wait-class rates) and
app/analytics/sql_metrics.py (SQL deltas) is reused unmodified rather than reimplemented.

StatspackWaitEventSample rows satisfy metrics.WaitRow's Protocol directly (same
wait_class/total_waits/time_waited_centisecs fields). StatspackSqlSample rows need one small
adapter (_StatspackSqlStatRow below) because STATS$SQL_SUMMARY has no child-cursor-count
concept the way V$SQL does - each row is already one summary row per (snap_id, sql_id,
plan_hash_value), so child_cursor_count is fixed at 1.

The one genuinely new thing here: Statspack's "rounds" are the DBA's own irregular snapshots,
which can be minutes or days apart (unlike this platform's fixed-interval collectors), so
rates are expressed per hour, not per second.
"""

from dataclasses import dataclass
from datetime import datetime

from app.models.statspack_telemetry import StatspackSqlSample


@dataclass(frozen=True)
class SnapshotPair:
    from_snap_id: int
    from_snap_time: datetime
    to_snap_id: int
    to_snap_time: datetime

    @property
    def elapsed_hours(self) -> float:
        seconds = (self.to_snap_time - self.from_snap_time).total_seconds()
        return seconds / 3600.0 if seconds > 0 else 0.0


def rate_per_hour(delta_value: float, elapsed_hours: float) -> float:
    return delta_value / elapsed_hours if elapsed_hours > 0 else 0.0


@dataclass(frozen=True)
class _StatspackSqlStatRow:
    """Adapts a StatspackSqlSample row to satisfy sql_metrics.SqlStatRowLike's Protocol."""

    sql_id: str
    plan_hash_value: int
    executions: int
    elapsed_time_us: int
    cpu_time_us: int
    buffer_gets: int
    disk_reads: int
    rows_processed: int
    parse_calls: int
    child_cursor_count: int = 1


def adapt_sql_samples(rows: list[StatspackSqlSample]) -> list[_StatspackSqlStatRow]:
    return [
        _StatspackSqlStatRow(
            sql_id=row.sql_id,
            plan_hash_value=row.plan_hash_value,
            executions=row.executions,
            elapsed_time_us=row.elapsed_time_us,
            cpu_time_us=row.cpu_time_us,
            buffer_gets=row.buffer_gets,
            disk_reads=row.disk_reads,
            rows_processed=row.rows_processed,
            parse_calls=row.parse_calls,
        )
        for row in rows
    ]
