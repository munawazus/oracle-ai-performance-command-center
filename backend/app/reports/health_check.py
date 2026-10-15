"""Deterministic health-check engine (master plan Sec 17). Every category the master plan
names is always present in the result - a category this platform has no collector for yet
is reported as UNAVAILABLE, never silently omitted or reported as passing, per Sec 21's
"Unavailable checks must be explicitly shown as unavailable rather than passed."

Pure function: takes already-fetched rows, does no I/O, so it's directly unit-testable -
same pattern as app/telemetry_views.py and app/analytics/metrics.py.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.analytics.config_metrics import ParameterChange
from app.models.analytics import Anomaly
from app.models.base import ensure_aware
from app.models.capacity_telemetry import CapacityAlertEvent
from app.models.sql_telemetry import SqlRegressionEvent
from app.models.telemetry import CollectorStatus, InstanceHealthSnapshot, SessionSample
from app.telemetry_views import build_blocking_chains, latest_round

# A health-check status label, not a credential - bandit's B105 heuristic string-matches
# on the variable name.
STATUS_PASS = "PASS"  # nosec B105
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"
STATUS_UNAVAILABLE = "UNAVAILABLE"

# How stale a health snapshot can be before a passing reachability check is downgraded to
# WARN. Deliberately a fixed constant rather than a multiple of the configurable collector
# interval, so this check's behavior is simple to reason about and test.
STALE_AFTER = timedelta(minutes=15)

_HIGH_SEVERITIES = {"HIGH", "CRITICAL"}


@dataclass(frozen=True)
class HealthCheckResult:
    category: str
    status: str
    detail: str

    def as_dict(self) -> dict:
        return {"category": self.category, "status": self.status, "detail": self.detail}


def _check_availability(
    latest_health: InstanceHealthSnapshot | None, collector_status: CollectorStatus | None, now: datetime
) -> HealthCheckResult:
    if latest_health is None:
        return HealthCheckResult("availability", STATUS_UNAVAILABLE, "No telemetry has been collected yet.")

    if latest_health.reachability != "up":
        return HealthCheckResult(
            "availability",
            STATUS_FAIL,
            f"Last sample at {latest_health.sampled_at.isoformat()} reported reachability="
            f"{latest_health.reachability}.",
        )

    if collector_status is not None and collector_status.consecutive_failures > 0:
        return HealthCheckResult(
            "availability",
            STATUS_WARN,
            f"Database is reachable but the collector has {collector_status.consecutive_failures} "
            "consecutive failure(s).",
        )

    age = now - ensure_aware(latest_health.sampled_at)
    if age > STALE_AFTER:
        return HealthCheckResult(
            "availability",
            STATUS_WARN,
            f"Last sample is {int(age.total_seconds() // 60)} minute(s) old (stale after "
            f"{int(STALE_AFTER.total_seconds() // 60)}).",
        )

    return HealthCheckResult(
        "availability", STATUS_PASS, f"Reachable and fresh as of {latest_health.sampled_at.isoformat()}."
    )


def _check_locking(session_samples: list[SessionSample]) -> HealthCheckResult:
    if not session_samples:
        return HealthCheckResult("locking", STATUS_UNAVAILABLE, "No session telemetry has been collected yet.")

    chains = build_blocking_chains(latest_round(session_samples))
    if chains:
        return HealthCheckResult(
            "locking", STATUS_WARN, f"{len(chains)} blocking chain(s) present in the latest session sample."
        )
    return HealthCheckResult("locking", STATUS_PASS, "No blocking sessions in the latest sample.")


def _check_performance(recent_anomalies: list[Anomaly]) -> HealthCheckResult:
    if not recent_anomalies:
        return HealthCheckResult("performance", STATUS_PASS, "No anomalies detected in the check window.")
    if any(a.severity in _HIGH_SEVERITIES for a in recent_anomalies):
        high = [a for a in recent_anomalies if a.severity in _HIGH_SEVERITIES]
        return HealthCheckResult(
            "performance", STATUS_FAIL, f"{len(high)} HIGH/CRITICAL anomaly(ies) in the check window."
        )
    return HealthCheckResult(
        "performance", STATUS_WARN, f"{len(recent_anomalies)} anomaly(ies) in the check window."
    )


def _check_sql_regression(
    has_sql_telemetry: bool, open_regressions: list[SqlRegressionEvent]
) -> HealthCheckResult:
    if not has_sql_telemetry:
        return HealthCheckResult(
            "sql_regression", STATUS_UNAVAILABLE, "No SQL statistics have been collected yet."
        )
    if any(e.severity in _HIGH_SEVERITIES for e in open_regressions):
        high = [e for e in open_regressions if e.severity in _HIGH_SEVERITIES]
        return HealthCheckResult(
            "sql_regression", STATUS_FAIL, f"{len(high)} open HIGH/CRITICAL SQL regression(s)."
        )
    if open_regressions:
        return HealthCheckResult(
            "sql_regression", STATUS_WARN, f"{len(open_regressions)} open SQL regression(s)."
        )
    return HealthCheckResult("sql_regression", STATUS_PASS, "No open SQL regressions.")


def _check_storage(
    has_capacity_telemetry: bool, open_capacity_alerts: list[CapacityAlertEvent]
) -> HealthCheckResult:
    if not has_capacity_telemetry:
        return HealthCheckResult("storage", STATUS_UNAVAILABLE, "No capacity telemetry has been collected yet.")
    if any(e.severity in _HIGH_SEVERITIES for e in open_capacity_alerts):
        high = [e for e in open_capacity_alerts if e.severity in _HIGH_SEVERITIES]
        return HealthCheckResult("storage", STATUS_FAIL, f"{len(high)} open HIGH/CRITICAL capacity alert(s).")
    if open_capacity_alerts:
        return HealthCheckResult("storage", STATUS_WARN, f"{len(open_capacity_alerts)} open capacity alert(s).")
    return HealthCheckResult("storage", STATUS_PASS, "No open capacity alerts.")


def _check_configuration(
    has_config_telemetry: bool, recent_config_changes: list[ParameterChange]
) -> HealthCheckResult:
    """No FAIL tier - a configuration change isn't inherently a failure severity, same
    PASS/WARN-only shape as _check_locking above."""
    if not has_config_telemetry:
        return HealthCheckResult(
            "configuration", STATUS_UNAVAILABLE, "Fewer than two configuration collection rounds exist yet."
        )
    if recent_config_changes:
        return HealthCheckResult(
            "configuration",
            STATUS_WARN,
            f"{len(recent_config_changes)} configuration parameter change(s) since the previous round.",
        )
    return HealthCheckResult("configuration", STATUS_PASS, "No configuration changes since the previous round.")


_UNAVAILABLE_CATEGORIES = {
    "resource_limits": "V$RESOURCE_LIMIT is not collected by this platform yet.",
    "operational": "No job/alert-log event collection is implemented yet.",
    "backup": "Backup status collection is not implemented.",
    "data_guard": "Data Guard telemetry is not collected.",
    "asm": "ASM diskgroup telemetry is not collected.",
}

# Registration order defines report/UI display order and matches master plan Sec 17's table.
CATEGORY_ORDER = [
    "availability",
    "resource_limits",
    "storage",
    "performance",
    "sql_regression",
    "locking",
    "operational",
    "backup",
    "data_guard",
    "asm",
    "configuration",
]


def run_health_checks(
    *,
    latest_health: InstanceHealthSnapshot | None,
    collector_status: CollectorStatus | None,
    session_samples: list[SessionSample],
    recent_anomalies: list[Anomaly],
    now: datetime,
    has_sql_telemetry: bool = False,
    open_sql_regressions: list[SqlRegressionEvent] | None = None,
    has_capacity_telemetry: bool = False,
    open_capacity_alerts: list[CapacityAlertEvent] | None = None,
    has_config_telemetry: bool = False,
    recent_config_changes: list[ParameterChange] | None = None,
) -> list[dict]:
    results = {
        "availability": _check_availability(latest_health, collector_status, now),
        "locking": _check_locking(session_samples),
        "performance": _check_performance(recent_anomalies),
        "sql_regression": _check_sql_regression(has_sql_telemetry, open_sql_regressions or []),
        "storage": _check_storage(has_capacity_telemetry, open_capacity_alerts or []),
        "configuration": _check_configuration(has_config_telemetry, recent_config_changes or []),
    }
    for category, reason in _UNAVAILABLE_CATEGORIES.items():
        results[category] = HealthCheckResult(category, STATUS_UNAVAILABLE, reason)

    return [results[category].as_dict() for category in CATEGORY_ORDER]


def summarize_health(check_results: list[dict]) -> dict:
    """Deterministic rollup - never itself an AI judgment. A FAIL anywhere makes the whole
    check FAIL; otherwise a WARN anywhere makes it WARN; otherwise PASS, regardless of how
    many categories are UNAVAILABLE (those are excluded from the pass/fail computation but
    reported in `unavailable_count` so a reader knows how much of the picture is missing)."""
    statuses = [r["status"] for r in check_results]
    unavailable_count = statuses.count(STATUS_UNAVAILABLE)
    evaluated = [s for s in statuses if s != STATUS_UNAVAILABLE]

    if STATUS_FAIL in evaluated:
        overall = STATUS_FAIL
    elif STATUS_WARN in evaluated:
        overall = STATUS_WARN
    elif evaluated:
        overall = STATUS_PASS
    else:
        overall = STATUS_UNAVAILABLE

    return {
        "overall_status": overall,
        "categories_evaluated": len(evaluated),
        "categories_unavailable": unavailable_count,
        "categories_total": len(check_results),
    }
