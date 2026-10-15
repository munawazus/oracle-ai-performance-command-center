"""SQL Intelligence collection (master plan Phase 9) - top SQL statistics and execution
plans, from V$SQL and V$SQL_PLAN. Both are base dynamic performance views, not
Diagnostics/Tuning Pack views - unlike DBA_HIST_SQLSTAT/DBA_HIST_SQL_PLAN/V$SQL_MONITOR
(already in app/oracle/allowlist.py's BLOCKLISTED_VIEWS), which this module never touches.

V$SQL, not V$SQLAREA, is the stats source: V$SQLAREA aggregates away PLAN_HASH_VALUE, which
is the entire basis of plan-change detection. Grain here is (sql_id, plan_hash_value),
aggregated across child cursors server-side - a child cursor can age out of the shared pool
and reload with its own counters reset to zero, so child_number is not a usable diffing key.

Deliberately never collected, each for a stated reason:
- V$SQL_BIND_CAPTURE / V$SQL_PLAN's ACCESS_PREDICATES/FILTER_PREDICATES: can embed literal
  application data values (e.g. "EMPNO=12345") - a real PII/data-exfiltration surface, not
  needed for top-SQL ranking, plan-change detection, or plan history.
- V$SQL_PLAN_STATISTICS(_ALL): normally empty unless STATISTICS_LEVEL=ALL or a
  GATHER_PLAN_STATISTICS hint was used - would silently collect nothing on a typical
  instance, the worst failure mode for this platform (looks like a working feature,
  produces no evidence).
- V$SQL.SQL_FULLTEXT (a CLOB) and LAST_ACTIVE_TIME/FIRST_LOAD_TIME (an Oracle DATE and a
  VARCHAR2 date string, neither with a reliable timezone to convert against): the
  deliberately minimal OracleCursor protocol (app/oracle/connector.py) has no LOB handling,
  and this platform's own sampled_at is already correctly timezone-aware and authoritative.
"""

from dataclasses import dataclass

from app.oracle.connector import OracleConnection

_TOP_SQL_SQL_TEMPLATE = (
    "SELECT * FROM ("
    "  SELECT s.sql_id, s.plan_hash_value,"
    "         SUM(s.executions) executions, SUM(s.elapsed_time) elapsed_time,"
    "         SUM(s.cpu_time) cpu_time, SUM(s.buffer_gets) buffer_gets,"
    "         SUM(s.disk_reads) disk_reads, SUM(s.rows_processed) rows_processed,"
    "         SUM(s.parse_calls) parse_calls, COUNT(*) child_cursor_count,"
    "         MAX(s.parsing_schema_name) parsing_schema_name, MAX(s.sql_text) sql_text"
    "  FROM v$sql s"
    "  WHERE s.executions > 0"
    "  GROUP BY s.sql_id, s.plan_hash_value"
    "  ORDER BY SUM(s.elapsed_time) DESC"
    ") WHERE ROWNUM <= {top_n}"
)

_SQL_PLAN_SQL_TEMPLATE = (
    "SELECT DISTINCT p.sql_id, p.plan_hash_value, p.id, p.parent_id, p.depth, p.position,"
    "       p.operation, p.options, p.object_owner, p.object_name, p.object_type,"
    "       p.cost, p.cardinality, p.bytes "
    "FROM v$sql_plan p "
    "WHERE (p.sql_id, p.plan_hash_value) IN ("
    "  SELECT sql_id, plan_hash_value FROM ("
    "    SELECT s.sql_id, s.plan_hash_value"
    "    FROM v$sql s WHERE s.executions > 0"
    "    GROUP BY s.sql_id, s.plan_hash_value"
    "    ORDER BY SUM(s.elapsed_time) DESC"
    "  ) WHERE ROWNUM <= {top_n}"
    ") "
    "ORDER BY p.sql_id, p.plan_hash_value, p.id"
)


def _validated_top_n(top_n: int) -> int:
    if not isinstance(top_n, int) or not (0 < top_n <= 500):
        raise ValueError(f"top_n must be an int in (0, 500], got {top_n!r}")
    return top_n


@dataclass(frozen=True)
class SqlStatRow:
    sql_id: str
    plan_hash_value: int
    executions: int
    elapsed_time_us: int
    cpu_time_us: int
    buffer_gets: int
    disk_reads: int
    rows_processed: int
    parse_calls: int
    child_cursor_count: int
    parsing_schema_name: str | None
    sql_text: str | None


@dataclass(frozen=True)
class SqlPlanLineRow:
    id: int
    parent_id: int | None
    depth: int | None
    position: int | None
    operation: str | None
    options: str | None
    object_owner: str | None
    object_name: str | None
    object_type: str | None
    cost: int | None
    cardinality: int | None
    bytes: int | None


@dataclass(frozen=True)
class SqlPlanRow:
    sql_id: str
    plan_hash_value: int
    lines: tuple[SqlPlanLineRow, ...]


def sample_top_sql(conn: OracleConnection, top_n: int) -> list[SqlStatRow]:
    top_n = _validated_top_n(top_n)
    cursor = conn.cursor()
    # top_n is validated above (a bounded int, never request-derived) before interpolation -
    # same guard pattern as app/oracle/capability_probe.py's `# nosec B608` usage.
    cursor.execute(_TOP_SQL_SQL_TEMPLATE.format(top_n=top_n))  # nosec B608
    return [
        SqlStatRow(
            sql_id=row[0],
            plan_hash_value=row[1] or 0,
            executions=row[2] or 0,
            elapsed_time_us=row[3] or 0,
            cpu_time_us=row[4] or 0,
            buffer_gets=row[5] or 0,
            disk_reads=row[6] or 0,
            rows_processed=row[7] or 0,
            parse_calls=row[8] or 0,
            child_cursor_count=row[9] or 0,
            parsing_schema_name=row[10],
            sql_text=row[11],
        )
        for row in cursor.fetchall()
    ]


def sample_sql_plans(conn: OracleConnection, top_n: int) -> list[SqlPlanRow]:
    top_n = _validated_top_n(top_n)
    cursor = conn.cursor()
    cursor.execute(_SQL_PLAN_SQL_TEMPLATE.format(top_n=top_n))  # nosec B608

    # Two child cursors can share one plan_hash_value, so the same plan line can appear more
    # than once - dedupe on (sql_id, plan_hash_value, id) with a first-wins rule. This is
    # deduplication of identical rows, not the query-level diagnostic filtering convention
    # (excluding idle waits, background sessions) used elsewhere in this module.
    seen: set[tuple[str, int, int]] = set()
    lines_by_plan: dict[tuple[str, int], list[SqlPlanLineRow]] = {}
    order: list[tuple[str, int]] = []
    for row in cursor.fetchall():
        sql_id, plan_hash_value, line_id = row[0], row[1] or 0, row[2]
        key = (sql_id, plan_hash_value, line_id)
        if key in seen:
            continue
        seen.add(key)
        plan_key = (sql_id, plan_hash_value)
        if plan_key not in lines_by_plan:
            lines_by_plan[plan_key] = []
            order.append(plan_key)
        lines_by_plan[plan_key].append(
            SqlPlanLineRow(
                id=line_id,
                parent_id=row[3],
                depth=row[4],
                position=row[5],
                operation=row[6],
                options=row[7],
                object_owner=row[8],
                object_name=row[9],
                object_type=row[10],
                cost=row[11],
                cardinality=row[12],
                bytes=row[13],
            )
        )

    return [
        SqlPlanRow(sql_id=sql_id, plan_hash_value=plan_hash_value, lines=tuple(lines_by_plan[(sql_id, plan_hash_value)]))
        for sql_id, plan_hash_value in order
    ]
