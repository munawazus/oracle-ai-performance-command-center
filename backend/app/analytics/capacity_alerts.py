"""Deterministic capacity-alert detection (master plan Phase 5, Enterprise Readiness Plan
D.2) - a dedicated comparator against operator-configured thresholds, not the general
per-metric-key hour-of-day baseline engine. Same reasoning app/analytics/sql_regression.py
already gives for SQL regressions applies here: this is a hard, decidable fact given a
threshold, not a statistical outlier.

Unlike SQL regression's fixed ratio ladder (a shape/behavior signal, less site-specific), "how
full is too full" varies enormously by environment and disk-provisioning policy, so the
warn/critical thresholds are passed in by the caller (app/config.py), not hardcoded here.

PCT_USED and PROJECTED_FULL are independent, non-suppressing finding kinds - unlike SQL's
PLAN_CHANGE suppressing ELAPSED_PER_EXEC (same root cause), a tablespace can be nearly full
but flat (no growth alert needed) or well under threshold but growing fast (worth flagging
early) - two separately meaningful facts, not two views of one cause.
"""

from dataclasses import dataclass
from datetime import timedelta

from app.analytics.capacity_metrics import GrowthTrend, TablespaceUsage

# 3x the default 1h collector interval - same "3x the collection interval" ratio D.1 used
# for SQL_REGRESSION_RESOLVE_AFTER against its own 5min interval.
CAPACITY_ALERT_RESOLVE_AFTER = timedelta(hours=3)

ALERT_KIND_PCT_USED = "PCT_USED"
ALERT_KIND_PROJECTED_FULL = "PROJECTED_FULL"


@dataclass(frozen=True)
class CapacityFinding:
    tablespace_name: str
    alert_kind: str
    severity: str
    pct_used: float | None
    # Which denominator pct_used is measured against - only meaningful for PCT_USED findings
    # (PROJECTED_FULL's days-to-full is always computed against max size already, never
    # ambiguous). None for a PROJECTED_FULL finding, or an alert persisted before this field
    # existed - never guessed.
    pct_basis: str | None
    projected_days_to_full: float | None
    current_used_bytes: int | None
    current_allocated_bytes: int | None
    current_max_bytes: int | None
    detection_note: str


def classify_pct_used_severity(pct_used: float, *, warn_pct: float, critical_pct: float) -> str | None:
    """None below warn_pct - not flagged as an alert at all."""
    if pct_used >= critical_pct:
        return "CRITICAL"
    if pct_used >= warn_pct:
        return "HIGH"
    return None


def classify_days_to_full_severity(days: float, *, warn_days: float, critical_days: float) -> str | None:
    """None above warn_days - not flagged as an alert at all."""
    if days <= critical_days:
        return "CRITICAL"
    if days <= warn_days:
        return "HIGH"
    return None


def _detect_pct_used(usage: TablespaceUsage, *, warn_pct: float, critical_pct: float) -> CapacityFinding | None:
    """Prefers % of the tablespace's real max size (pct_of_max) when one is known - % of the
    datafile's current allocated size alone cries wolf for an autoextensible tablespace with
    real headroom (e.g. 98% of a 490MB allocation that could still grow to 32GB). Falls back
    to % of allocated only when there's no known max size at all (autoextend disabled, or an
    effectively unbounded ceiling) - the one case where allocated genuinely IS the practical
    limit, so there's no second number to prefer."""
    has_max = usage.pct_of_max is not None
    effective_pct = usage.pct_of_max if has_max else usage.pct_used
    assert effective_pct is not None  # pct_used is never None; pct_of_max is checked above
    severity = classify_pct_used_severity(effective_pct, warn_pct=warn_pct, critical_pct=critical_pct)
    if severity is None:
        return None
    basis = "max size" if has_max else "allocated"
    basis_bytes = usage.max_bytes if has_max else usage.allocated_bytes
    return CapacityFinding(
        tablespace_name=usage.tablespace_name,
        alert_kind=ALERT_KIND_PCT_USED,
        severity=severity,
        pct_used=effective_pct,
        pct_basis=basis,
        projected_days_to_full=None,
        current_used_bytes=usage.used_bytes,
        current_allocated_bytes=usage.allocated_bytes,
        current_max_bytes=usage.max_bytes,
        detection_note=(
            f"Tablespace {usage.tablespace_name} is {effective_pct:.1f}% used of {basis} "
            f"({usage.used_bytes} of {basis_bytes} bytes)."
        ),
    )


def _detect_projected_full(
    usage: TablespaceUsage, trend: GrowthTrend, *, warn_days: float, critical_days: float
) -> CapacityFinding | None:
    if not trend.available or trend.projected_days_to_full is None or trend.bytes_per_day is None:
        return None
    severity = classify_days_to_full_severity(
        trend.projected_days_to_full, warn_days=warn_days, critical_days=critical_days
    )
    if severity is None:
        return None
    return CapacityFinding(
        tablespace_name=usage.tablespace_name,
        alert_kind=ALERT_KIND_PROJECTED_FULL,
        severity=severity,
        pct_used=usage.pct_used,
        pct_basis=None,  # not applicable - projected_days_to_full is always max-size-relative
        projected_days_to_full=trend.projected_days_to_full,
        current_used_bytes=usage.used_bytes,
        current_allocated_bytes=usage.allocated_bytes,
        current_max_bytes=usage.max_bytes,
        detection_note=(
            f"Tablespace {usage.tablespace_name} is growing at {trend.bytes_per_day:.0f} bytes/day "
            f"and is projected to reach its capacity ceiling in {trend.projected_days_to_full:.1f} day(s)."
        ),
    )


def detect_capacity_alerts(
    usages_with_trend: list[tuple[TablespaceUsage, GrowthTrend]],
    *,
    warn_pct: float,
    critical_pct: float,
    warn_days: float,
    critical_days: float,
) -> list[CapacityFinding]:
    """usages_with_trend: one (TablespaceUsage, GrowthTrend) pair per tablespace in the
    latest round. Both finding kinds are evaluated independently per tablespace - neither
    suppresses the other, unlike SQL regression's plan-change/elapsed-per-exec suppression."""
    findings: list[CapacityFinding] = []
    for usage, trend in usages_with_trend:
        pct_finding = _detect_pct_used(usage, warn_pct=warn_pct, critical_pct=critical_pct)
        if pct_finding is not None:
            findings.append(pct_finding)
        projected_finding = _detect_projected_full(usage, trend, warn_days=warn_days, critical_days=critical_days)
        if projected_finding is not None:
            findings.append(projected_finding)
    return findings
