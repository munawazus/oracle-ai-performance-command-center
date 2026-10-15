"""Pure computation over raw tablespace facts - no I/O. Protocol-based duck typing (same
pattern as app/analytics/sql_metrics.py) so both real TablespaceSample rows and plain test
doubles work identically.

pct_used, growth rate, and time-to-full projection are all computed here, never stored - the
raw allocated/used/max bytes are the only facts TablespaceSample persists (see that model's
docstring for why used_bytes itself still counts as "a fact," not a derived metric).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol, Sequence

# Named v1 defaults, in the same spirit as baseline_engine.MIN_SAMPLES_FOR_ANOMALY - revisit
# once real telemetry volume shows what actually makes a stable trend.
MIN_SAMPLES_FOR_TREND = 3
GROWTH_TREND_WINDOW = timedelta(days=7)


def _ensure_aware(dt: datetime) -> datetime:
    """Some drivers (notably SQLite, used in this backend's test suite) hand back naive
    datetimes even for tz-aware columns - treat a naive value as UTC rather than let it blow
    up a comparison against `now` (same rule as app.models.base.ensure_aware, duplicated here
    rather than imported so this module stays a pure computation with zero app.models/ORM
    coupling, matching app/analytics/sql_metrics.py's own zero-dependency stance)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class TablespaceSampleLike(Protocol):
    tablespace_name: str
    contents: str
    sampled_at: datetime
    allocated_bytes: int
    used_bytes: int
    max_bytes: int | None
    file_count: int


@dataclass(frozen=True)
class TablespaceUsage:
    tablespace_name: str
    contents: str
    allocated_bytes: int
    used_bytes: int
    max_bytes: int | None
    file_count: int
    pct_used: float
    pct_of_max: float | None


def describe_usage(row: TablespaceSampleLike) -> TablespaceUsage:
    pct_used = (row.used_bytes / row.allocated_bytes * 100.0) if row.allocated_bytes > 0 else 0.0
    pct_of_max = (row.used_bytes / row.max_bytes * 100.0) if row.max_bytes else None
    return TablespaceUsage(
        tablespace_name=row.tablespace_name,
        contents=row.contents,
        allocated_bytes=row.allocated_bytes,
        used_bytes=row.used_bytes,
        max_bytes=row.max_bytes,
        file_count=row.file_count,
        pct_used=pct_used,
        pct_of_max=pct_of_max,
    )


@dataclass(frozen=True)
class GrowthTrend:
    available: bool
    reason: str | None
    bytes_per_day: float | None
    projected_days_to_full: float | None
    projection_reason: str | None


def compute_growth_trend(
    samples: Sequence[TablespaceSampleLike],
    now: datetime,
    *,
    current_used_bytes: int,
    max_bytes: int | None,
) -> GrowthTrend:
    """Deterministic linear least-squares slope of used_bytes vs. elapsed time over
    GROWTH_TREND_WINDOW - not ML/statistical, the simplest deterministic method for a numeric
    trend. `available=False` only when a rate genuinely can't be computed (too few samples,
    or all samples at the same timestamp); a computable-but-flat/shrinking rate, or a rate
    with no known ceiling to project against, is `available=True` with `projected_days_to_full
    =None` and an explicit `projection_reason` - never a fabricated or divide-by-zero ETA."""
    window_start = now - GROWTH_TREND_WINDOW
    windowed = [s for s in samples if _ensure_aware(s.sampled_at) >= window_start]
    if len(windowed) < MIN_SAMPLES_FOR_TREND:
        return GrowthTrend(
            available=False,
            reason=f"fewer than {MIN_SAMPLES_FOR_TREND} samples in the last "
            f"{GROWTH_TREND_WINDOW.days} days - no trend can be computed yet",
            bytes_per_day=None,
            projected_days_to_full=None,
            projection_reason=None,
        )

    x0 = windowed[0].sampled_at
    xs = [(s.sampled_at - x0).total_seconds() / 3600.0 for s in windowed]
    ys = [float(s.used_bytes) for s in windowed]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return GrowthTrend(
            available=False,
            reason="all samples in the trend window share the same timestamp",
            bytes_per_day=None,
            projected_days_to_full=None,
            projection_reason=None,
        )

    slope_per_hour = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    bytes_per_day = slope_per_hour * 24.0

    if bytes_per_day <= 0:
        return GrowthTrend(True, None, bytes_per_day, None, "not growing")
    if max_bytes is None:
        return GrowthTrend(True, None, bytes_per_day, None, "no known ceiling")

    remaining_bytes = max_bytes - current_used_bytes
    if remaining_bytes <= 0:
        return GrowthTrend(True, None, bytes_per_day, 0.0, None)
    return GrowthTrend(True, None, bytes_per_day, remaining_bytes / bytes_per_day, None)
