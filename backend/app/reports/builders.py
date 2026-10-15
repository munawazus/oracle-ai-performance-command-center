"""One builder function per report type (master plan Sec 21/26). Each returns
{data_available, data_completeness_note, limitations, content} - report_service.py wraps
that in the persisted Report envelope (id, requester, timestamps, etc).

Every builder is honest about what this platform has never collected (host metrics,
backup/Data Guard/ASM, change intelligence) - it returns a clear "not available" body instead
of fabricating content, exactly like the AI tool layer (app/ai/tools.py) already does for the
same gaps. SQL regression (Enterprise Readiness Plan D.1) and capacity (Enterprise Readiness
Plan D.2) are no longer in that list - see build_sql_regression_report/build_capacity_report.
"""

from datetime import datetime, timedelta
from typing import Sequence, cast

from app.analytics.capacity_metrics import GROWTH_TREND_WINDOW, TablespaceSampleLike, compute_growth_trend, describe_usage
from app.analytics.config_metrics import compute_parameter_changes
from app.analytics.metrics import WaitRow, aggregate_by_wait_class, compute_wait_class_rates
from app.analytics.sql_metrics import SqlStatRowLike, compute_sql_deltas, rank_sql_deltas
from app.analytics.statspack_metrics import adapt_sql_samples
from app.models.base import ensure_aware, utcnow
from app.models.database import MonitoredDatabase
from app.models.incident import Incident
from app.reports.health_check import run_health_checks, summarize_health
from app.repositories.analytics_repo import AnomalyRepository
from app.repositories.capacity_repo import CapacityRepository
from app.repositories.config_repo import ConfigTelemetryRepository
from app.repositories.database_repo import CapabilityRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.notification_repo import NotificationEventRepository
from app.repositories.sql_repo import SqlTelemetryRepository
from app.repositories.statspack_repo import StatspackRepository
from app.repositories.telemetry_repo import CollectorStatusRepository, TelemetryRepository
from app.telemetry_views import build_blocking_chains, latest_round


def _severity_summary(items: list) -> dict[str, int]:
    counts = {"INFO": 0, "LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
    for item in items:
        if item.severity in counts:
            counts[item.severity] += 1
    return counts


async def _wait_summary(telemetry_repo: TelemetryRepository, database_id: str) -> dict:
    rounds = await telemetry_repo.latest_two_wait_rounds(database_id)
    if rounds is None:
        return {"available": False, "reason": "fewer than two collection rounds - no rate can be computed yet"}
    (t1, rows1), (t2, rows2) = rounds
    elapsed_seconds = (t2 - t1).total_seconds()
    rates = compute_wait_class_rates(aggregate_by_wait_class(rows1), aggregate_by_wait_class(rows2), elapsed_seconds)
    return {
        "available": True,
        "sampled_at": t2.isoformat(),
        "elapsed_seconds": elapsed_seconds,
        "wait_classes": [
            {"wait_class": wc, "centisec_waited_per_sec": round(rate, 2)}
            for wc, rate in sorted(rates.items(), key=lambda kv: kv[1], reverse=True)
        ],
    }


async def _top_sql_summary(sql_repo: SqlTelemetryRepository, database_id: str, *, limit: int = 10) -> dict:
    rounds = await sql_repo.latest_two_sql_rounds(database_id)
    if rounds is None:
        return {"available": False, "reason": "fewer than two SQL collection rounds - no delta can be computed yet"}
    (t1, rows1), (t2, rows2) = rounds
    elapsed_seconds = (t2 - t1).total_seconds()
    deltas = rank_sql_deltas(compute_sql_deltas(rows1, rows2, elapsed_seconds), sort_by="elapsed_time", limit=limit)
    statements = await sql_repo.statements_for_sql_ids(database_id, [d.sql_id for d in deltas])
    return {
        "available": True,
        "sampled_at": t2.isoformat(),
        "elapsed_seconds": elapsed_seconds,
        "statements": [
            {
                "sql_id": d.sql_id,
                "plan_hash_value": d.plan_hash_value,
                "sql_text": statements[d.sql_id].sql_text if d.sql_id in statements else None,
                "executions_delta": d.executions,
                "elapsed_time_us_delta": d.elapsed_time_us,
                "elapsed_per_exec_us": d.elapsed_per_exec_us,
            }
            for d in deltas
        ],
    }


async def _sql_regressions_summary(sql_repo: SqlTelemetryRepository, database_id: str) -> dict:
    events = await sql_repo.list_regressions(database_id, status="OPEN", limit=50)
    return {
        "available": True,
        "open_regression_count": len(events),
        "regressions": [
            {
                "sql_id": e.sql_id,
                "regression_kind": e.regression_kind,
                "severity": e.severity,
                "first_detected_at": e.first_detected_at.isoformat(),
                "last_detected_at": e.last_detected_at.isoformat(),
                "ratio": e.ratio,
                "detection_note": e.detection_note,
            }
            for e in events
        ],
    }


async def _capacity_summary(capacity_repo: CapacityRepository, database_id: str, *, limit: int = 25) -> dict:
    latest = await capacity_repo.latest_round(database_id)
    if latest is None:
        return {"available": False, "reason": "no capacity telemetry collected yet"}

    sampled_at, current_rows = latest
    now = utcnow()

    tablespaces = []
    for row in current_rows[:limit]:
        # cast, not a real type mismatch: TablespaceSample satisfies TablespaceSampleLike's
        # Protocol at runtime - same documented mypy+SQLAlchemy quirk as
        # app/services/capacity_alert_service.py.
        usage = describe_usage(cast(TablespaceSampleLike, row))
        history = await capacity_repo.samples_in_window(
            database_id, row.tablespace_name, now - GROWTH_TREND_WINDOW, now
        )
        trend = compute_growth_trend(
            cast(Sequence[TablespaceSampleLike], history),
            now,
            current_used_bytes=row.used_bytes,
            max_bytes=row.max_bytes,
        )
        tablespaces.append(
            {
                "tablespace_name": row.tablespace_name,
                "contents": row.contents,
                "pct_used": round(usage.pct_used, 2),
                "allocated_bytes": row.allocated_bytes,
                "used_bytes": row.used_bytes,
                "max_bytes": row.max_bytes,
                "growth_bytes_per_day": trend.bytes_per_day,
                "projected_days_to_full": trend.projected_days_to_full,
                "projection_reason": trend.projection_reason,
            }
        )

    return {"available": True, "sampled_at": sampled_at.isoformat(), "tablespaces": tablespaces}


def _serialize_capacity_alert(e) -> dict:
    return {
        "tablespace_name": e.tablespace_name,
        "alert_kind": e.alert_kind,
        "severity": e.severity,
        "status": e.status,
        "first_detected_at": e.first_detected_at.isoformat(),
        "last_detected_at": e.last_detected_at.isoformat(),
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
        "pct_used": e.pct_used,
        "projected_days_to_full": e.projected_days_to_full,
        "detection_note": e.detection_note,
    }


async def _statspack_summary(
    capability_repo: CapabilityRepository,
    statspack_repo: StatspackRepository,
    sql_repo: SqlTelemetryRepository,
    database_id: str,
    *,
    limit: int = 20,
) -> dict:
    """Top wait classes and top SQL by delta between the two most recently synced Statspack
    snapshots - mirrors app/ai/tools.py's get_statspack_summary exactly (same envelope, same
    honest reasons), reused here as an additive Daily DBA report section, same shape as
    top_sql_summary/capacity_summary above."""
    capability = await capability_repo.get_by_database_id(database_id)
    if not capability or not capability.statspack_available:
        return {"available": False, "reason": "Statspack is not installed on this database"}

    rounds = await statspack_repo.latest_two_synced_snapshots(database_id)
    if rounds is None:
        if await statspack_repo.has_any_snapshots(database_id):
            reason = "only one Statspack snapshot has been synced so far - a delta needs at least two"
        else:
            reason = "Statspack is installed, but no snapshots have been synced yet"
        return {"available": False, "reason": reason}

    from_snap, to_snap = rounds
    elapsed_seconds = (to_snap.snap_time - from_snap.snap_time).total_seconds()

    previous_waits = await statspack_repo.wait_event_samples_for_snapshot(database_id, from_snap.snap_id)
    current_waits = await statspack_repo.wait_event_samples_for_snapshot(database_id, to_snap.snap_id)
    # cast, not a real type mismatch: StatspackWaitEventSample satisfies WaitRow's Protocol at
    # runtime - same documented mypy+SQLAlchemy quirk as app/services/capacity_alert_service.py.
    wait_rates = compute_wait_class_rates(
        aggregate_by_wait_class(cast(list[WaitRow], previous_waits)),
        aggregate_by_wait_class(cast(list[WaitRow], current_waits)),
        elapsed_seconds,
    )

    previous_sql = await statspack_repo.sql_samples_for_snapshot(database_id, from_snap.snap_id)
    current_sql = await statspack_repo.sql_samples_for_snapshot(database_id, to_snap.snap_id)
    sql_deltas = rank_sql_deltas(
        compute_sql_deltas(
            cast(Sequence[SqlStatRowLike], adapt_sql_samples(previous_sql)),
            cast(Sequence[SqlStatRowLike], adapt_sql_samples(current_sql)),
            elapsed_seconds,
        ),
        sort_by="elapsed_time",
        limit=limit,
    )
    statements = await sql_repo.statements_for_sql_ids(database_id, [d.sql_id for d in sql_deltas])

    return {
        "available": True,
        "from_snap_id": from_snap.snap_id,
        "to_snap_id": to_snap.snap_id,
        "elapsed_hours": round(elapsed_seconds / 3600.0, 2) if elapsed_seconds > 0 else 0.0,
        "wait_classes": [
            {"wait_class": wc, "centisec_waited_per_hour": round(rate * 3600, 2)}
            for wc, rate in sorted(wait_rates.items(), key=lambda kv: kv[1], reverse=True)
        ],
        "top_sql": [
            {
                "sql_id": d.sql_id,
                "sql_text": statements[d.sql_id].sql_text if d.sql_id in statements else None,
                "executions_delta": d.executions,
                "elapsed_time_us_delta": d.elapsed_time_us,
                "elapsed_per_exec_us": d.elapsed_per_exec_us,
            }
            for d in sql_deltas
        ],
    }


async def _configuration_summary(config_repo: ConfigTelemetryRepository, database_id: str) -> dict:
    """Configuration parameter drift between the two most recently collected rounds - mirrors
    app/ai/tools.py's get_change_history exactly (same envelope, same honest reasons), reused
    here as an additive Daily DBA report section, same shape as capacity_summary/
    statspack_summary above."""
    rounds = await config_repo.latest_two_rounds(database_id)
    if rounds is None:
        if await config_repo.has_any_samples(database_id):
            reason = "only one configuration collection round exists so far - a diff needs at least two"
        else:
            reason = "no configuration telemetry has been collected yet"
        return {"available": False, "reason": reason}

    (from_sampled_at, previous_rows), (to_sampled_at, current_rows) = rounds
    changes = compute_parameter_changes(previous_rows, current_rows)

    return {
        "available": True,
        "from_sampled_at": from_sampled_at.isoformat(),
        "to_sampled_at": to_sampled_at.isoformat(),
        "changes": [
            {
                "parameter_name": c.parameter_name,
                "previous_value": c.previous_value,
                "current_value": c.current_value,
                "change_type": c.change_type,
            }
            for c in changes
        ],
    }


async def _blocking_summary(telemetry_repo: TelemetryRepository, database_id: str) -> dict:
    samples = await telemetry_repo.recent_session_samples(database_id, limit=500)
    if not samples:
        return {"available": False, "reason": "no session telemetry collected yet", "blocking_chains": []}
    current_round = latest_round(samples)
    return {
        "available": True,
        "sampled_at": current_round[0].sampled_at.isoformat(),
        "blocking_chains": build_blocking_chains(current_round),
    }


async def _health_summary(
    telemetry_repo: TelemetryRepository,
    collector_status_repo: CollectorStatusRepository,
    anomaly_repo: AnomalyRepository,
    sql_repo: SqlTelemetryRepository,
    capacity_repo: CapacityRepository,
    config_repo: ConfigTelemetryRepository,
    database_id: str,
    now: datetime,
) -> dict:
    latest_health = await telemetry_repo.latest_instance_health(database_id)
    collector_status = await collector_status_repo.get_by_database_id(database_id)
    session_samples = await telemetry_repo.recent_session_samples(database_id, limit=500)
    recent_anomalies = await anomaly_repo.list_recent(database_id, limit=200, since=now - timedelta(hours=24))
    has_sql_telemetry = await sql_repo.has_any_stat_samples(database_id)
    open_sql_regressions = await sql_repo.list_regressions(database_id, status="OPEN", limit=200)
    has_capacity_telemetry = await capacity_repo.has_any_samples(database_id)
    open_capacity_alerts = await capacity_repo.list_alerts(database_id, status="OPEN", limit=200)
    config_rounds = await config_repo.latest_two_rounds(database_id)
    has_config_telemetry = config_rounds is not None
    recent_config_changes = (
        compute_parameter_changes(config_rounds[0][1], config_rounds[1][1]) if config_rounds else []
    )

    checks = run_health_checks(
        latest_health=latest_health,
        collector_status=collector_status,
        session_samples=session_samples,
        recent_anomalies=recent_anomalies,
        now=now,
        has_sql_telemetry=has_sql_telemetry,
        open_sql_regressions=open_sql_regressions,
        has_capacity_telemetry=has_capacity_telemetry,
        open_capacity_alerts=open_capacity_alerts,
        has_config_telemetry=has_config_telemetry,
        recent_config_changes=recent_config_changes,
    )
    return {"checks": checks, "summary": summarize_health(checks)}


def _recommended_investigation_items(
    open_incidents: list[Incident], blocking: dict, health_checks: list[dict]
) -> list[str]:
    """Deterministic, evidence-derived bullet points - never an LLM guess (master plan Sec
    12's "Anomaly/severity classification must be deterministic and testable" applies
    equally here: recommendations are diagnostic, read-only, and reproducible)."""
    items = []
    high_or_critical = [i for i in open_incidents if i.severity in ("HIGH", "CRITICAL")]
    if high_or_critical:
        items.append(
            f"{len(high_or_critical)} open HIGH/CRITICAL incident(s) need investigation: "
            + ", ".join(i.title for i in high_or_critical[:5])
        )
    if blocking.get("available") and blocking.get("blocking_chains"):
        items.append(f"{len(blocking['blocking_chains'])} active blocking chain(s) - review Blocking & Locks.")
    failing = [c for c in health_checks if c["status"] == "FAIL"]
    for c in failing:
        items.append(f"Health check '{c['category']}' is FAILING: {c['detail']}")
    if not items:
        items.append("No investigation items identified from currently available telemetry.")
    return items


async def build_daily_dba_report(
    *,
    database: MonitoredDatabase,
    period_start: datetime,
    period_end: datetime,
    now: datetime,
    telemetry_repo: TelemetryRepository,
    collector_status_repo: CollectorStatusRepository,
    anomaly_repo: AnomalyRepository,
    incident_repo: IncidentRepository,
    sql_repo: SqlTelemetryRepository,
    capacity_repo: CapacityRepository,
    capability_repo: CapabilityRepository,
    statspack_repo: StatspackRepository,
    config_repo: ConfigTelemetryRepository,
) -> dict:
    latest_health = await telemetry_repo.latest_instance_health(database.id)
    health_points = await telemetry_repo.recent_instance_health(
        database.id, limit=2000, since=period_start, until=period_end
    )
    collector_status = await collector_status_repo.get_by_database_id(database.id)

    all_incidents = await incident_repo.list_for_database(database.id, limit=500)
    incidents_in_period = [i for i in all_incidents if period_start <= ensure_aware(i.opened_at) <= period_end]
    open_incidents = [i for i in all_incidents if i.status != "RESOLVED"]

    anomalies = await anomaly_repo.list_recent(database.id, limit=500, since=period_start, until=period_end)

    session_samples = await telemetry_repo.recent_session_samples(
        database.id, limit=2000, since=period_start, until=period_end
    )
    distinct_usernames = sorted({s.username for s in session_samples if s.username})
    distinct_machines = sorted({s.machine for s in session_samples if s.machine})
    distinct_programs = sorted({s.program for s in session_samples if s.program})

    active_session_counts = [p.active_session_count for p in health_points if p.active_session_count is not None]
    reachable_count = sum(1 for p in health_points if p.reachability == "up")

    wait_summary = await _wait_summary(telemetry_repo, database.id)
    blocking_summary = await _blocking_summary(telemetry_repo, database.id)
    health = await _health_summary(
        telemetry_repo, collector_status_repo, anomaly_repo, sql_repo, capacity_repo, config_repo, database.id, now
    )
    top_sql_summary = await _top_sql_summary(sql_repo, database.id)
    sql_regressions_summary = await _sql_regressions_summary(sql_repo, database.id)
    capacity_summary = await _capacity_summary(capacity_repo, database.id)
    statspack_summary = await _statspack_summary(capability_repo, statspack_repo, sql_repo, database.id)
    configuration_summary = await _configuration_summary(config_repo, database.id)

    limitations = [
        "Wait summary reflects the two most recent collection rounds at generation time, "
        "not an aggregate over the full reporting period.",
        "Top SQL summary reflects the two most recent SQL collection rounds at generation time, "
        "not an aggregate over the full reporting period.",
    ]
    if not capacity_summary.get("available"):
        limitations.append("Capacity summary: " + capacity_summary.get("reason", "unavailable."))
    # Only flagged as a limitation when Statspack IS installed but temporarily unavailable
    # (e.g. not yet synced) - Statspack simply not being installed is an expected, permanent
    # fact about most databases, not a gap in this particular report worth calling out.
    if not statspack_summary.get("available") and statspack_summary.get("reason") != (
        "Statspack is not installed on this database"
    ):
        limitations.append("Statspack summary: " + statspack_summary.get("reason", "unavailable."))
    if not configuration_summary.get("available"):
        limitations.append("Configuration summary: " + configuration_summary.get("reason", "unavailable."))
    if not health_points:
        limitations.append("No instance health telemetry was collected during the reporting period.")

    content = {
        "database": {"id": database.id, "name": database.name, "edition_hint": database.edition_hint},
        "availability": {
            "latest_reachability": latest_health.reachability if latest_health else None,
            "latest_sampled_at": latest_health.sampled_at.isoformat() if latest_health else None,
            "samples_in_period": len(health_points),
            "reachable_samples_in_period": reachable_count,
            "collector_consecutive_failures": collector_status.consecutive_failures if collector_status else None,
        },
        "active_incidents": {
            "open_count": len(open_incidents),
            "severity_summary": _severity_summary(open_incidents),
            "items": [
                {"id": i.id, "title": i.title, "severity": i.severity, "status": i.status, "opened_at": i.opened_at.isoformat()}
                for i in open_incidents[:20]
            ],
        },
        "incidents_opened_in_period": len(incidents_in_period),
        "important_anomalies": {
            "count_in_period": len(anomalies),
            "severity_summary": _severity_summary(anomalies),
            "top": [
                {
                    "id": a.id,
                    "metric_key": a.metric_key,
                    "severity": a.severity,
                    "detected_at": a.detected_at.isoformat(),
                    "observed_value": a.observed_value,
                    "baseline_mean": a.baseline_mean,
                }
                for a in anomalies[:20]
            ],
        },
        "performance_summary": {
            "active_session_count_avg": round(sum(active_session_counts) / len(active_session_counts), 2)
            if active_session_counts
            else None,
            "active_session_count_max": max(active_session_counts) if active_session_counts else None,
        },
        "workload_summary": {
            "distinct_usernames": distinct_usernames,
            "distinct_machines": distinct_machines,
            "distinct_programs": distinct_programs,
            "session_samples_in_period": len(session_samples),
        },
        "top_sql_summary": top_sql_summary,
        "sql_regression_summary": sql_regressions_summary,
        "wait_summary": wait_summary,
        "blocking_summary": blocking_summary,
        "capacity_summary": capacity_summary,
        "statspack_summary": statspack_summary,
        "configuration_summary": configuration_summary,
        "health_check_results": health,
        "recommended_investigation_items": _recommended_investigation_items(
            open_incidents, blocking_summary, health["checks"]
        ),
    }

    return {
        "data_available": True,
        "data_completeness_note": (
            f"Built from {len(health_points)} health sample(s), {len(session_samples)} session "
            f"sample(s), and {len(anomalies)} anomaly(ies) recorded in the reporting period."
        ),
        "limitations": limitations,
        "content": content,
    }


async def _affected_sql_workload(
    telemetry_repo: TelemetryRepository,
    sql_repo: SqlTelemetryRepository,
    incident: Incident,
    now: datetime,
    *,
    limit: int = 20,
) -> dict:
    """SQL_IDs seen in session samples during the incident's own time window - a temporal
    correlation (this SQL ran while the incident was open), not a causal claim (we don't
    know it caused or was affected by whatever triggered the incident). Reuses the existing
    session_samples.sql_id column - no new collection needed for this join."""
    window_start = ensure_aware(incident.opened_at)
    window_end = ensure_aware(incident.resolved_at) if incident.resolved_at else now

    samples = await telemetry_repo.recent_session_samples(
        incident.monitored_database_id, limit=2000, since=window_start, until=window_end
    )
    counts: dict[str, int] = {}
    for s in samples:
        if s.sql_id:
            counts[s.sql_id] = counts.get(s.sql_id, 0) + 1
    if not counts:
        return {
            "available": False,
            "reason": "no session sample during the incident window recorded a sql_id",
        }

    top_sql_ids = sorted(counts, key=lambda sid: counts[sid], reverse=True)[:limit]
    statements = await sql_repo.statements_for_sql_ids(incident.monitored_database_id, top_sql_ids)
    return {
        "available": True,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "distinct_sql_id_count": len(counts),
        "statements": [
            {
                "sql_id": sql_id,
                "sql_text": statements[sql_id].sql_text if sql_id in statements else None,
                "session_samples_in_window": counts[sql_id],
            }
            for sql_id in top_sql_ids
        ],
    }


async def build_incident_report(
    *,
    incident: Incident,
    database: MonitoredDatabase | None,
    anomaly_repo: AnomalyRepository,
    incident_repo: IncidentRepository,
    notification_repo: NotificationEventRepository,
    telemetry_repo: TelemetryRepository,
    sql_repo: SqlTelemetryRepository,
    now: datetime,
    rca_result: dict | None,
) -> dict:
    events = await incident_repo.list_events(incident.id)
    evidence = await anomaly_repo.list_for_incident(incident.id)
    notifications = await notification_repo.list_for_incident(incident.id)
    affected_sql_workload = await _affected_sql_workload(telemetry_repo, sql_repo, incident, now)

    duration_seconds = None
    if incident.resolved_at:
        duration_seconds = (incident.resolved_at - incident.opened_at).total_seconds()

    limitations = [
        "Affected SQL/session/workload is correlated temporally (SQL seen in session samples "
        "during the incident's own time window), not shown to be causal."
    ]
    if rca_result is None:
        limitations.append(
            "RCA was not requested for this report (pass include_rca=true to invoke the AI gateway), "
            "or AI is disabled on this deployment (AI_ENABLED=false)."
        )

    content = {
        "incident": {
            "id": incident.id,
            "database_id": incident.monitored_database_id,
            "database_name": database.name if database else None,
            "severity": incident.severity,
            "status": incident.status,
            "title": incident.title,
            "opened_at": incident.opened_at.isoformat(),
            "last_evidence_at": incident.last_evidence_at.isoformat(),
            "acknowledged_at": incident.acknowledged_at.isoformat() if incident.acknowledged_at else None,
            "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
            "resolution_note": incident.resolution_note,
        },
        "impact": {
            "duration_seconds": duration_seconds,
            "still_open": incident.status != "RESOLVED",
        },
        "evidence": [
            {
                "anomaly_id": a.id,
                "metric_key": a.metric_key,
                "severity": a.severity,
                "detected_at": a.detected_at.isoformat(),
                "observed_value": a.observed_value,
                "baseline_mean": a.baseline_mean,
                "baseline_stddev": a.baseline_stddev,
            }
            for a in evidence
        ],
        "timeline": [
            {
                "id": e.id,
                "occurred_at": e.occurred_at.isoformat(),
                "event_type": e.event_type,
                "actor_user_id": e.actor_user_id,
                "detail": e.detail,
            }
            for e in events
        ],
        "correlated_anomaly_count": len(evidence),
        "affected_sql_session_workload": affected_sql_workload,
        "rca": {
            "status": "included" if rca_result else "not_requested",
            "result": rca_result,
            "note": "AI conclusions are a hypothesis derived from platform telemetry, not verified "
            "authoritative telemetry themselves." if rca_result else None,
        },
        "notification_history": [
            {
                "id": n.id,
                "severity": n.severity,
                "status": n.status,
                "recipients": n.recipients,
                "attempt_count": n.attempt_count,
                "last_error_category": n.last_error_category,
                "sent_at": n.sent_at.isoformat() if n.sent_at else None,
                "created_at": n.created_at.isoformat(),
            }
            for n in notifications
        ],
    }

    return {
        "data_available": True,
        "data_completeness_note": (
            f"Built from {len(events)} timeline event(s), {len(evidence)} correlated anomaly(ies), "
            f"and {len(notifications)} notification event(s)."
        ),
        "limitations": limitations,
        "content": content,
    }


async def build_performance_report(
    *,
    database: MonitoredDatabase,
    period_start: datetime,
    period_end: datetime,
    telemetry_repo: TelemetryRepository,
    anomaly_repo: AnomalyRepository,
    incident_repo: IncidentRepository,
) -> dict:
    health_points = await telemetry_repo.recent_instance_health(
        database.id, limit=5000, since=period_start, until=period_end
    )
    session_samples = await telemetry_repo.recent_session_samples(
        database.id, limit=2000, since=period_start, until=period_end
    )
    anomalies = await anomaly_repo.list_recent(database.id, limit=500, since=period_start, until=period_end)
    all_incidents = await incident_repo.list_for_database(database.id, limit=500)
    incidents_in_period = [i for i in all_incidents if period_start <= ensure_aware(i.opened_at) <= period_end]

    wait_summary = await _wait_summary(telemetry_repo, database.id)

    active_session_counts = [p.active_session_count for p in health_points if p.active_session_count is not None]
    distinct_usernames = sorted({s.username for s in session_samples if s.username})
    distinct_programs = sorted({s.program for s in session_samples if s.program})

    content = {
        "database": {"id": database.id, "name": database.name},
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "cpu": {"available": False, "reason": "No V$SYSSTAT/host CPU telemetry is collected by this platform yet."},
        "io": {"available": False, "reason": "No V$SYSSTAT I/O telemetry is collected by this platform yet."},
        "waits": wait_summary,
        "workload": {
            "distinct_usernames": distinct_usernames,
            "distinct_programs": distinct_programs,
            "session_samples_in_period": len(session_samples),
        },
        "throughput_transactions": {
            "available": False,
            "reason": "No transaction/commit-rate telemetry is collected by this platform yet.",
        },
        "historical_comparison": {
            "active_session_count_timeseries": [
                {"sampled_at": p.sampled_at.isoformat(), "active_session_count": p.active_session_count}
                for p in reversed(health_points)
            ],
            "active_session_count_avg": round(sum(active_session_counts) / len(active_session_counts), 2)
            if active_session_counts
            else None,
            "active_session_count_max": max(active_session_counts) if active_session_counts else None,
        },
        "anomalies": [
            {
                "id": a.id,
                "metric_key": a.metric_key,
                "severity": a.severity,
                "detected_at": a.detected_at.isoformat(),
                "observed_value": a.observed_value,
                "baseline_mean": a.baseline_mean,
            }
            for a in anomalies
        ],
        "incidents_in_period": [
            {"id": i.id, "title": i.title, "severity": i.severity, "status": i.status, "opened_at": i.opened_at.isoformat()}
            for i in incidents_in_period
        ],
        "significant_deviations": [a.metric_key for a in anomalies if a.severity in ("HIGH", "CRITICAL")],
    }

    limitations = [
        "CPU and I/O sections are unavailable - this platform does not collect V$SYSSTAT telemetry yet.",
        "Throughput/transaction rate is unavailable - no transaction telemetry is collected.",
        "Wait profile reflects the two most recent collection rounds at generation time, "
        "not an aggregate over the full reporting period.",
    ]

    return {
        "data_available": True,
        "data_completeness_note": (
            f"Built from {len(health_points)} health sample(s), {len(session_samples)} session "
            f"sample(s), and {len(anomalies)} anomaly(ies) in the reporting period. CPU, I/O and "
            "throughput are unavailable - see limitations."
        ),
        "limitations": limitations,
        "content": content,
    }


def _serialize_regression(e) -> dict:
    return {
        "sql_id": e.sql_id,
        "regression_kind": e.regression_kind,
        "severity": e.severity,
        "status": e.status,
        "first_detected_at": e.first_detected_at.isoformat(),
        "last_detected_at": e.last_detected_at.isoformat(),
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
        "ratio": e.ratio,
        "detection_note": e.detection_note,
    }


async def build_sql_regression_report(*, database: MonitoredDatabase, sql_repo: SqlTelemetryRepository) -> dict:
    if not await sql_repo.has_any_stat_samples(database.id):
        reason = "No SQL statistics have been collected yet for this database."
        return {
            "data_available": False,
            "data_completeness_note": reason,
            "limitations": [reason],
            "content": {"database": {"id": database.id, "name": database.name}, "sections": {}},
        }

    top_sql = await _top_sql_summary(sql_repo, database.id, limit=25)
    all_regressions = await sql_repo.list_regressions(database.id, status=None, limit=100)
    open_regressions = [e for e in all_regressions if e.status == "OPEN"]
    resolved_regressions = [e for e in all_regressions if e.status != "OPEN"]

    content = {
        "database": {"id": database.id, "name": database.name},
        "top_sql": top_sql,
        "open_regressions": [_serialize_regression(e) for e in open_regressions],
        "recently_resolved_regressions": [_serialize_regression(e) for e in resolved_regressions[:20]],
    }
    limitations = [
        "Regression detection is deterministic (plan change, or elapsed-per-execution degradation "
        "vs this statement's own recent history), not a statistical anomaly model."
    ]
    if not top_sql.get("available"):
        limitations.append(top_sql.get("reason", "Top SQL delta is unavailable."))

    return {
        "data_available": True,
        "data_completeness_note": (
            f"{len(open_regressions)} open and {len(resolved_regressions)} resolved regression event(s) "
            "found from this platform's own SQL collection history."
        ),
        "limitations": limitations,
        "content": content,
    }


async def build_capacity_report(*, database: MonitoredDatabase, capacity_repo: CapacityRepository) -> dict:
    if not await capacity_repo.has_any_samples(database.id):
        reason = "No capacity telemetry has been collected yet for this database."
        return {
            "data_available": False,
            "data_completeness_note": reason,
            "limitations": [reason],
            "content": {"database": {"id": database.id, "name": database.name}, "sections": {}},
        }

    capacity = await _capacity_summary(capacity_repo, database.id, limit=100)
    all_alerts = await capacity_repo.list_alerts(database.id, status=None, limit=100)
    open_alerts = [a for a in all_alerts if a.status == "OPEN"]
    resolved_alerts = [a for a in all_alerts if a.status != "OPEN"]

    content = {
        "database": {"id": database.id, "name": database.name},
        "tablespaces": capacity,
        "open_alerts": [_serialize_capacity_alert(a) for a in open_alerts],
        "recently_resolved_alerts": [_serialize_capacity_alert(a) for a in resolved_alerts[:20]],
    }
    limitations = [
        "Growth rate and time-to-full projection are a deterministic linear fit over the last "
        "7 days of collection, not a statistical forecast model."
    ]

    return {
        "data_available": True,
        "data_completeness_note": (
            f"{len(open_alerts)} open and {len(resolved_alerts)} resolved capacity alert(s) "
            "found from this platform's own tablespace collection history."
        ),
        "limitations": limitations,
        "content": content,
    }


async def build_health_report(
    *,
    database: MonitoredDatabase,
    now: datetime,
    telemetry_repo: TelemetryRepository,
    collector_status_repo: CollectorStatusRepository,
    anomaly_repo: AnomalyRepository,
    sql_repo: SqlTelemetryRepository,
    capacity_repo: CapacityRepository,
    config_repo: ConfigTelemetryRepository,
) -> dict:
    health = await _health_summary(
        telemetry_repo, collector_status_repo, anomaly_repo, sql_repo, capacity_repo, config_repo, database.id, now
    )
    unavailable = [c["category"] for c in health["checks"] if c["status"] == "UNAVAILABLE"]

    content = {
        "database": {"id": database.id, "name": database.name},
        "checked_at": now.isoformat(),
        "overall": health["summary"],
        "categories": health["checks"],
    }
    limitations = [
        f"{len(unavailable)} of {len(health['checks'])} health categories are unavailable "
        f"(no collector implemented yet): {', '.join(unavailable)}."
    ]

    return {
        "data_available": True,
        "data_completeness_note": (
            f"{health['summary']['categories_evaluated']} of {health['summary']['categories_total']} "
            "health categories were actually evaluated from real telemetry; the rest are honestly "
            "reported as unavailable rather than assumed to pass."
        ),
        "limitations": limitations,
        "content": content,
    }
