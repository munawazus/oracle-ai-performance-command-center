from datetime import datetime, timedelta, timezone

from app.analytics.config_metrics import ParameterChange
from app.reports.health_check import (
    CATEGORY_ORDER,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_UNAVAILABLE,
    STATUS_WARN,
    run_health_checks,
    summarize_health,
)

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class _Health:
    def __init__(self, reachability: str, sampled_at: datetime):
        self.reachability = reachability
        self.sampled_at = sampled_at


class _Collector:
    def __init__(self, consecutive_failures: int):
        self.consecutive_failures = consecutive_failures


class _Session:
    def __init__(self, sid, username, blocking_session, sql_id=None, sampled_at=NOW):
        self.sid = sid
        self.username = username
        self.blocking_session = blocking_session
        self.sql_id = sql_id
        self.sampled_at = sampled_at


class _Anomaly:
    def __init__(self, severity: str):
        self.severity = severity


class _Regression:
    def __init__(self, severity: str):
        self.severity = severity


def test_every_category_from_the_master_plan_is_always_present():
    checks = run_health_checks(
        latest_health=None, collector_status=None, session_samples=[], recent_anomalies=[], now=NOW
    )
    categories = [c["category"] for c in checks]
    assert categories == CATEGORY_ORDER


def test_unavailable_categories_are_never_reported_as_passing():
    checks = run_health_checks(
        latest_health=None, collector_status=None, session_samples=[], recent_anomalies=[], now=NOW
    )
    by_category = {c["category"]: c for c in checks}
    for category in ("resource_limits", "storage", "operational", "backup", "data_guard", "asm", "configuration"):
        assert by_category[category]["status"] == STATUS_UNAVAILABLE


def test_availability_unavailable_with_no_telemetry():
    checks = run_health_checks(
        latest_health=None, collector_status=None, session_samples=[], recent_anomalies=[], now=NOW
    )
    assert next(c for c in checks if c["category"] == "availability")["status"] == STATUS_UNAVAILABLE


def test_availability_fails_when_last_sample_reports_unreachable():
    checks = run_health_checks(
        latest_health=_Health("unreachable", NOW),
        collector_status=None,
        session_samples=[],
        recent_anomalies=[],
        now=NOW,
    )
    assert next(c for c in checks if c["category"] == "availability")["status"] == STATUS_FAIL


def test_availability_warns_on_stale_sample():
    checks = run_health_checks(
        latest_health=_Health("up", NOW - timedelta(minutes=30)),
        collector_status=None,
        session_samples=[],
        recent_anomalies=[],
        now=NOW,
    )
    assert next(c for c in checks if c["category"] == "availability")["status"] == STATUS_WARN


def test_availability_warns_on_collector_failures_even_if_reachable():
    checks = run_health_checks(
        latest_health=_Health("up", NOW),
        collector_status=_Collector(consecutive_failures=3),
        session_samples=[],
        recent_anomalies=[],
        now=NOW,
    )
    assert next(c for c in checks if c["category"] == "availability")["status"] == STATUS_WARN


def test_availability_passes_when_fresh_reachable_and_healthy_collector():
    checks = run_health_checks(
        latest_health=_Health("up", NOW),
        collector_status=_Collector(consecutive_failures=0),
        session_samples=[],
        recent_anomalies=[],
        now=NOW,
    )
    assert next(c for c in checks if c["category"] == "availability")["status"] == STATUS_PASS


def test_locking_unavailable_with_no_session_samples():
    checks = run_health_checks(
        latest_health=None, collector_status=None, session_samples=[], recent_anomalies=[], now=NOW
    )
    assert next(c for c in checks if c["category"] == "locking")["status"] == STATUS_UNAVAILABLE


def test_locking_warns_when_a_blocking_chain_exists():
    samples = [
        _Session(101, "BLOCKER", None),
        _Session(102, "VICTIM", 101),
    ]
    checks = run_health_checks(
        latest_health=None, collector_status=None, session_samples=samples, recent_anomalies=[], now=NOW
    )
    assert next(c for c in checks if c["category"] == "locking")["status"] == STATUS_WARN


def test_locking_passes_with_no_blocking():
    samples = [_Session(101, "APP_USER", None)]
    checks = run_health_checks(
        latest_health=None, collector_status=None, session_samples=samples, recent_anomalies=[], now=NOW
    )
    assert next(c for c in checks if c["category"] == "locking")["status"] == STATUS_PASS


def test_performance_passes_with_no_anomalies():
    checks = run_health_checks(
        latest_health=None, collector_status=None, session_samples=[], recent_anomalies=[], now=NOW
    )
    assert next(c for c in checks if c["category"] == "performance")["status"] == STATUS_PASS


def test_performance_warns_on_low_severity_anomaly():
    checks = run_health_checks(
        latest_health=None, collector_status=None, session_samples=[], recent_anomalies=[_Anomaly("MEDIUM")], now=NOW
    )
    assert next(c for c in checks if c["category"] == "performance")["status"] == STATUS_WARN


def test_performance_fails_on_high_severity_anomaly():
    checks = run_health_checks(
        latest_health=None, collector_status=None, session_samples=[], recent_anomalies=[_Anomaly("CRITICAL")], now=NOW
    )
    assert next(c for c in checks if c["category"] == "performance")["status"] == STATUS_FAIL


def test_sql_regression_unavailable_with_no_sql_telemetry():
    checks = run_health_checks(
        latest_health=None, collector_status=None, session_samples=[], recent_anomalies=[], now=NOW
    )
    assert next(c for c in checks if c["category"] == "sql_regression")["status"] == STATUS_UNAVAILABLE


def test_sql_regression_passes_with_telemetry_and_no_open_regressions():
    checks = run_health_checks(
        latest_health=None,
        collector_status=None,
        session_samples=[],
        recent_anomalies=[],
        now=NOW,
        has_sql_telemetry=True,
        open_sql_regressions=[],
    )
    assert next(c for c in checks if c["category"] == "sql_regression")["status"] == STATUS_PASS


def test_sql_regression_warns_on_a_low_severity_open_regression():
    checks = run_health_checks(
        latest_health=None,
        collector_status=None,
        session_samples=[],
        recent_anomalies=[],
        now=NOW,
        has_sql_telemetry=True,
        open_sql_regressions=[_Regression("MEDIUM")],
    )
    assert next(c for c in checks if c["category"] == "sql_regression")["status"] == STATUS_WARN


def test_sql_regression_fails_on_a_high_severity_open_regression():
    checks = run_health_checks(
        latest_health=None,
        collector_status=None,
        session_samples=[],
        recent_anomalies=[],
        now=NOW,
        has_sql_telemetry=True,
        open_sql_regressions=[_Regression("CRITICAL")],
    )
    assert next(c for c in checks if c["category"] == "sql_regression")["status"] == STATUS_FAIL


def test_storage_unavailable_with_no_capacity_telemetry():
    checks = run_health_checks(
        latest_health=None, collector_status=None, session_samples=[], recent_anomalies=[], now=NOW
    )
    assert next(c for c in checks if c["category"] == "storage")["status"] == STATUS_UNAVAILABLE


def test_storage_passes_with_telemetry_and_no_open_alerts():
    checks = run_health_checks(
        latest_health=None,
        collector_status=None,
        session_samples=[],
        recent_anomalies=[],
        now=NOW,
        has_capacity_telemetry=True,
        open_capacity_alerts=[],
    )
    assert next(c for c in checks if c["category"] == "storage")["status"] == STATUS_PASS


def test_storage_warns_on_a_non_high_severity_open_alert():
    # Real capacity alerts are only ever HIGH/CRITICAL (see app/analytics/capacity_alerts.py -
    # only two configurable thresholds exist per kind), so this WARN branch is only reachable
    # via a test double, same as _check_sql_regression's own WARN case is structural, not
    # something a real SQL regression's ratio ladder happens to hit in this specific test.
    checks = run_health_checks(
        latest_health=None,
        collector_status=None,
        session_samples=[],
        recent_anomalies=[],
        now=NOW,
        has_capacity_telemetry=True,
        open_capacity_alerts=[_Regression("MEDIUM")],
    )
    assert next(c for c in checks if c["category"] == "storage")["status"] == STATUS_WARN


def test_storage_fails_on_a_high_severity_open_alert():
    checks = run_health_checks(
        latest_health=None,
        collector_status=None,
        session_samples=[],
        recent_anomalies=[],
        now=NOW,
        has_capacity_telemetry=True,
        open_capacity_alerts=[_Regression("CRITICAL")],
    )
    assert next(c for c in checks if c["category"] == "storage")["status"] == STATUS_FAIL


def test_configuration_unavailable_with_fewer_than_two_rounds():
    checks = run_health_checks(
        latest_health=None, collector_status=None, session_samples=[], recent_anomalies=[], now=NOW
    )
    assert next(c for c in checks if c["category"] == "configuration")["status"] == STATUS_UNAVAILABLE


def test_configuration_passes_with_telemetry_and_no_changes():
    checks = run_health_checks(
        latest_health=None,
        collector_status=None,
        session_samples=[],
        recent_anomalies=[],
        now=NOW,
        has_config_telemetry=True,
        recent_config_changes=[],
    )
    assert next(c for c in checks if c["category"] == "configuration")["status"] == STATUS_PASS


def test_configuration_warns_on_detected_changes():
    checks = run_health_checks(
        latest_health=None,
        collector_status=None,
        session_samples=[],
        recent_anomalies=[],
        now=NOW,
        has_config_telemetry=True,
        recent_config_changes=[ParameterChange("open_cursors", "300", "500", "changed")],
    )
    assert next(c for c in checks if c["category"] == "configuration")["status"] == STATUS_WARN


def test_summarize_health_fail_wins_over_warn_and_pass():
    checks = [
        {"category": "a", "status": STATUS_PASS, "detail": ""},
        {"category": "b", "status": STATUS_WARN, "detail": ""},
        {"category": "c", "status": STATUS_FAIL, "detail": ""},
        {"category": "d", "status": STATUS_UNAVAILABLE, "detail": ""},
    ]
    summary = summarize_health(checks)
    assert summary["overall_status"] == STATUS_FAIL
    assert summary["categories_evaluated"] == 3
    assert summary["categories_unavailable"] == 1
    assert summary["categories_total"] == 4


def test_summarize_health_all_unavailable_is_reported_as_unavailable_not_pass():
    checks = [{"category": "a", "status": STATUS_UNAVAILABLE, "detail": ""}]
    summary = summarize_health(checks)
    assert summary["overall_status"] == STATUS_UNAVAILABLE
