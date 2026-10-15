"""Runs the deterministic SQL regression comparator (app/analytics/sql_regression.py) on its
own schedule - the SQL collection interval, not the 15s main-collector interval, since
comparing 24h of history is pointless more often than new SQL telemetry actually arrives.

Deliberately not wired into IncidentService/NotificationService/IntegrationService in this
pass (Enterprise Readiness Plan D.1a) - regressions are fully detected, persisted, and
readable via the API/UI/reports without that; incident correlation and alert delivery for
them is an explicitly deferred, separately-scoped addition (see the plan's Log entry), not
part of the core feature.
"""

from datetime import datetime
from typing import Sequence, cast

from app.analytics.sql_metrics import SqlStatRowLike, aggregate_window
from app.analytics.sql_regression import (
    SQL_BASELINE_WINDOW,
    SQL_CURRENT_WINDOW,
    SQL_REGRESSION_RESOLVE_AFTER,
    detect_regressions,
)
from app.db import SessionLocal
from app.logging_config import get_logger
from app.models.base import utcnow
from app.models.sql_telemetry import SqlStatSample
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.repositories.sql_repo import SqlTelemetryRepository

logger = get_logger(__name__)

# Same severity vocabulary/ranking as IncidentService.SEVERITY_RANK - duplicated rather than
# imported to keep this module's only dependency on incident_service.py at zero, since
# regressions aren't correlated into incidents in this pass (see module docstring).
_SEVERITY_RANK = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def _group_by_round(rows: list[SqlStatSample]) -> list[list[SqlStatSample]]:
    by_ts: dict[datetime, list[SqlStatSample]] = {}
    for row in rows:
        by_ts.setdefault(row.sampled_at, []).append(row)
    return [by_ts[ts] for ts in sorted(by_ts)]


class SqlRegressionService:
    async def run_once_for_database(self, database_id: str) -> None:
        async with SessionLocal() as db:
            sql_repo = SqlTelemetryRepository(db)
            now = utcnow()
            baseline_start = now - SQL_BASELINE_WINDOW
            current_start = now - SQL_CURRENT_WINDOW

            baseline_rows = await sql_repo.stat_samples_in_window(database_id, baseline_start, current_start)
            current_rows = await sql_repo.stat_samples_in_window(database_id, current_start, now)

            # cast, not a real type mismatch: SqlStatSample satisfies SqlStatRowLike's
            # Protocol at runtime (same fields, same types) - mypy's SQLAlchemy plugin just
            # doesn't reconcile Mapped[int] against a doubly-nested Sequence[Sequence[...]]
            # Protocol parameter cleanly. Confirmed structurally correct, not suppressing a
            # genuine incompatibility.
            baseline_totals = aggregate_window(
                cast(Sequence[Sequence[SqlStatRowLike]], _group_by_round(baseline_rows))
            )
            current_totals = aggregate_window(
                cast(Sequence[Sequence[SqlStatRowLike]], _group_by_round(current_rows))
            )

            findings = detect_regressions(baseline_totals, current_totals)

            for finding in findings:
                existing = await sql_repo.find_open_regression(database_id, finding.sql_id, finding.regression_kind)
                # Severity only ever escalates to match the worst evidence seen so far, never
                # de-escalates on a milder observation - same principle IncidentService
                # already applies to incident severity.
                if existing and _SEVERITY_RANK[existing.severity] >= _SEVERITY_RANK[finding.severity]:
                    effective_severity = existing.severity
                else:
                    effective_severity = finding.severity

                fields = {
                    "severity": effective_severity,
                    "baseline_elapsed_per_exec_us": finding.baseline_elapsed_per_exec_us,
                    "current_elapsed_per_exec_us": finding.current_elapsed_per_exec_us,
                    "ratio": finding.ratio,
                    "baseline_executions": finding.baseline_executions,
                    "current_executions": finding.current_executions,
                    "baseline_plan_hash_value": finding.baseline_plan_hash_value,
                    "current_plan_hash_value": finding.current_plan_hash_value,
                    "detection_note": finding.detection_note,
                }
                if existing:
                    await sql_repo.touch_regression(existing, **fields)
                else:
                    await sql_repo.create_regression(
                        database_id, sql_id=finding.sql_id, regression_kind=finding.regression_kind, **fields
                    )

            resolved_count = await sql_repo.resolve_stale_regressions(
                database_id, now - SQL_REGRESSION_RESOLVE_AFTER
            )
            await db.commit()

            if findings or resolved_count:
                logger.info(
                    "sql_regression_detection_completed",
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
                logger.error("sql_regression_run_crashed", database_id=database_id)
