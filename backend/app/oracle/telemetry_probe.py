"""Our own historical workload/session repository - built from V$SESSION/V$SYSTEM_EVENT/
V$INSTANCE sampling, explicitly NOT a reproduction of Oracle ASH (master plan Sec 7). Every
query here is one of the Section 7 base-view allowlist entries.
"""

from dataclasses import dataclass

from app.oracle.connector import OracleConnection

# Background/system sessions add noise without diagnostic value for a first pass -
# excluded at the query level, not by client-side filtering.
_SESSION_SAMPLE_SQL = (
    "SELECT sid, serial#, username, sql_id, event, wait_class, status, blocking_session, "
    "machine, program, module, action, state, last_call_et "
    "FROM v$session WHERE type = 'USER' AND username IS NOT NULL"
)

# Idle is the overwhelming majority of rows in most instances and carries no diagnostic
# signal for wait analysis - excluded at the query level.
_WAIT_SNAPSHOT_SQL = (
    "SELECT event, wait_class, total_waits, time_waited FROM v$system_event WHERE wait_class != 'Idle'"
)

_INSTANCE_STATUS_SQL = "SELECT status FROM v$instance"
_ACTIVE_SESSION_COUNT_SQL = "SELECT COUNT(*) FROM v$session WHERE type = 'USER' AND status = 'ACTIVE'"
# Instance-wide cumulative centiseconds of CPU consumed since instance startup - despite the
# "this session" name, V$SYSSTAT is a system-wide view (V$SESSTAT is the real per-session
# one). Same raw-cumulative-counter shape as V$SYSTEM_EVENT's time_waited - a rate needs a
# delta between two rounds, computed by the analytics layer, not here.
_CPU_STAT_SQL = "SELECT value FROM v$sysstat WHERE name = 'CPU used by this session'"


@dataclass(frozen=True)
class SessionSampleRow:
    sid: int
    serial_num: int | None
    username: str | None
    sql_id: str | None
    event: str | None
    wait_class: str | None
    status: str | None
    blocking_session: int | None
    machine: str | None
    program: str | None
    module: str | None
    action: str | None
    # V$SESSION.STATE - 'WAITING' (blocked on `event` right now) vs 'WAITED SHORT/KNOWN/
    # UNKNOWN TIME' (recently finished a wait, genuinely on CPU now). Without this, a
    # CPU-bound session shows the same stale event/wait_class as a truly idle one (Enterprise
    # Readiness QA_LIVE_FINDINGS.md Finding 7) - defaulted so existing positional
    # SessionSampleRow(...) construction (gold_incidents.py, tests) keeps working unchanged.
    state: str | None = None
    # V$SESSION.LAST_CALL_ET - seconds since this session became ACTIVE (elapsed in its
    # current call) or since it became INACTIVE, depending on `status`. Without this, a
    # session that just started waiting and one that's been stuck for two hours render
    # identically - the one signal a DBA actually looks for when triaging. Defaulted for the
    # same positional-construction reason as `state`.
    last_call_et: int | None = None


@dataclass(frozen=True)
class WaitSnapshotRow:
    event: str
    wait_class: str | None
    total_waits: int
    time_waited_centisecs: int


@dataclass(frozen=True)
class InstanceHealthSample:
    status: str  # the instance's own reported status (e.g. OPEN, MOUNTED), not up/down
    active_session_count: int
    # Raw cumulative V$SYSSTAT counter (centiseconds) - None only if the query itself failed
    # to return a row, not a real "no CPU used" reading. Defaulted for the same reason as
    # SessionSampleRow.state: existing positional InstanceHealthSample(...) construction
    # keeps working unchanged.
    cpu_centisecs_used: int | None = None


def sample_sessions(conn: OracleConnection) -> list[SessionSampleRow]:
    cursor = conn.cursor()
    cursor.execute(_SESSION_SAMPLE_SQL)
    return [
        SessionSampleRow(
            sid=row[0],
            serial_num=row[1],
            username=row[2],
            sql_id=row[3],
            event=row[4],
            wait_class=row[5],
            status=row[6],
            blocking_session=row[7],
            machine=row[8],
            program=row[9],
            module=row[10],
            action=row[11],
            state=row[12],
            last_call_et=row[13],
        )
        for row in cursor.fetchall()
    ]


def sample_wait_events(conn: OracleConnection) -> list[WaitSnapshotRow]:
    cursor = conn.cursor()
    cursor.execute(_WAIT_SNAPSHOT_SQL)
    return [
        WaitSnapshotRow(event=row[0], wait_class=row[1], total_waits=row[2] or 0, time_waited_centisecs=row[3] or 0)
        for row in cursor.fetchall()
    ]


def sample_instance_health(conn: OracleConnection) -> InstanceHealthSample:
    status_cursor = conn.cursor()
    status_cursor.execute(_INSTANCE_STATUS_SQL)
    status_row = status_cursor.fetchone()

    count_cursor = conn.cursor()
    count_cursor.execute(_ACTIVE_SESSION_COUNT_SQL)
    count_row = count_cursor.fetchone()

    cpu_cursor = conn.cursor()
    cpu_cursor.execute(_CPU_STAT_SQL)
    cpu_row = cpu_cursor.fetchone()

    return InstanceHealthSample(
        status=status_row[0] if status_row else "UNKNOWN",
        active_session_count=count_row[0] if count_row else 0,
        cpu_centisecs_used=cpu_row[0] if cpu_row else None,
    )
