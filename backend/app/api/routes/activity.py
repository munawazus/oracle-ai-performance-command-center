from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.base import utcnow
from app.reports.activity_feed import build_activity_feed
from app.repositories.analytics_repo import AnomalyRepository
from app.repositories.capacity_repo import CapacityRepository
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.sql_repo import SqlTelemetryRepository
from app.schemas.activity import ActivityEventResponse, FleetVitalsRowResponse
from app.security.deps import require_permission
from app.services.activity_service import fleet_vitals

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("/fleet-pulse", response_model=list[FleetVitalsRowResponse])
async def get_fleet_pulse(
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> list[FleetVitalsRowResponse]:
    """One row per active monitored database - the Activity tab's "what's happening right
    now" pulse strip. Fleet-wide by design (Master Plan follow-up: Activity tab Phase 1)."""
    rows = await fleet_vitals(db, now=utcnow())
    return [FleetVitalsRowResponse.model_validate(row) for row in rows]


@router.get("/feed", response_model=list[ActivityEventResponse])
async def get_activity_feed(
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    database_ids: list[str] | None = Query(default=None),
    severity: str | None = Query(default=None),
    limit: int = Query(default=200, le=500),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> list[ActivityEventResponse]:
    """The Activity tab's unified, fleet-wide event timeline (Phase 2) - merges anomalies,
    SQL regressions, capacity alerts, and incident lifecycle events, normalized and
    time-sorted by app/reports/activity_feed.py. `database_ids` unset means every active
    database; a historical range (`since`/`until` given) also includes resolved
    regressions/alerts, not just currently-open ones."""
    database_repo = MonitoredDatabaseRepository(db)
    target_ids = database_ids if database_ids is not None else await database_repo.list_active_ids()
    databases = await database_repo.list_all(database_ids=target_ids, limit=max(len(target_ids), 1)) if target_ids else []
    database_names = {d.id: d.name for d in databases}

    is_historical_range = since is not None or until is not None
    regression_status = None if is_historical_range else "OPEN"
    alert_status = None if is_historical_range else "OPEN"

    anomalies = await AnomalyRepository(db).list_recent_fleet(
        limit=limit, since=since, until=until, monitored_database_ids=target_ids
    )
    sql_regressions = await SqlTelemetryRepository(db).list_regressions_fleet(
        status=regression_status, limit=limit, monitored_database_ids=target_ids
    )
    capacity_alerts = await CapacityRepository(db).list_alerts_fleet(
        status=alert_status, limit=limit, monitored_database_ids=target_ids
    )
    incidents = await IncidentRepository(db).list_all(status=None, monitored_database_ids=target_ids, limit=limit)

    events = build_activity_feed(
        anomalies=anomalies,
        sql_regressions=sql_regressions,
        capacity_alerts=capacity_alerts,
        incidents=incidents,
        database_names=database_names,
        since=since,
        until=until,
        severity=severity,
        limit=limit,
    )
    return [ActivityEventResponse.model_validate(e) for e in events]
