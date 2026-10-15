from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.analytics.capacity_metrics import MIN_SAMPLES_FOR_TREND, compute_growth_trend, describe_usage

NOW = datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc)


@dataclass
class _Row:
    tablespace_name: str
    contents: str
    sampled_at: datetime
    allocated_bytes: int
    used_bytes: int
    max_bytes: int | None
    file_count: int


def _row(sampled_at, used_bytes, allocated_bytes=1000, max_bytes=10_000, **overrides):
    defaults = dict(
        tablespace_name="USERS",
        contents="PERMANENT",
        sampled_at=sampled_at,
        allocated_bytes=allocated_bytes,
        used_bytes=used_bytes,
        max_bytes=max_bytes,
        file_count=1,
    )
    defaults.update(overrides)
    return _Row(**defaults)


def test_describe_usage_computes_pct_used_and_pct_of_max():
    row = _row(NOW, used_bytes=500, allocated_bytes=1000, max_bytes=2000)

    usage = describe_usage(row)

    assert usage.pct_used == 50.0
    assert usage.pct_of_max == 25.0


def test_describe_usage_pct_of_max_is_none_with_no_known_ceiling():
    row = _row(NOW, used_bytes=500, allocated_bytes=1000, max_bytes=None)

    usage = describe_usage(row)

    assert usage.pct_of_max is None


def test_describe_usage_pct_used_is_zero_when_allocated_is_zero():
    row = _row(NOW, used_bytes=0, allocated_bytes=0)

    usage = describe_usage(row)

    assert usage.pct_used == 0.0


def test_growth_trend_unavailable_with_fewer_than_min_samples():
    samples = [_row(NOW - timedelta(days=i), used_bytes=100 * i) for i in range(MIN_SAMPLES_FOR_TREND - 1)]

    trend = compute_growth_trend(samples, NOW, current_used_bytes=100, max_bytes=10_000)

    assert trend.available is False
    assert trend.bytes_per_day is None
    assert trend.projected_days_to_full is None


def test_growth_trend_unavailable_when_all_samples_share_one_timestamp():
    samples = [_row(NOW, used_bytes=100 + i) for i in range(MIN_SAMPLES_FOR_TREND)]

    trend = compute_growth_trend(samples, NOW, current_used_bytes=100, max_bytes=10_000)

    assert trend.available is False


def test_growth_trend_ignores_samples_outside_the_trend_window():
    # Two samples inside the 7-day window, one far outside it (should be excluded, leaving
    # too few for a trend).
    samples = [
        _row(NOW - timedelta(days=100), used_bytes=1),
        _row(NOW - timedelta(days=2), used_bytes=500),
        _row(NOW - timedelta(days=1), used_bytes=600),
    ]

    trend = compute_growth_trend(samples, NOW, current_used_bytes=600, max_bytes=10_000)

    assert trend.available is False


def test_growth_trend_reports_not_growing_for_flat_usage():
    samples = [_row(NOW - timedelta(days=i), used_bytes=500) for i in range(3, -1, -1)]

    trend = compute_growth_trend(samples, NOW, current_used_bytes=500, max_bytes=10_000)

    assert trend.available is True
    assert trend.bytes_per_day is not None
    assert trend.bytes_per_day <= 0
    assert trend.projected_days_to_full is None
    assert trend.projection_reason == "not growing"


def test_growth_trend_reports_no_known_ceiling_when_max_bytes_is_none():
    samples = [_row(NOW - timedelta(days=i), used_bytes=1000 - 100 * i, max_bytes=None) for i in range(3, -1, -1)]

    trend = compute_growth_trend(samples, NOW, current_used_bytes=1000, max_bytes=None)

    assert trend.available is True
    assert trend.bytes_per_day is not None
    assert trend.bytes_per_day > 0
    assert trend.projected_days_to_full is None
    assert trend.projection_reason == "no known ceiling"


def test_growth_trend_projects_days_to_full_from_a_positive_linear_trend():
    # used_bytes grows by 100/day for 4 days: 700, 800, 900, 1000 - ceiling at 1400, so ~4
    # more days at the same rate from the current 1000.
    samples = [_row(NOW - timedelta(days=i), used_bytes=1000 - 100 * i) for i in range(3, -1, -1)]

    trend = compute_growth_trend(samples, NOW, current_used_bytes=1000, max_bytes=1400)

    assert trend.available is True
    assert trend.projection_reason is None
    assert trend.bytes_per_day == 100.0
    assert trend.projected_days_to_full == 4.0


def test_growth_trend_reports_zero_days_when_already_at_or_over_the_ceiling():
    samples = [_row(NOW - timedelta(days=i), used_bytes=1000 - 100 * i) for i in range(3, -1, -1)]

    trend = compute_growth_trend(samples, NOW, current_used_bytes=1000, max_bytes=900)

    assert trend.available is True
    assert trend.projected_days_to_full == 0.0
    assert trend.projection_reason is None
