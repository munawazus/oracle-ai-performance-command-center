from dataclasses import dataclass

from app.analytics.metrics import aggregate_by_wait_class, compute_wait_class_rates


@dataclass
class _Row:
    wait_class: str | None
    total_waits: int
    time_waited_centisecs: int


def test_aggregate_sums_multiple_events_within_a_class():
    rows = [
        _Row("User I/O", 10, 100),
        _Row("User I/O", 5, 50),
        _Row("Commit", 2, 20),
    ]
    totals = aggregate_by_wait_class(rows)
    assert totals["User I/O"].total_waits == 15
    assert totals["User I/O"].time_waited_centisecs == 150
    assert totals["Commit"].time_waited_centisecs == 20


def test_aggregate_buckets_null_class_as_unclassified():
    totals = aggregate_by_wait_class([_Row(None, 1, 10)])
    assert "Unclassified" in totals


def test_compute_rates_divides_delta_by_elapsed_seconds():
    previous = aggregate_by_wait_class([_Row("User I/O", 100, 1000)])
    current = aggregate_by_wait_class([_Row("User I/O", 150, 3500)])

    rates = compute_wait_class_rates(previous, current, elapsed_seconds=25.0)
    assert rates["User I/O"] == (3500 - 1000) / 25.0


def test_compute_rates_skips_class_with_no_prior_round():
    previous = aggregate_by_wait_class([_Row("User I/O", 100, 1000)])
    current = aggregate_by_wait_class([_Row("User I/O", 150, 1500), _Row("Network", 5, 50)])

    rates = compute_wait_class_rates(previous, current, elapsed_seconds=10.0)
    assert "Network" not in rates
    assert "User I/O" in rates


def test_compute_rates_drops_negative_delta_from_counter_reset():
    previous = aggregate_by_wait_class([_Row("User I/O", 100, 5000)])
    current = aggregate_by_wait_class([_Row("User I/O", 2, 10)])  # instance bounced, counters reset

    rates = compute_wait_class_rates(previous, current, elapsed_seconds=10.0)
    assert "User I/O" not in rates


def test_compute_rates_returns_empty_for_non_positive_elapsed_time():
    previous = aggregate_by_wait_class([_Row("User I/O", 100, 1000)])
    current = aggregate_by_wait_class([_Row("User I/O", 150, 1500)])

    assert compute_wait_class_rates(previous, current, elapsed_seconds=0) == {}
    assert compute_wait_class_rates(previous, current, elapsed_seconds=-5) == {}
