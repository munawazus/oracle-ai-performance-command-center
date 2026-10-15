"""Statspack integration (master plan Phase 11, Enterprise Readiness Plan D.3) - top wait
events and top SQL, read from the PERFSTAT schema's own STATS$SNAPSHOT/STATS$SYSTEM_EVENT/
STATS$SQL_SUMMARY tables. Statspack itself is a free, bundled Oracle feature requiring no
Diagnostics/Tuning Pack license - unlike the DBA_HIST_*/V$ACTIVE_SESSION_HISTORY (AWR/ASH)
family already in app/oracle/allowlist.py's BLOCKLISTED_VIEWS, which this module never
touches.

Unlike app/oracle/sql_probe.py/tablespace_probe.py (which sample a live V$/DBA_* view right
now, on this platform's own fixed schedule), Statspack's snapshots already exist, taken on the
DBA's own independent, irregular schedule via their own statspack.snap job - this module reads
incrementally, "what's new since the snap_id we last synced," never "sample now."

Assumes a 10g+ PERFSTAT schema (SQL_ID and WAIT_CLASS columns present in STATS$SQL_SUMMARY/
STATS$SYSTEM_EVENT) - matches this codebase's existing 11g+-era assumptions everywhere else
(app/oracle/sql_probe.py keys on sql_id, not hash_value). Older hash-value-only schemas
(8i/9i) are out of scope.

Deliberately never queried, each for a stated reason:
- STATS$SQLTEXT: a CLOB-assembly table - same LOB-handling gap sql_probe.py already avoids
  (the deliberately minimal OracleCursor protocol has no LOB handling). sql_text for a
  Statspack-observed statement is populated only via cross-reference against this platform's
  own SqlStatement table (from the D.1 SQL Intelligence collector) when the two happen to
  track the same sql_id.
- STATS$SYSSTAT / STATS$WAITSTAT / STATS$LATCH* / STATS$ENQUEUE_STAT: system-wide counters
  and latch/enqueue statistics, out of scope for the top-wait-events/top-SQL value
  proposition this integration targets (Enterprise Readiness Plan D.3's own scope cut-line).
"""

from dataclasses import dataclass
from datetime import datetime

from app.oracle.connector import OracleConnection

_SNAPSHOT_SQL_TEMPLATE = (
    "SELECT * FROM ("
    "  SELECT s.snap_id, s.snap_time"
    "  FROM PERFSTAT.STATS$SNAPSHOT s"
    "  WHERE s.dbid = (SELECT dbid FROM v$database)"
    "    AND s.instance_number = (SELECT instance_number FROM v$instance)"
    "    {since_clause}"
    "  ORDER BY s.snap_id"
    ") WHERE ROWNUM <= {max_snapshots}"
)

_WAIT_EVENT_SQL_TEMPLATE = (
    "SELECT e.snap_id, e.event, e.wait_class, e.total_waits, e.time_waited_micro"
    " FROM PERFSTAT.STATS$SYSTEM_EVENT e"
    " WHERE e.snap_id IN ({snap_ids})"
)

_SQL_SUMMARY_SQL_TEMPLATE = (
    "SELECT s.snap_id, s.sql_id, s.plan_hash_value, s.executions, s.elapsed_time,"
    "       s.cpu_time, s.buffer_gets, s.disk_reads, s.rows_processed, s.parse_calls"
    " FROM PERFSTAT.STATS$SQL_SUMMARY s"
    " WHERE s.snap_id IN ({snap_ids})"
)


def _validated_max_snapshots(max_snapshots: int) -> int:
    if not isinstance(max_snapshots, int) or not (0 < max_snapshots <= 100):
        raise ValueError(f"max_snapshots must be an int in (0, 100], got {max_snapshots!r}")
    return max_snapshots


def _validated_snap_ids(snap_ids: list[int]) -> list[int]:
    if not snap_ids:
        raise ValueError("snap_ids must be non-empty")
    if not all(isinstance(s, int) for s in snap_ids):
        raise ValueError(f"snap_ids must all be ints, got {snap_ids!r}")
    return snap_ids


@dataclass(frozen=True)
class SnapshotRow:
    snap_id: int
    snap_time: datetime


@dataclass(frozen=True)
class WaitEventRow:
    snap_id: int
    event_name: str
    wait_class: str | None
    total_waits: int
    time_waited_centisecs: int


@dataclass(frozen=True)
class SqlRow:
    snap_id: int
    sql_id: str
    plan_hash_value: int
    executions: int
    elapsed_time_us: int
    cpu_time_us: int
    buffer_gets: int
    disk_reads: int
    rows_processed: int
    parse_calls: int


def sample_new_snapshots(
    conn: OracleConnection, since_snap_id: int | None, max_snapshots: int
) -> list[SnapshotRow]:
    """Snapshots this platform hasn't synced yet, oldest first, capped at max_snapshots -
    bounds one run's cost if a large backlog exists (e.g. the first sync after months of
    untouched Statspack)."""
    max_snapshots = _validated_max_snapshots(max_snapshots)
    since_clause = ""
    if since_snap_id is not None:
        if not isinstance(since_snap_id, int):
            raise ValueError(f"since_snap_id must be an int or None, got {since_snap_id!r}")
        since_clause = f"AND s.snap_id > {since_snap_id}"

    cursor = conn.cursor()
    # since_snap_id/max_snapshots are validated ints above (never request-derived) before
    # interpolation - same `# nosec B608` guard pattern as app/oracle/sql_probe.py's top_n.
    cursor.execute(
        _SNAPSHOT_SQL_TEMPLATE.format(since_clause=since_clause, max_snapshots=max_snapshots)
    )  # nosec B608
    return [SnapshotRow(snap_id=row[0], snap_time=row[1]) for row in cursor.fetchall()]


def sample_wait_events_for_snapshots(conn: OracleConnection, snap_ids: list[int]) -> list[WaitEventRow]:
    snap_ids = _validated_snap_ids(snap_ids)
    cursor = conn.cursor()
    ids_literal = ",".join(str(s) for s in snap_ids)
    cursor.execute(_WAIT_EVENT_SQL_TEMPLATE.format(snap_ids=ids_literal))  # nosec B608
    return [
        WaitEventRow(
            snap_id=row[0],
            event_name=row[1],
            wait_class=row[2],
            total_waits=row[3] or 0,
            # STATS$SYSTEM_EVENT stores TIME_WAITED_MICRO (microseconds) - converted to
            # centiseconds here so StatspackWaitEventSample shares WaitSnapshot's existing
            # unit convention and stays a drop-in row for app/analytics/metrics.py.
            time_waited_centisecs=(row[4] or 0) // 10_000,
        )
        for row in cursor.fetchall()
    ]


def sample_sql_for_snapshots(conn: OracleConnection, snap_ids: list[int]) -> list[SqlRow]:
    snap_ids = _validated_snap_ids(snap_ids)
    cursor = conn.cursor()
    ids_literal = ",".join(str(s) for s in snap_ids)
    cursor.execute(_SQL_SUMMARY_SQL_TEMPLATE.format(snap_ids=ids_literal))  # nosec B608
    return [
        SqlRow(
            snap_id=row[0],
            sql_id=row[1],
            plan_hash_value=row[2] or 0,
            executions=row[3] or 0,
            elapsed_time_us=row[4] or 0,
            cpu_time_us=row[5] or 0,
            buffer_gets=row[6] or 0,
            disk_reads=row[7] or 0,
            rows_processed=row[8] or 0,
            parse_calls=row[9] or 0,
        )
        for row in cursor.fetchall()
    ]
