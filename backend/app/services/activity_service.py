"""Activity tab, Phase 1: the fleet vitals pulse strip - one row per active monitored
database answering "is it healthy right now," reusing the exact same deterministic
scoring already proven for the single-database Health report
(app/reports/health_check.py's run_health_checks/summarize_health via
app/reports/builders.py's _health_summary) rather than inventing a new score.

_health_summary itself is NOT reused directly (it's private to builders.py and its
signature takes one database_id at a time with no way to inject a pre-batched open-incident
count) - this module runs the same repo calls it does, per active database, concurrently.

Each concurrent per-database task opens its OWN SessionLocal() rather than sharing the
request-scoped session passed into fleet_vitals() - SQLAlchemy's AsyncSession is explicitly
documented as "not safe for use in concurrent tasks" (sqlalchemy.ext.asyncio.session), so
genuine concurrency here requires genuinely separate sessions (and therefore separate pooled
connections), matching the same "own SessionLocal() per unit of work" convention already used
by CollectorService/AnalyticsService/etc.'s run_once_for_database.

Deliberately excludes configuration-change telemetry (unlike _health_summary) - config-change
detection already has a dedicated home in the Change Intelligence tab, and skipping it here
saves one repo call per database on every fleet-pulse poll.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models.capacity_telemetry import CapacityAlertEvent
from app.models.database import MonitoredDatabase
from app.reports.health_check import run_health_checks, summarize_health
from app.repositories.analytics_repo import AnomalyRepository
from app.repositories.capacity_repo import CapacityRepository
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.sql_repo import SqlTelemetryRepository
from app.repositories.telemetry_repo import CollectorStatusRepository, TelemetryRepository
from app.telemetry_views import build_blocking_chains, latest_round

# Bounds how many databases' vitals are computed concurrently in one fleet-pulse request - each
# concurrent task holds its own pooled connection (see module docstring), so this also bounds
# how many connections one request can check out at once against the engine's pool.
_MAX_CONCURRENT_DATABASES = 10

_HIGH_SEVERITIES = {"HIGH", "CRITICAL"}


@dataclass(frozen=True)
class FleetVitalsRow:
    database_id: str
    database_name: str
    overall_status: str  # PASS|WARN|FAIL|UNAVAILABLE (app/reports/health_check.py)
    open_incident_count: int
    blocking_present: bool
    capacity_trend: str  # "stable"|"warning"|"critical"


def _capacity_trend(open_alerts: list[CapacityAlertEvent]) -> str:
    if any(a.severity in _HIGH_SEVERITIES for a in open_alerts):
        return "critical"
    if open_alerts:
        return "warning"
    return "stable"


async def _vitals_for_one(
    database: MonitoredDatabase,
    *,
    open_incident_counts: dict[str, int],
    now: datetime,
    semaphore: asyncio.Semaphore,
) -> FleetVitalsRow:
    async with semaphore, SessionLocal() as db:
        telemetry_repo = TelemetryRepository(db)
        collector_status_repo = CollectorStatusRepository(db)
        anomaly_repo = AnomalyRepository(db)
        sql_repo = SqlTelemetryRepository(db)
        capacity_repo = CapacityRepository(db)

        latest_health = await telemetry_repo.latest_instance_health(database.id)
        collector_status = await collector_status_repo.get_by_database_id(database.id)
        session_samples = await telemetry_repo.recent_session_samples(database.id, limit=500)
        recent_anomalies = await anomaly_repo.list_recent(database.id, limit=200, since=now - timedelta(hours=24))
        has_sql_telemetry = await sql_repo.has_any_stat_samples(database.id)
        open_sql_regressions = await sql_repo.list_regressions(database.id, status="OPEN", limit=200)
        has_capacity_telemetry = await capacity_repo.has_any_samples(database.id)
        open_capacity_alerts = await capacity_repo.list_alerts(database.id, status="OPEN", limit=200)

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
        )
        overall_status = summarize_health(checks)["overall_status"]
        chains = build_blocking_chains(latest_round(session_samples)) if session_samples else []

        return FleetVitalsRow(
            database_id=database.id,
            database_name=database.name,
            overall_status=overall_status,
            open_incident_count=open_incident_counts.get(database.id, 0),
            blocking_present=bool(chains),
            capacity_trend=_capacity_trend(open_capacity_alerts),
        )


async def fleet_vitals(db: AsyncSession, *, now: datetime) -> list[FleetVitalsRow]:
    """One row per active monitored database, computed concurrently (not a serial loop -
    9 sequential awaited queries per database would make a real fleet visibly slow well
    before it's "large"). Open-incident counts are fetched ONCE up front and grouped in
    Python, not re-queried per database."""
    database_repo = MonitoredDatabaseRepository(db)
    active_ids = await database_repo.list_active_ids()
    if not active_ids:
        return []
    databases = await database_repo.list_all(database_ids=active_ids, limit=len(active_ids))

    open_incident_counts: dict[str, int] = {}
    for incident in await IncidentRepository(db).list_all(
        status="OPEN", monitored_database_ids=active_ids, limit=len(active_ids) * 50
    ):
        open_incident_counts[incident.monitored_database_id] = open_incident_counts.get(
            incident.monitored_database_id, 0
        ) + 1

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_DATABASES)

    rows = await asyncio.gather(
        *[
            _vitals_for_one(database, open_incident_counts=open_incident_counts, now=now, semaphore=semaphore)
            for database in databases
        ]
    )
    return list(rows)
