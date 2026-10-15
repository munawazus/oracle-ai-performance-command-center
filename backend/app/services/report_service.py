from datetime import datetime, timedelta

from app.exceptions import NotFoundError, ValidationAppError
from app.models.base import ensure_aware, utcnow
from app.models.database import MonitoredDatabase
from app.models.incident import Incident
from app.models.report import (
    REPORT_TYPE_CAPACITY,
    REPORT_TYPE_DAILY_DBA,
    REPORT_TYPE_HEALTH,
    REPORT_TYPE_INCIDENT,
    REPORT_TYPE_PERFORMANCE,
    REPORT_TYPE_SQL_REGRESSION,
    REPORT_TYPES,
    Report,
)
from app.reports import builders, export
from app.repositories.analytics_repo import AnomalyRepository
from app.repositories.capacity_repo import CapacityRepository
from app.repositories.config_repo import ConfigTelemetryRepository
from app.repositories.database_repo import CapabilityRepository, MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.notification_repo import NotificationEventRepository
from app.repositories.report_repo import ReportRepository
from app.repositories.sql_repo import SqlTelemetryRepository
from app.repositories.statspack_repo import StatspackRepository
from app.repositories.telemetry_repo import CollectorStatusRepository, TelemetryRepository
from app.services.audit_service import AuditService

MAX_PERIOD_DAYS = 30
DEFAULT_PERIOD_HOURS = 24

_DATABASE_SCOPED_TYPES = {
    REPORT_TYPE_DAILY_DBA,
    REPORT_TYPE_PERFORMANCE,
    REPORT_TYPE_SQL_REGRESSION,
    REPORT_TYPE_CAPACITY,
    REPORT_TYPE_HEALTH,
}


class ReportService:
    """Orchestrates report generation: resolves and authorizes the scope (database or
    incident), validates the reporting period, delegates content construction to
    app/reports/builders.py, persists the result, and audits generation/export. SQL_REGRESSION
    (Enterprise Readiness Plan D.1) and CAPACITY (Enterprise Readiness Plan D.2) both return
    data_available=False only when this database has no telemetry of their kind yet - a
    data-availability fact, not a feature-doesn't-exist fact.
    """

    def __init__(
        self,
        *,
        report_repo: ReportRepository,
        database_repo: MonitoredDatabaseRepository,
        incident_repo: IncidentRepository,
        telemetry_repo: TelemetryRepository,
        collector_status_repo: CollectorStatusRepository,
        anomaly_repo: AnomalyRepository,
        notification_repo: NotificationEventRepository,
        sql_repo: SqlTelemetryRepository,
        capacity_repo: CapacityRepository,
        capability_repo: CapabilityRepository,
        statspack_repo: StatspackRepository,
        config_repo: ConfigTelemetryRepository,
        audit: AuditService,
    ):
        self.report_repo = report_repo
        self.database_repo = database_repo
        self.incident_repo = incident_repo
        self.telemetry_repo = telemetry_repo
        self.collector_status_repo = collector_status_repo
        self.anomaly_repo = anomaly_repo
        self.notification_repo = notification_repo
        self.sql_repo = sql_repo
        self.capacity_repo = capacity_repo
        self.capability_repo = capability_repo
        self.statspack_repo = statspack_repo
        self.config_repo = config_repo
        self.audit = audit

    async def generate(
        self,
        *,
        report_type: str,
        database_id: str | None,
        incident_id: str | None,
        period_start: datetime | None,
        period_end: datetime | None,
        rca_result: dict | None,
        actor_user_id: str,
        actor_email: str,
        ip_address: str | None,
    ) -> Report:
        if report_type not in REPORT_TYPES:
            raise ValidationAppError(f"Unknown report_type: {report_type}")

        database: MonitoredDatabase | None = None
        incident: Incident | None = None
        resolved_start: datetime | None = None
        resolved_end: datetime | None = None
        now = utcnow()

        if report_type == REPORT_TYPE_INCIDENT:
            if not incident_id:
                raise ValidationAppError("incident_id is required for an INCIDENT report.")
            incident = await self.incident_repo.get(incident_id)
            if incident is None:
                raise NotFoundError("Incident not found.")
            database = await self.database_repo.get(incident.monitored_database_id)
        elif report_type in _DATABASE_SCOPED_TYPES:
            if not database_id:
                raise ValidationAppError(f"database_id is required for a {report_type} report.")
            database = await self.database_repo.get(database_id)
            if database is None:
                raise NotFoundError("Database not found.")

            resolved_end = ensure_aware(period_end) if period_end else now
            resolved_start = ensure_aware(period_start) if period_start else resolved_end - timedelta(hours=DEFAULT_PERIOD_HOURS)
            if resolved_start >= resolved_end:
                raise ValidationAppError("period_start must be before period_end.")
            if resolved_end - resolved_start > timedelta(days=MAX_PERIOD_DAYS):
                raise ValidationAppError(f"Reporting period cannot exceed {MAX_PERIOD_DAYS} days.")

        built = await self._build(
            report_type, database=database, incident=incident, period_start=resolved_start,
            period_end=resolved_end, now=now, rca_result=rca_result,
        )

        report = await self.report_repo.create(
            report_type=report_type,
            requested_by_user_id=actor_user_id,
            requested_by_email=actor_email,
            monitored_database_id=database.id if database else None,
            database_name_snapshot=database.name if database else None,
            incident_id=incident.id if incident else None,
            incident_title_snapshot=incident.title if incident else None,
            period_start=resolved_start,
            period_end=resolved_end,
            timezone="UTC",
            data_available=built["data_available"],
            data_completeness_note=built["data_completeness_note"],
            limitations=built["limitations"],
            content=built["content"],
        )

        await self.audit.record(
            action="report.generated",
            success=True,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            target_type="report",
            target_id=report.id,
            ip_address=ip_address,
            metadata={
                "report_type": report_type,
                "database_id": database.id if database else None,
                "incident_id": incident.id if incident else None,
                "data_available": built["data_available"],
            },
        )
        return report

    async def _build(
        self,
        report_type: str,
        *,
        database: MonitoredDatabase | None,
        incident: Incident | None,
        period_start: datetime | None,
        period_end: datetime | None,
        now: datetime,
        rca_result: dict | None,
    ) -> dict:
        if report_type == REPORT_TYPE_DAILY_DBA:
            if database is None or period_start is None or period_end is None:
                raise RuntimeError("database/period must be resolved before building a DAILY_DBA report")
            return await builders.build_daily_dba_report(
                database=database,
                period_start=period_start,
                period_end=period_end,
                now=now,
                telemetry_repo=self.telemetry_repo,
                collector_status_repo=self.collector_status_repo,
                anomaly_repo=self.anomaly_repo,
                incident_repo=self.incident_repo,
                sql_repo=self.sql_repo,
                capacity_repo=self.capacity_repo,
                capability_repo=self.capability_repo,
                statspack_repo=self.statspack_repo,
                config_repo=self.config_repo,
            )
        if report_type == REPORT_TYPE_INCIDENT:
            if incident is None:
                raise RuntimeError("incident must be resolved before building an INCIDENT report")
            return await builders.build_incident_report(
                incident=incident,
                database=database,
                anomaly_repo=self.anomaly_repo,
                incident_repo=self.incident_repo,
                notification_repo=self.notification_repo,
                telemetry_repo=self.telemetry_repo,
                sql_repo=self.sql_repo,
                now=now,
                rca_result=rca_result,
            )
        if report_type == REPORT_TYPE_PERFORMANCE:
            if database is None or period_start is None or period_end is None:
                raise RuntimeError("database/period must be resolved before building a PERFORMANCE report")
            return await builders.build_performance_report(
                database=database,
                period_start=period_start,
                period_end=period_end,
                telemetry_repo=self.telemetry_repo,
                anomaly_repo=self.anomaly_repo,
                incident_repo=self.incident_repo,
            )
        if report_type == REPORT_TYPE_SQL_REGRESSION:
            if database is None:
                raise RuntimeError("database must be resolved before building a SQL_REGRESSION report")
            return await builders.build_sql_regression_report(database=database, sql_repo=self.sql_repo)
        if report_type == REPORT_TYPE_CAPACITY:
            if database is None:
                raise RuntimeError("database must be resolved before building a CAPACITY report")
            return await builders.build_capacity_report(database=database, capacity_repo=self.capacity_repo)
        if report_type == REPORT_TYPE_HEALTH:
            if database is None:
                raise RuntimeError("database must be resolved before building a HEALTH report")
            return await builders.build_health_report(
                database=database,
                now=now,
                telemetry_repo=self.telemetry_repo,
                collector_status_repo=self.collector_status_repo,
                anomaly_repo=self.anomaly_repo,
                sql_repo=self.sql_repo,
                capacity_repo=self.capacity_repo,
                config_repo=self.config_repo,
            )
        raise ValidationAppError(f"Unknown report_type: {report_type}")  # pragma: no cover - guarded above

    async def get(self, report_id: str) -> Report:
        report = await self.report_repo.get(report_id)
        if report is None:
            raise NotFoundError("Report not found.")
        return report

    async def list_reports(
        self,
        *,
        report_type: str | None = None,
        monitored_database_id: str | None = None,
        incident_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Report]:
        return await self.report_repo.list_all(
            report_type=report_type,
            monitored_database_id=monitored_database_id,
            incident_id=incident_id,
            limit=limit,
            offset=offset,
        )

    async def export(
        self, report_id: str, fmt: str, *, actor_user_id: str, actor_email: str, ip_address: str | None
    ) -> tuple[bytes, str, Report]:
        if fmt not in export.SUPPORTED_FORMATS:
            raise ValidationAppError(f"Unsupported export format: '{fmt}'. Supported: {', '.join(export.SUPPORTED_FORMATS)}.")
        report = await self.get(report_id)
        data = export.export_report(report, fmt)

        await self.audit.record(
            action="report.exported",
            success=True,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            target_type="report",
            target_id=report.id,
            ip_address=ip_address,
            metadata={"format": fmt, "report_type": report.report_type},
        )
        content_type = "text/csv" if fmt == "csv" else "application/pdf"
        return data, content_type, report
