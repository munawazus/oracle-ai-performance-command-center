"""Runs the deterministic capacity-alert comparator (app/analytics/capacity_alerts.py) on its
own schedule - the capacity collection interval (default 1h), not the 15s main-collector
interval, since comparing a multi-day tablespace growth trend is pointless more often than new
capacity telemetry actually arrives.

Deliberately not wired into IncidentService/NotificationService/IntegrationService in this
pass (Enterprise Readiness Plan D.2), mirroring D.1's own deferral for SQL regressions -
alerts are fully detected, persisted, and readable via the API/UI/reports without that.
"""

from typing import Sequence, cast

from app.analytics.capacity_alerts import CAPACITY_ALERT_RESOLVE_AFTER, detect_capacity_alerts
from app.analytics.capacity_metrics import (
    GROWTH_TREND_WINDOW,
    GrowthTrend,
    TablespaceSampleLike,
    TablespaceUsage,
    compute_growth_trend,
    describe_usage,
)
from app.config import get_settings
from app.db import SessionLocal
from app.logging_config import get_logger
from app.models.base import utcnow
from app.repositories.capacity_repo import CapacityRepository
from app.repositories.database_repo import MonitoredDatabaseRepository

logger = get_logger(__name__)

# Same severity vocabulary/ranking as IncidentService.SEVERITY_RANK - duplicated rather than
# imported, same reasoning app/services/sql_regression_service.py gives.
_SEVERITY_RANK = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


class CapacityAlertService:
    async def run_once_for_database(self, database_id: str) -> None:
        settings = get_settings()
        async with SessionLocal() as db:
            db_repo = MonitoredDatabaseRepository(db)
            database = await db_repo.get(database_id)
            if database is None:
                return

            capacity_repo = CapacityRepository(db)
            now = utcnow()

            latest = await capacity_repo.latest_round(database_id)
            if latest is None:
                return
            _, current_rows = latest

            usages_with_trend: list[tuple[TablespaceUsage, GrowthTrend]] = []
            for row in current_rows:
                # cast, not a real type mismatch: TablespaceSample satisfies
                # TablespaceSampleLike's Protocol at runtime (same fields, same types) -
                # mypy's SQLAlchemy plugin just doesn't reconcile Mapped[int]/Mapped[str]
                # against a Protocol parameter cleanly (same quirk app/services/
                # sql_regression_service.py already documents for SqlStatRowLike).
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
                usages_with_trend.append((usage, trend))

            # A per-database override (set via PUT .../capacity-thresholds) always wins over
            # the fleet-wide default - "how full is too full" varies by environment far more
            # than one number can express for every registered database.
            findings = detect_capacity_alerts(
                usages_with_trend,
                warn_pct=database.capacity_warn_pct_used
                if database.capacity_warn_pct_used is not None
                else settings.capacity_warn_pct_used,
                critical_pct=database.capacity_critical_pct_used
                if database.capacity_critical_pct_used is not None
                else settings.capacity_critical_pct_used,
                warn_days=database.capacity_warn_days_to_full
                if database.capacity_warn_days_to_full is not None
                else settings.capacity_warn_days_to_full,
                critical_days=database.capacity_critical_days_to_full
                if database.capacity_critical_days_to_full is not None
                else settings.capacity_critical_days_to_full,
            )

            for finding in findings:
                existing = await capacity_repo.find_open_alert(
                    database_id, finding.tablespace_name, finding.alert_kind
                )
                # Severity only ever escalates to match the worst evidence seen so far, never
                # de-escalates on a milder observation - same principle IncidentService and
                # SqlRegressionService already apply.
                if existing and _SEVERITY_RANK[existing.severity] >= _SEVERITY_RANK[finding.severity]:
                    effective_severity = existing.severity
                else:
                    effective_severity = finding.severity

                fields = {
                    "severity": effective_severity,
                    "pct_used": finding.pct_used,
                    "pct_basis": finding.pct_basis,
                    "projected_days_to_full": finding.projected_days_to_full,
                    "current_used_bytes": finding.current_used_bytes,
                    "current_allocated_bytes": finding.current_allocated_bytes,
                    "current_max_bytes": finding.current_max_bytes,
                    "detection_note": finding.detection_note,
                }
                if existing:
                    await capacity_repo.touch_alert(existing, **fields)
                else:
                    await capacity_repo.create_alert(
                        database_id,
                        tablespace_name=finding.tablespace_name,
                        alert_kind=finding.alert_kind,
                        **fields,
                    )

            resolved_count = await capacity_repo.resolve_stale_alerts(
                database_id, now - CAPACITY_ALERT_RESOLVE_AFTER
            )
            await db.commit()

            if findings or resolved_count:
                logger.info(
                    "capacity_alert_detection_completed",
                    database_id=database_id,
                    findings=len(findings),
                    resolved=resolved_count,
                )

    async def run_once_all_active(self) -> None:
        async with SessionLocal() as db:
            db_repo = MonitoredDatabaseRepository(db)
            database_ids = await db_repo.list_active_ids()

        for database_id in database_ids:
            try:
                await self.run_once_for_database(database_id)
            except Exception:
                logger.error("capacity_alert_run_crashed", database_id=database_id)
