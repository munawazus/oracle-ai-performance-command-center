from dataclasses import dataclass

from app.analytics.sql_metrics import aggregate_window, compute_sql_deltas, rank_sql_deltas


@dataclass
class _Row:
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


def _row(sql_id="SQL1", plan=111, executions=10, elapsed=1000, **overrides):
    defaults = dict(
        sql_id=sql_id,
        plan_hash_value=plan,
        executions=executions,
        elapsed_time_us=elapsed,
        cpu_time_us=500,
        buffer_gets=200,
        disk_reads=5,
        rows_processed=100,
        parse_calls=1,
        child_cursor_count=1,
    )
    defaults.update(overrides)
    return _Row(**defaults)


def test_computes_deltas_between_two_rounds_for_the_same_key():
    previous = [_row(executions=10, elapsed=1000)]
    current = [_row(executions=15, elapsed=2500)]

    deltas = compute_sql_deltas(previous, current, elapsed_seconds=10.0)

    assert len(deltas) == 1
    assert deltas[0].executions == 5
    assert deltas[0].elapsed_time_us == 1500
    assert deltas[0].elapsed_per_exec_us == 300.0


def test_key_absent_from_previous_round_contributes_nothing():
    previous = [_row(sql_id="OLD")]
    current = [_row(sql_id="NEW"), _row(sql_id="OLD", executions=20, elapsed=2000)]

    deltas = compute_sql_deltas(previous, current, elapsed_seconds=10.0)

    assert {d.sql_id for d in deltas} == {"OLD"}


def test_different_plan_hash_is_a_different_key_not_a_delta():
    previous = [_row(plan=111, executions=10, elapsed=1000)]
    current = [_row(plan=222, executions=5, elapsed=400)]  # a reloaded cursor, new plan

    deltas = compute_sql_deltas(previous, current, elapsed_seconds=10.0)

    assert deltas == []


def test_any_negative_counter_delta_drops_the_whole_key():
    previous = [_row(executions=100, elapsed=50_000, buffer_gets=9000)]
    current = [_row(executions=5, elapsed=100, buffer_gets=10)]  # cursor reload, counters reset

    deltas = compute_sql_deltas(previous, current, elapsed_seconds=10.0)

    assert deltas == []


def test_zero_or_negative_elapsed_seconds_returns_nothing():
    previous = [_row(executions=10)]
    current = [_row(executions=20)]

    assert compute_sql_deltas(previous, current, elapsed_seconds=0) == []
    assert compute_sql_deltas(previous, current, elapsed_seconds=-1) == []


def test_rank_sql_deltas_sorts_descending_by_the_requested_dimension():
    previous = [_row(sql_id="A", elapsed=0, buffer_gets=0), _row(sql_id="B", elapsed=0, buffer_gets=0)]
    current = [
        _row(sql_id="A", executions=20, elapsed=100, buffer_gets=9000),
        _row(sql_id="B", executions=20, elapsed=5000, buffer_gets=10),
    ]

    by_elapsed = rank_sql_deltas(compute_sql_deltas(previous, current, 10.0), sort_by="elapsed_time", limit=10)
    by_gets = rank_sql_deltas(compute_sql_deltas(previous, current, 10.0), sort_by="buffer_gets", limit=10)

    assert [d.sql_id for d in by_elapsed] == ["B", "A"]
    assert [d.sql_id for d in by_gets] == ["A", "B"]


def test_rank_sql_deltas_falls_back_to_elapsed_time_for_an_unknown_sort_key():
    previous = [_row(elapsed=0)]
    current = [_row(elapsed=100)]

    deltas = rank_sql_deltas(compute_sql_deltas(previous, current, 10.0), sort_by="not_a_real_key", limit=10)

    assert len(deltas) == 1  # doesn't raise


def test_rank_sql_deltas_respects_limit():
    previous = [_row(sql_id=f"S{i}", elapsed=0) for i in range(5)]
    current = [_row(sql_id=f"S{i}", executions=10, elapsed=100 * (i + 1)) for i in range(5)]

    ranked = rank_sql_deltas(compute_sql_deltas(previous, current, 10.0), sort_by="elapsed_time", limit=2)

    assert len(ranked) == 2
    assert ranked[0].sql_id == "S4"


def test_aggregate_window_sums_positive_deltas_across_consecutive_rounds():
    rounds = [
        [_row(executions=10, elapsed=1000)],
        [_row(executions=15, elapsed=2000)],  # +5 execs, +1000us
        [_row(executions=25, elapsed=4000)],  # +10 execs, +2000us
    ]

    totals = aggregate_window(rounds)

    assert totals["SQL1"][111].executions == 15
    assert totals["SQL1"][111].elapsed_time_us == 3000
    assert totals["SQL1"][111].round_count == 2


def test_aggregate_window_drops_a_negative_delta_from_a_mid_window_cursor_reload():
    rounds = [
        [_row(executions=100, elapsed=50_000)],
        [_row(executions=5, elapsed=100)],  # reload mid-window: dropped, not undercounted
        [_row(executions=20, elapsed=1000)],  # +15 execs, +900us vs the reloaded round
    ]

    totals = aggregate_window(rounds)

    assert totals["SQL1"][111].executions == 15
    assert totals["SQL1"][111].elapsed_time_us == 900
    assert totals["SQL1"][111].round_count == 1
