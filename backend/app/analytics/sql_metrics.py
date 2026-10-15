"""Pure computation over raw SQL telemetry rows - no I/O. Duck-typed via Protocol (same
pattern as app/analytics/metrics.py) so both real SqlStatSample rows and plain test doubles
work identically.

V$SQL counters are cumulative per (sql_id, plan_hash_value) cursor grouping, exactly like
V$SYSTEM_EVENT - a rate/delta always needs two rounds to diff against, and this module never
stores a delta, only computes one on demand from two rounds of raw facts (the storage
convention stated in app/models/sql_telemetry.py's SqlStatSample docstring).
"""

from dataclasses import dataclass
from typing import Protocol, Sequence

SORT_KEYS: tuple[str, ...] = (
    "elapsed_time",
    "cpu_time",
    "buffer_gets",
    "disk_reads",
    "executions",
    "elapsed_per_exec",
)


class SqlStatRowLike(Protocol):
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


@dataclass(frozen=True)
class SqlKey:
    sql_id: str
    plan_hash_value: int


@dataclass(frozen=True)
class SqlStatDelta:
    sql_id: str
    plan_hash_value: int
    child_cursor_count: int
    executions: int
    elapsed_time_us: int
    cpu_time_us: int
    buffer_gets: int
    disk_reads: int
    rows_processed: int
    parse_calls: int

    @property
    def elapsed_per_exec_us(self) -> float | None:
        return self.elapsed_time_us / self.executions if self.executions > 0 else None

    def rate_per_second(self, field: str, elapsed_seconds: float) -> float:
        return getattr(self, field) / elapsed_seconds if elapsed_seconds > 0 else 0.0


def _index_by_key(rows: Sequence[SqlStatRowLike]) -> dict[SqlKey, SqlStatRowLike]:
    return {SqlKey(row.sql_id, row.plan_hash_value): row for row in rows}


def compute_sql_deltas(
    previous: Sequence[SqlStatRowLike], current: Sequence[SqlStatRowLike], elapsed_seconds: float
) -> list[SqlStatDelta]:
    """Per-(sql_id, plan_hash_value) deltas between two rounds. A key absent from `previous`
    (new this round, or a reloaded cursor with a different plan hash) contributes nothing -
    there's nothing to diff against yet, same rule as compute_wait_class_rates. Any single
    counter going backwards (a cursor aged out and reloaded into the same child slot with
    counters reset to zero) drops the whole key rather than reporting a negative or clamped
    value, for the same reason: 0 would misleadingly assert "no work happened".

    Consequence, confirmed empirically (docs/QA_LIVE_FINDINGS.md, Finding 8): a SQL statement
    that starts and completely finishes within a single collector interval never appears in
    Top SQL, no matter how expensive - it needs to still be executing across a full
    tick-to-tick boundary before a round exists to diff it against.
    """
    if elapsed_seconds <= 0:
        return []

    prev_by_key = _index_by_key(previous)
    deltas: list[SqlStatDelta] = []
    for row in current:
        prev = prev_by_key.get(SqlKey(row.sql_id, row.plan_hash_value))
        if prev is None:
            continue

        raw_deltas = {
            "executions": row.executions - prev.executions,
            "elapsed_time_us": row.elapsed_time_us - prev.elapsed_time_us,
            "cpu_time_us": row.cpu_time_us - prev.cpu_time_us,
            "buffer_gets": row.buffer_gets - prev.buffer_gets,
            "disk_reads": row.disk_reads - prev.disk_reads,
            "rows_processed": row.rows_processed - prev.rows_processed,
            "parse_calls": row.parse_calls - prev.parse_calls,
        }
        if any(value < 0 for value in raw_deltas.values()):
            continue

        deltas.append(
            SqlStatDelta(
                sql_id=row.sql_id,
                plan_hash_value=row.plan_hash_value,
                child_cursor_count=row.child_cursor_count,
                **raw_deltas,
            )
        )
    return deltas


def rank_sql_deltas(deltas: list[SqlStatDelta], sort_by: str, limit: int) -> list[SqlStatDelta]:
    if sort_by not in SORT_KEYS:
        sort_by = "elapsed_time"

    if sort_by == "elapsed_per_exec":

        def key(d: SqlStatDelta) -> float:
            return d.elapsed_per_exec_us or 0.0

    else:
        field = f"{sort_by}_us" if sort_by in ("elapsed_time", "cpu_time") else sort_by

        def key(d: SqlStatDelta) -> float:
            return float(getattr(d, field))

    return sorted(deltas, key=key, reverse=True)[:limit]


@dataclass(frozen=True)
class WindowTotals:
    executions: int
    elapsed_time_us: int
    round_count: int

    @property
    def elapsed_per_exec_us(self) -> float | None:
        return self.elapsed_time_us / self.executions if self.executions > 0 else None


def aggregate_window(rows_by_round: Sequence[Sequence[SqlStatRowLike]]) -> dict[str, dict[int, WindowTotals]]:
    """Sums positive consecutive per-round deltas per (sql_id, plan_hash_value) across an
    ordered sequence of rounds (oldest first). Not first-minus-last endpoint subtraction: a
    cursor reload mid-window resets its counters, and endpoint subtraction would silently
    undercount or go negative across that reset. Negative per-round deltas are dropped for
    the same reason compute_sql_deltas drops them.
    """
    totals: dict[str, dict[int, dict[str, int]]] = {}

    def _bump(sql_id: str, plan_hash_value: int, executions: int, elapsed_time_us: int) -> None:
        by_plan = totals.setdefault(sql_id, {})
        entry = by_plan.setdefault(plan_hash_value, {"executions": 0, "elapsed_time_us": 0, "round_count": 0})
        entry["executions"] += executions
        entry["elapsed_time_us"] += elapsed_time_us
        entry["round_count"] += 1

    for previous, current in zip(rows_by_round, rows_by_round[1:]):
        for delta in compute_sql_deltas(previous, current, elapsed_seconds=1.0):
            _bump(delta.sql_id, delta.plan_hash_value, delta.executions, delta.elapsed_time_us)

    return {
        sql_id: {
            plan_hash_value: WindowTotals(
                executions=entry["executions"],
                elapsed_time_us=entry["elapsed_time_us"],
                round_count=entry["round_count"],
            )
            for plan_hash_value, entry in by_plan.items()
        }
        for sql_id, by_plan in totals.items()
    }
