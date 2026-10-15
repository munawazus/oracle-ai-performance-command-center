"""Activity tab, Phase 2: the unified, fleet-wide event timeline. Four event sources -
anomalies, SQL regressions, capacity alerts, and incident lifecycle events - currently live in
four separate repositories with no merged view anywhere in this platform. This module is a
pure normalize-and-sort function (no I/O - same directly-unit-testable convention as
app/reports/health_check.py and app/analytics/*.py): callers fetch the raw rows, this module
turns them into one typed, time-ordered list.

Deliberately does NOT change how SQL regressions/capacity alerts relate to Incidents - both are
explicitly NOT wired into incident creation elsewhere in this platform (a prior, deliberate
design decision), and their appearing here as timeline entries is Activity surfacing existing
state, not a new escalation path.

Phase 3 adds storm/burst collapsing (see _collapse_anomaly_storms) - still no I/O, still
stateless, no new table.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.models.analytics import Anomaly
from app.models.capacity_telemetry import CapacityAlertEvent
from app.models.incident import Incident
from app.models.sql_telemetry import SqlRegressionEvent
from app.services.incident_service import SEVERITY_RANK

ANOMALY = "anomaly"
SQL_REGRESSION = "sql_regression"
CAPACITY_ALERT = "capacity_alert"
INCIDENT_OPENED = "incident_opened"
INCIDENT_RESOLVED = "incident_resolved"
STORM = "storm"

# Activity tab Phase 3's v1 storm definition, deliberately narrow (written down before coding,
# per this feature's own design review): >=3 distinct databases with an anomaly on the SAME
# metric_key, all within STORM_WINDOW of the EARLIEST anomaly in the candidate cluster - a
# fixed anchor, not a true pairwise-sliding window, so the rule stays simple and deterministic
# to test. Anomalies only for v1 - a SQL regression's sql_id or a capacity alert's
# tablespace_name rarely coincides across independent databases the way a metric_key does, so
# neither has an equally natural cross-database grouping key yet.
STORM_WINDOW = timedelta(minutes=10)
STORM_MIN_DATABASES = 3


@dataclass(frozen=True)
class ActivityEvent:
    """Mirrors app/ai/investigation.py's Finding shape deliberately (category, severity,
    summary, entities) - the same "one normalized fact" convention, applied here across
    databases instead of within one."""

    event_id: str
    category: str
    severity: str
    occurred_at: datetime
    database_id: str
    database_name: str
    summary: str
    entities: dict = field(default_factory=dict)


def _anomaly_to_event(a: Anomaly, database_name: str) -> ActivityEvent:
    return ActivityEvent(
        event_id=f"anomaly:{a.id}",
        category=ANOMALY,
        severity=a.severity,
        occurred_at=a.detected_at,
        database_id=a.monitored_database_id,
        database_name=database_name,
        summary=f"Anomaly detected on {a.metric_key} (observed {a.observed_value:.1f}, baseline {a.baseline_mean:.1f}).",
        entities={"metric_key": a.metric_key, "observed_value": a.observed_value, "incident_id": a.incident_id},
    )


def _sql_regression_to_event(e: SqlRegressionEvent, database_name: str) -> ActivityEvent:
    return ActivityEvent(
        event_id=f"sql_regression:{e.id}",
        category=SQL_REGRESSION,
        severity=e.severity,
        occurred_at=e.last_detected_at,
        database_id=e.monitored_database_id,
        database_name=database_name,
        summary=e.detection_note,
        entities={"sql_id": e.sql_id, "regression_kind": e.regression_kind, "ratio": e.ratio, "status": e.status},
    )


def _capacity_alert_to_event(e: CapacityAlertEvent, database_name: str) -> ActivityEvent:
    return ActivityEvent(
        event_id=f"capacity_alert:{e.id}",
        category=CAPACITY_ALERT,
        severity=e.severity,
        occurred_at=e.last_detected_at,
        database_id=e.monitored_database_id,
        database_name=database_name,
        summary=e.detection_note,
        entities={
            "tablespace_name": e.tablespace_name,
            "alert_kind": e.alert_kind,
            "pct_used": e.pct_used,
            "status": e.status,
        },
    )


def _incident_to_events(i: Incident, database_name: str) -> list[ActivityEvent]:
    events = [
        ActivityEvent(
            event_id=f"incident_opened:{i.id}",
            category=INCIDENT_OPENED,
            severity=i.severity,
            occurred_at=i.opened_at,
            database_id=i.monitored_database_id,
            database_name=database_name,
            summary=f"Incident opened: {i.title}",
            entities={"incident_id": i.id, "status": i.status},
        )
    ]
    if i.resolved_at is not None:
        events.append(
            ActivityEvent(
                event_id=f"incident_resolved:{i.id}",
                category=INCIDENT_RESOLVED,
                severity="INFO",
                occurred_at=i.resolved_at,
                database_id=i.monitored_database_id,
                database_name=database_name,
                summary=f"Incident resolved: {i.title}",
                entities={"incident_id": i.id, "status": i.status},
            )
        )
    return events


def _storm_event(metric_key: str, cluster: list[ActivityEvent]) -> ActivityEvent:
    earliest = min(e.occurred_at for e in cluster)
    worst_severity = max((e.severity for e in cluster), key=lambda s: SEVERITY_RANK.get(s, 0))
    database_ids = sorted({e.database_id for e in cluster})
    database_names = sorted({e.database_name for e in cluster})
    return ActivityEvent(
        event_id=f"storm:{metric_key}:{earliest.isoformat()}",
        category=STORM,
        severity=worst_severity,
        occurred_at=earliest,
        database_id=database_ids[0],
        database_name=f"{len(database_ids)} databases",
        summary=(
            f"{len(database_ids)} databases ({', '.join(database_names)}) showing an anomaly "
            f"on {metric_key} within {int(STORM_WINDOW.total_seconds() // 60)} minutes of each other."
        ),
        entities={
            "metric_key": metric_key,
            "database_ids": database_ids,
            "database_names": database_names,
            "constituent_event_ids": sorted(e.event_id for e in cluster),
        },
    )


def _collapse_anomaly_storms(events: list[ActivityEvent]) -> list[ActivityEvent]:
    """See STORM_WINDOW/STORM_MIN_DATABASES above for the v1 definition. Stateless - built
    fresh from whatever anomaly events are already in `events` (already since/until-filtered
    by the caller), never persisted, never dependent on a prior run's result."""
    anomalies = [e for e in events if e.category == ANOMALY]
    if not anomalies:
        return events
    others = [e for e in events if e.category != ANOMALY]

    by_metric: dict[str, list[ActivityEvent]] = {}
    for e in anomalies:
        by_metric.setdefault(str(e.entities.get("metric_key", "")), []).append(e)

    collapsed: list[ActivityEvent] = list(others)
    for metric_key, group in by_metric.items():
        group.sort(key=lambda e: e.occurred_at)
        i, n = 0, len(group)
        while i < n:
            window_start = group[i].occurred_at
            j = i + 1
            while j < n and group[j].occurred_at - window_start <= STORM_WINDOW:
                j += 1
            cluster = group[i:j]
            if len({e.database_id for e in cluster}) >= STORM_MIN_DATABASES:
                collapsed.append(_storm_event(metric_key, cluster))
            else:
                collapsed.extend(cluster)
            i = j
    return collapsed


def build_activity_feed(
    *,
    anomalies: list[Anomaly],
    sql_regressions: list[SqlRegressionEvent],
    capacity_alerts: list[CapacityAlertEvent],
    incidents: list[Incident],
    database_names: dict[str, str],
    since: datetime | None = None,
    until: datetime | None = None,
    severity: str | None = None,
    limit: int = 200,
) -> list[ActivityEvent]:
    """Normalizes four independently-fetched event sources into one time-sorted (newest
    first) list. `since`/`until` are applied before storm-collapsing (a storm is only ever
    built from anomalies already inside the caller's requested window); `severity` is applied
    after (a storm's own computed severity - the worst of its constituents - governs its own
    filtering, same as any other event)."""
    events: list[ActivityEvent] = []
    for a in anomalies:
        events.append(_anomaly_to_event(a, database_names.get(a.monitored_database_id, "Unknown")))
    for e in sql_regressions:
        events.append(_sql_regression_to_event(e, database_names.get(e.monitored_database_id, "Unknown")))
    for e in capacity_alerts:
        events.append(_capacity_alert_to_event(e, database_names.get(e.monitored_database_id, "Unknown")))
    for i in incidents:
        events.extend(_incident_to_events(i, database_names.get(i.monitored_database_id, "Unknown")))

    if since is not None:
        events = [e for e in events if e.occurred_at >= since]
    if until is not None:
        events = [e for e in events if e.occurred_at <= until]

    events = _collapse_anomaly_storms(events)

    if severity is not None:
        events = [e for e in events if e.severity == severity]

    events.sort(key=lambda e: e.occurred_at, reverse=True)
    return events[:limit]
