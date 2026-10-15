from app.analytics.capacity_alerts import (
    ALERT_KIND_PCT_USED,
    ALERT_KIND_PROJECTED_FULL,
    classify_days_to_full_severity,
    classify_pct_used_severity,
    detect_capacity_alerts,
)
from app.analytics.capacity_metrics import GrowthTrend, TablespaceUsage

WARN_PCT = 85.0
CRITICAL_PCT = 95.0
WARN_DAYS = 30.0
CRITICAL_DAYS = 7.0

_NO_TREND = GrowthTrend(available=False, reason="insufficient history", bytes_per_day=None, projected_days_to_full=None, projection_reason=None)


def _usage(name="USERS", pct_used=50.0, **overrides):
    # pct_of_max defaults to None (no known max size) - every existing test in this file is
    # about the fallback-to-allocated behavior specifically, not the max-size-preference
    # behavior (see the dedicated "pct_of_max preference" section below for that).
    defaults = dict(
        tablespace_name=name,
        contents="PERMANENT",
        allocated_bytes=1000,
        used_bytes=int(1000 * pct_used / 100),
        max_bytes=2000,
        file_count=1,
        pct_used=pct_used,
        pct_of_max=None,
    )
    defaults.update(overrides)
    return TablespaceUsage(**defaults)


def _trend(days_to_full, bytes_per_day=100.0):
    return GrowthTrend(
        available=True, reason=None, bytes_per_day=bytes_per_day,
        projected_days_to_full=days_to_full, projection_reason=None,
    )


# -- severity classification --------------------------------------------------------------


def test_pct_used_severity_boundaries():
    assert classify_pct_used_severity(84.9, warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT) is None
    assert classify_pct_used_severity(85.0, warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT) == "HIGH"
    assert classify_pct_used_severity(94.9, warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT) == "HIGH"
    assert classify_pct_used_severity(95.0, warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT) == "CRITICAL"


def test_days_to_full_severity_boundaries():
    assert classify_days_to_full_severity(30.1, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS) is None
    assert classify_days_to_full_severity(30.0, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS) == "HIGH"
    assert classify_days_to_full_severity(7.1, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS) == "HIGH"
    assert classify_days_to_full_severity(7.0, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS) == "CRITICAL"


# -- PCT_USED signal -------------------------------------------------------------------------


def test_pct_used_alert_fires_above_the_warn_threshold():
    findings = detect_capacity_alerts(
        [(_usage(pct_used=90.0), _NO_TREND)],
        warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS,
    )

    assert len(findings) == 1
    assert findings[0].alert_kind == ALERT_KIND_PCT_USED
    assert findings[0].severity == "HIGH"


def test_pct_used_alert_does_not_fire_below_the_warn_threshold():
    findings = detect_capacity_alerts(
        [(_usage(pct_used=50.0), _NO_TREND)],
        warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS,
    )

    assert findings == []


# -- PROJECTED_FULL signal ------------------------------------------------------------------


def test_projected_full_alert_fires_when_days_to_full_crosses_the_warn_threshold():
    findings = detect_capacity_alerts(
        [(_usage(pct_used=50.0), _trend(days_to_full=5.0))],
        warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS,
    )

    assert len(findings) == 1
    assert findings[0].alert_kind == ALERT_KIND_PROJECTED_FULL
    assert findings[0].severity == "CRITICAL"
    assert findings[0].pct_basis is None  # not applicable - days-to-full is never ambiguous


def test_projected_full_alert_does_not_fire_when_trend_is_unavailable():
    findings = detect_capacity_alerts(
        [(_usage(pct_used=50.0), _NO_TREND)],
        warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS,
    )

    assert findings == []


def test_projected_full_alert_does_not_fire_when_projection_has_no_eta():
    not_growing = GrowthTrend(
        available=True, reason=None, bytes_per_day=-10.0, projected_days_to_full=None, projection_reason="not growing"
    )
    findings = detect_capacity_alerts(
        [(_usage(pct_used=50.0), not_growing)],
        warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS,
    )

    assert findings == []


# -- independence: neither kind suppresses the other ----------------------------------------


def test_both_kinds_fire_independently_for_the_same_tablespace():
    # 96% full (PCT_USED) AND projected to run out in 3 days (PROJECTED_FULL) - two separate,
    # non-redundant facts, unlike SQL regression's plan-change/elapsed-per-exec suppression.
    findings = detect_capacity_alerts(
        [(_usage(pct_used=96.0), _trend(days_to_full=3.0))],
        warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS,
    )

    assert len(findings) == 2
    assert {f.alert_kind for f in findings} == {ALERT_KIND_PCT_USED, ALERT_KIND_PROJECTED_FULL}


def test_pct_used_high_but_flat_growth_only_fires_pct_used():
    findings = detect_capacity_alerts(
        [(_usage(pct_used=96.0), _NO_TREND)],
        warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS,
    )

    assert len(findings) == 1
    assert findings[0].alert_kind == ALERT_KIND_PCT_USED


# -- % of max size preferred over % of allocated, when a real max size is known -------------


def test_pct_used_alert_does_not_fire_from_a_high_allocated_pct_when_max_size_is_known_and_low():
    # The real case this fixes: 96% of a small current allocation, but only 4.8% of a real,
    # much larger max size - an autoextensible tablespace with genuine headroom shouldn't
    # cry wolf just because its current datafile size happens to be nearly full.
    findings = detect_capacity_alerts(
        [(_usage(pct_used=96.0, pct_of_max=4.8), _NO_TREND)],
        warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS,
    )

    assert findings == []


def test_pct_used_alert_fires_from_pct_of_max_even_when_allocated_pct_is_low():
    # The inverse, proving this is genuinely max-size-driven, not just "max size suppresses
    # allocated": a tablespace can be nearly at its real ceiling while its current allocation
    # reads low, if the ceiling itself is close to already-allocated.
    findings = detect_capacity_alerts(
        [(_usage(pct_used=30.0, pct_of_max=96.0), _NO_TREND)],
        warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS,
    )

    assert len(findings) == 1
    assert findings[0].severity == "CRITICAL"
    assert findings[0].pct_used == 96.0  # the finding's own pct_used carries the *basis actually used*
    assert findings[0].pct_basis == "max size"
    assert "max size" in findings[0].detection_note


def test_pct_used_alert_falls_back_to_pct_of_allocated_when_no_known_max_size():
    findings = detect_capacity_alerts(
        [(_usage(pct_used=90.0, pct_of_max=None), _NO_TREND)],
        warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS,
    )

    assert len(findings) == 1
    assert findings[0].pct_used == 90.0
    assert findings[0].pct_basis == "allocated"
    assert "allocated" in findings[0].detection_note


def test_multiple_tablespaces_are_judged_independently():
    findings = detect_capacity_alerts(
        [
            (_usage(name="SYSTEM", pct_used=10.0), _NO_TREND),
            (_usage(name="USERS", pct_used=99.0), _NO_TREND),
        ],
        warn_pct=WARN_PCT, critical_pct=CRITICAL_PCT, warn_days=WARN_DAYS, critical_days=CRITICAL_DAYS,
    )

    assert len(findings) == 1
    assert findings[0].tablespace_name == "USERS"
