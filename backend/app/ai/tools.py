"""The controlled tool layer (master plan Sec 9). Every tool here:

- reads ONLY this platform's own PostgreSQL repository (session_samples, wait_snapshots,
  incidents, anomalies, capabilities, collector_status, sql_statements, sql_stat_samples,
  sql_plan_snapshots, sql_regression_events, tablespace_samples, capacity_alert_events) -
  never opens an Oracle connection, never sees an Oracle credential, never issues SQL against
  a monitored database. That is not a policy this code has to remember to follow - there is
  no code path here that even could reach a DatabaseCredential or Secret row.
- returns a plain, JSON-serializable dict, always including an "available" flag when data
  might legitimately be missing (too little telemetry, no capability probe run yet, ...) so
  the caller (and eventually the LLM) can say "insufficient evidence" instead of a tool
  silently returning something that looks like a real empty result.
- takes only IDs and small parameters, never a raw SQL fragment or arbitrary expression.

Master plan Sec 9 names one more tool (get_host_metrics) that this phase deliberately does NOT
implement, because its underlying collector doesn't exist yet - host metrics is a later phase.
Registering a tool that always returns empty/fake data would be exactly the fabricated-AI-
evidence problem master plan Sec 2 forbids, so that tool simply isn't offered yet rather than
faked. get_top_sql/get_sql_plan/get_sql_regressions (Enterprise Readiness Plan D.1),
get_tablespace_trend (Enterprise Readiness Plan D.2), get_statspack_summary (Enterprise
Readiness Plan D.3), and get_change_history (Enterprise Readiness Plan D.4) were all in that
same never-implemented list until their collectors existed to back them for real.
"""

from typing import Sequence, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.capacity_metrics import (
    GROWTH_TREND_WINDOW,
    TablespaceSampleLike,
    compute_growth_trend,
    describe_usage,
)
from app.analytics.config_metrics import compute_parameter_changes
from app.analytics.metrics import WaitRow, aggregate_by_wait_class, compute_wait_class_rates
from app.analytics.sql_metrics import SORT_KEYS, SqlStatRowLike, compute_sql_deltas, rank_sql_deltas
from app.analytics.statspack_metrics import adapt_sql_samples
from app.models.base import utcnow
from app.repositories.analytics_repo import BaselineRepository
from app.repositories.capacity_repo import CapacityRepository
from app.repositories.config_repo import ConfigTelemetryRepository
from app.repositories.database_repo import CapabilityRepository, MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.sql_repo import SqlTelemetryRepository
from app.repositories.statspack_repo import StatspackRepository
from app.repositories.telemetry_repo import CollectorStatusRepository, TelemetryRepository
from app.telemetry_views import build_blocking_chains, latest_round

# Capped for the same reason get_top_sql's own limit defaults lower than the API's - every row
# here goes into a prompt, not a table the user scrolls.
_TABLESPACE_TREND_MAX_ROWS = 20
_STATSPACK_TOP_SQL_MAX_ROWS = 20

TOOL_DESCRIPTIONS = {
    "get_database_health": "Registration, detected capabilities, collector status, and the latest instance health sample for one database.",
    "get_active_sessions": "The most recent V$SESSION sampling round for one database.",
    "get_wait_profile": "Per-wait-class time-waited rate, computed from the two most recent wait_snapshots rounds.",
    "get_blocking_chain": "Blocker/blocked session pairs from the most recent session sample.",
    "get_incident": "An incident's detail, timeline, and correlated anomaly evidence.",
    "compare_baseline_periods": "A metric's rolling baseline at two different hours of day, side by side.",
    "get_top_sql": "Top SQL by per-interval delta (elapsed/CPU/gets/reads/executions), computed from the two most recent SQL collection rounds.",
    "get_sql_plan": "One SQL statement's captured plan history - every distinct plan hash observed, when each was first seen, and its per-execution cost.",
    "get_sql_regressions": "Open SQL regression events (plan change or elapsed-per-execution degradation) detected deterministically from this platform's own SQL history.",
    "get_tablespace_trend": "Per-tablespace %used, growth rate, and projected time-to-full - all tablespaces sorted by fullness, or one tablespace's full detail.",
    "get_statspack_summary": "Top wait classes and top SQL by delta between the two most recently synced Statspack snapshots, for databases that have Statspack installed and actively snapshotting.",
    "get_change_history": "Configuration parameter changes (newly set, changed, or reverted to default) detected between the two most recently collected configuration rounds.",
}


async def get_database_health(db: AsyncSession, database_id: str) -> dict:
    database = await MonitoredDatabaseRepository(db).get(database_id)
    if database is None:
        return {"available": False, "reason": "database not found"}

    capability = await CapabilityRepository(db).get_by_database_id(database_id)
    status = await CollectorStatusRepository(db).get_by_database_id(database_id)
    health = await TelemetryRepository(db).latest_instance_health(database_id)

    return {
        "available": True,
        "database": {
            "name": database.name,
            "host": database.host,
            "service_name": database.service_name,
            "edition_hint": database.edition_hint,
            "is_active": database.is_active,
        },
        "capabilities": (
            {
                "detected_version": capability.detected_version,
                "detected_edition": capability.detected_edition,
                "is_rac": capability.is_rac,
                "statspack_available": capability.statspack_available,
                "last_probed_at": capability.last_probed_at.isoformat() if capability.last_probed_at else None,
            }
            if capability and capability.last_probed_at
            else {"available": False, "reason": "capabilities never probed"}
        ),
        "collector_status": (
            {
                "last_success_at": status.last_success_at.isoformat() if status.last_success_at else None,
                "consecutive_failures": status.consecutive_failures,
                "last_error_category": status.last_error_category,
            }
            if status
            else {"available": False, "reason": "collector has never run"}
        ),
        "latest_instance_health": (
            {
                "sampled_at": health.sampled_at.isoformat(),
                "reachability": health.reachability,
                "instance_status": health.instance_status,
                "active_session_count": health.active_session_count,
            }
            if health
            else {"available": False, "reason": "no telemetry collected yet"}
        ),
    }


async def get_active_sessions(db: AsyncSession, database_id: str, limit: int = 50) -> dict:
    samples = await TelemetryRepository(db).recent_session_samples(database_id, limit=limit)
    if not samples:
        return {"available": False, "reason": "no session telemetry collected yet"}

    current_round = latest_round(samples)
    latest_round_time = current_round[0].sampled_at
    return {
        "available": True,
        "sampled_at": latest_round_time.isoformat(),
        "session_count": len(current_round),
        "sessions": [
            {
                "sid": s.sid,
                "username": s.username,
                "sql_id": s.sql_id,
                "event": s.event,
                "wait_class": s.wait_class,
                "status": s.status,
                "blocking_session": s.blocking_session,
                "machine": s.machine,
                "program": s.program,
                "module": s.module,
                "action": s.action,
                "state": s.state,
                "last_call_et": s.last_call_et,
            }
            for s in current_round
        ],
    }


async def get_wait_profile(db: AsyncSession, database_id: str) -> dict:
    rounds = await TelemetryRepository(db).latest_two_wait_rounds(database_id)
    if rounds is None:
        return {"available": False, "reason": "fewer than two collection rounds - no rate can be computed yet"}

    (t1, rows1), (t2, rows2) = rounds
    elapsed_seconds = (t2 - t1).total_seconds()
    previous = aggregate_by_wait_class(rows1)
    current = aggregate_by_wait_class(rows2)
    rates = compute_wait_class_rates(previous, current, elapsed_seconds)

    return {
        "available": True,
        "sampled_at": t2.isoformat(),
        "elapsed_seconds": elapsed_seconds,
        "wait_classes": [
            {"wait_class": wc, "centisec_waited_per_sec": round(rate, 2)}
            for wc, rate in sorted(rates.items(), key=lambda kv: kv[1], reverse=True)
        ],
    }


async def get_blocking_chain(db: AsyncSession, database_id: str) -> dict:
    samples = await TelemetryRepository(db).recent_session_samples(database_id, limit=500)
    if not samples:
        return {"available": False, "reason": "no session telemetry collected yet"}

    current_round = latest_round(samples)
    return {
        "available": True,
        "sampled_at": current_round[0].sampled_at.isoformat(),
        "blocking_chains": build_blocking_chains(current_round),
    }


async def get_incident(db: AsyncSession, incident_id: str) -> dict:
    repo = IncidentRepository(db)
    incident = await repo.get(incident_id)
    if incident is None:
        return {"available": False, "reason": "incident not found"}

    events = await repo.list_events(incident_id)
    return {
        "available": True,
        "incident": {
            "id": incident.id,
            "title": incident.title,
            "severity": incident.severity,
            "status": incident.status,
            "opened_at": incident.opened_at.isoformat(),
            "last_evidence_at": incident.last_evidence_at.isoformat(),
            "monitored_database_id": incident.monitored_database_id,
        },
        "timeline": [
            {"occurred_at": e.occurred_at.isoformat(), "event_type": e.event_type, "detail": e.detail}
            for e in events
        ],
    }


async def compare_baseline_periods(db: AsyncSession, database_id: str, metric_key: str, hour_a: int, hour_b: int) -> dict:
    repo = BaselineRepository(db)
    baseline_a = await repo.get(database_id, metric_key, hour_a)
    baseline_b = await repo.get(database_id, metric_key, hour_b)

    def _describe(baseline, hour: int) -> dict:
        if baseline is None or baseline.sample_count == 0:
            return {"hour_of_day": hour, "available": False, "reason": "no baseline history for this hour yet"}
        return {
            "hour_of_day": hour,
            "available": True,
            "sample_count": baseline.sample_count,
            "mean": round(baseline.mean, 3),
            "stddev": round((baseline.m2 / baseline.sample_count) ** 0.5, 3) if baseline.sample_count > 0 else 0.0,
        }

    return {
        "available": True,
        "metric_key": metric_key,
        "period_a": _describe(baseline_a, hour_a),
        "period_b": _describe(baseline_b, hour_b),
    }


async def get_top_sql(db: AsyncSession, database_id: str, limit: int = 10, sort_by: str = "elapsed_time") -> dict:
    """Never a live V$SQL query - reads the two most recent already-collected SQL rounds
    from Postgres. limit defaults lower than the API's own default (10 vs 25): every row
    here goes into a prompt, not a table the user scrolls."""
    if sort_by not in SORT_KEYS:
        sort_by = "elapsed_time"  # an LLM-supplied value must never raise - fall back quietly

    sql_repo = SqlTelemetryRepository(db)
    rounds = await sql_repo.latest_two_sql_rounds(database_id)
    if rounds is None:
        return {
            "available": False,
            "reason": "fewer than two SQL collection rounds - no per-interval delta can be computed yet",
        }

    (t1, rows1), (t2, rows2) = rounds
    elapsed_seconds = (t2 - t1).total_seconds()
    deltas = rank_sql_deltas(compute_sql_deltas(rows1, rows2, elapsed_seconds), sort_by=sort_by, limit=limit)
    statements = await sql_repo.statements_for_sql_ids(database_id, [d.sql_id for d in deltas])

    statement_rows = []
    for d in deltas:
        stmt = statements.get(d.sql_id)
        statement_rows.append(
            {
                "sql_id": d.sql_id,
                "plan_hash_value": d.plan_hash_value,
                "sql_text": stmt.sql_text if stmt else None,
                "executions_delta": d.executions,
                "elapsed_time_us_delta": d.elapsed_time_us,
                "cpu_time_us_delta": d.cpu_time_us,
                "buffer_gets_delta": d.buffer_gets,
                "elapsed_per_exec_us": d.elapsed_per_exec_us,
            }
        )

    return {
        "available": True,
        "sampled_at": t2.isoformat(),
        "elapsed_seconds": elapsed_seconds,
        "sort_by": sort_by,
        "statements": statement_rows,
    }


async def get_sql_plan(db: AsyncSession, database_id: str, sql_id: str) -> dict:
    """The full plan history for one statement, not just its current plan - lets a model
    answer "did the plan change, and did it get worse" from one call. Served from persisted
    plan snapshots, never a live V$SQL_PLAN query."""
    sql_repo = SqlTelemetryRepository(db)
    statement = await sql_repo.get_statement(database_id, sql_id)
    if statement is None:
        return {"available": False, "reason": "no SQL telemetry collected for this statement yet"}

    snapshots = await sql_repo.list_plan_snapshots(database_id, sql_id)
    return {
        "available": True,
        "sql_id": sql_id,
        "sql_text": statement.sql_text,
        "plan_count": len(snapshots),
        "plan_changed": len(snapshots) > 1,
        "plans": [
            {
                "plan_hash_value": s.plan_hash_value,
                "first_seen_at": s.first_seen_at.isoformat(),
                "last_seen_at": s.last_seen_at.isoformat(),
                "plan_lines": s.plan_lines,
            }
            for s in snapshots
        ],
    }


async def get_sql_regressions(db: AsyncSession, database_id: str, limit: int = 20) -> dict:
    events = await SqlTelemetryRepository(db).list_regressions(database_id, status="OPEN", limit=limit)
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


async def get_tablespace_trend(db: AsyncSession, database_id: str, tablespace_name: str | None = None) -> dict:
    """All tablespaces sorted by %used descending (capped, since every row goes into a
    prompt) when tablespace_name is omitted; one tablespace's full detail - including growth
    trend and open alerts - when given. Never a live Oracle query on read. An LLM-supplied
    tablespace_name that doesn't match any known tablespace falls back to the all-tablespaces
    view rather than raising - same "never error on a bad LLM-supplied value" rule get_top_sql
    already follows for an invalid sort_by."""
    capacity_repo = CapacityRepository(db)
    latest = await capacity_repo.latest_round(database_id)
    if latest is None:
        return {"available": False, "reason": "no capacity telemetry collected yet for this database"}

    _, current_rows = latest
    now = utcnow()
    row_by_name = {r.tablespace_name: r for r in current_rows}

    if tablespace_name is not None and tablespace_name in row_by_name:
        row = row_by_name[tablespace_name]
        usage = describe_usage(cast(TablespaceSampleLike, row))
        history = await capacity_repo.samples_in_window(
            database_id, row.tablespace_name, now - GROWTH_TREND_WINDOW, now
        )
        trend = compute_growth_trend(
            cast(Sequence[TablespaceSampleLike], history), now,
            current_used_bytes=row.used_bytes, max_bytes=row.max_bytes,
        )
        open_alerts = [
            a for a in await capacity_repo.list_alerts(database_id, status="OPEN", limit=200)
            if a.tablespace_name == row.tablespace_name
        ]
        return {
            "available": True,
            "tablespace_name": row.tablespace_name,
            "contents": row.contents,
            "pct_used": round(usage.pct_used, 2),
            "pct_of_max": round(usage.pct_of_max, 2) if usage.pct_of_max is not None else None,
            "allocated_bytes": row.allocated_bytes,
            "used_bytes": row.used_bytes,
            "max_bytes": row.max_bytes,
            "growth_bytes_per_day": trend.bytes_per_day,
            "projected_days_to_full": trend.projected_days_to_full,
            "projection_reason": trend.projection_reason,
            "open_alerts": [
                {"alert_kind": a.alert_kind, "severity": a.severity, "detection_note": a.detection_note}
                for a in open_alerts
            ],
        }

    open_alert_severity_by_tablespace = {
        a.tablespace_name: a.severity
        for a in await capacity_repo.list_alerts(database_id, status="OPEN", limit=200)
    }
    ranked = sorted(
        current_rows,
        key=lambda r: (r.used_bytes / r.allocated_bytes) if r.allocated_bytes else 0.0,
        reverse=True,
    )[:_TABLESPACE_TREND_MAX_ROWS]

    return {
        "available": True,
        "tablespace_count": len(current_rows),
        "tablespaces": [
            {
                "tablespace_name": row.tablespace_name,
                "contents": row.contents,
                "pct_used": round(describe_usage(cast(TablespaceSampleLike, row)).pct_used, 2),
                "max_bytes": row.max_bytes,
                "open_alert_severity": open_alert_severity_by_tablespace.get(row.tablespace_name),
            }
            for row in ranked
        ],
    }


async def get_statspack_summary(db: AsyncSession, database_id: str) -> dict:
    """Top wait classes and top SQL by delta between the two most recently synced Statspack
    snapshots. Never a live Oracle query on read, and always available: false (with an honest
    reason) for the large majority of databases that don't have Statspack installed - never
    an error. Deliberately NOT gathered automatically into every Copilot/RCA evidence bundle
    the way get_top_sql/get_tablespace_trend are: a Statspack delta can span days at the
    DBA's own discretion, so auto-including it risks presenting stale evidence as current -
    it stays a targeted, on-demand tool, same reasoning get_sql_plan already follows."""
    capability = await CapabilityRepository(db).get_by_database_id(database_id)
    if not capability or not capability.statspack_available:
        return {"available": False, "reason": "Statspack is not installed on this database"}

    statspack_repo = StatspackRepository(db)
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
        limit=_STATSPACK_TOP_SQL_MAX_ROWS,
    )
    statements = await SqlTelemetryRepository(db).statements_for_sql_ids(
        database_id, [d.sql_id for d in sql_deltas]
    )

    return {
        "available": True,
        "from_snap_id": from_snap.snap_id,
        "from_snap_time": from_snap.snap_time.isoformat(),
        "to_snap_id": to_snap.snap_id,
        "to_snap_time": to_snap.snap_time.isoformat(),
        "elapsed_hours": round(elapsed_seconds / 3600.0, 2) if elapsed_seconds > 0 else 0.0,
        "wait_classes": [
            {"wait_class": wc, "centisec_waited_per_hour": round(rate * 3600, 2)}
            for wc, rate in sorted(wait_rates.items(), key=lambda kv: kv[1], reverse=True)
        ],
        "top_sql": [
            {
                "sql_id": d.sql_id,
                "plan_hash_value": d.plan_hash_value,
                "sql_text": statements[d.sql_id].sql_text if d.sql_id in statements else None,
                "executions_delta": d.executions,
                "elapsed_time_us_delta": d.elapsed_time_us,
                "elapsed_per_exec_us": d.elapsed_per_exec_us,
            }
            for d in sql_deltas
        ],
    }


async def get_change_history(db: AsyncSession, database_id: str) -> dict:
    """Configuration parameter changes (newly set, changed, or reverted to default) between
    the two most recently collected configuration rounds. Never a live Oracle query on read.
    Deliberately NOT gathered automatically into every Copilot/RCA evidence bundle: a
    configuration-drift lookback spans whatever gap exists between collection rounds
    (potentially many hours or days for a newly-onboarded database), so auto-including it
    risks presenting stale evidence as current - same reasoning get_sql_plan/
    get_statspack_summary already follow, kept a targeted, on-demand tool."""
    config_repo = ConfigTelemetryRepository(db)
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
