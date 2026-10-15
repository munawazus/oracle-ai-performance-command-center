from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]

from app.config import get_settings
from app.db import SessionLocal
from app.logging_config import get_logger
from app.oracle.connector import connect
from app.repositories.audit_repo import AuditRepository
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.integration_repo import IntegrationDeliveryRepository, IntegrationRepository
from app.repositories.notification_repo import NotificationEventRepository, NotificationSettingsRepository
from app.repositories.secret_repo import SecretRepository
from app.services.analytics_service import AnalyticsService
from app.services.audit_service import AuditService
from app.services.capacity_alert_service import CapacityAlertService
from app.services.collector_service import CollectorService
from app.services.integration_service import IntegrationService
from app.services.notification_service import NotificationService
from app.services.secret_service import LocalEncryptedSecretProvider
from app.services.sql_regression_service import SqlRegressionService

logger = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _process_pending_notifications() -> None:
    settings = get_settings()
    async with SessionLocal() as db:
        service = NotificationService(
            NotificationSettingsRepository(db),
            NotificationEventRepository(db),
            MonitoredDatabaseRepository(db),
            IncidentRepository(db),
            LocalEncryptedSecretProvider(SecretRepository(db)),
            dashboard_base_url=settings.frontend_base_url,
        )
        try:
            await service.process_pending()
        finally:
            await db.commit()


async def _process_pending_integrations() -> None:
    settings = get_settings()
    async with SessionLocal() as db:
        service = IntegrationService(
            IntegrationRepository(db),
            IntegrationDeliveryRepository(db),
            MonitoredDatabaseRepository(db),
            LocalEncryptedSecretProvider(SecretRepository(db)),
            AuditService(AuditRepository(db)),
            dashboard_base_url=settings.frontend_base_url,
        )
        try:
            await service.process_pending()
        finally:
            await db.commit()


def start_collector_scheduler() -> AsyncIOScheduler | None:
    """No-op if COLLECTOR_ENABLED=false (set by the test suite, and available as an
    operator off-switch) - never starts a real Oracle-polling loop against test doubles
    or in an environment that hasn't opted in. Runs six jobs: the Oracle collector and
    the analytics engine on the same interval (analytics lags by up to one interval, which
    is fine - it reads the last two completed rounds, not "this instant"), the
    notification/integration queue processors on their own, faster interval, since
    HIGH/CRITICAL delivery latency matters more than telemetry polling latency, the SQL
    collector + SQL regression detector (master plan Phase 9, Enterprise Readiness Plan D.1)
    on their own, slower interval - V$SQL/V$SQL_PLAN queries are the heaviest this platform
    issues, and comparing 24h of SQL history is pointless more often than new SQL telemetry
    actually arrives - the capacity collector + capacity alert detector (master plan
    Phase 5, Enterprise Readiness Plan D.2) on an even slower interval, since tablespace
    growth is an hours-to-days signal - the Statspack sync (master plan Phase 11, Enterprise
    Readiness Plan D.3) on the slowest interval of all, since Statspack snapshots are taken on
    the DBA's own irregular schedule, not this platform's - and the Change Intelligence
    collector (master plan Phase 14, Enterprise Readiness Plan D.4) on the same tier as
    capacity, since configuration drift is also an hours-to-days signal. SQL_COLLECTOR_ENABLED/
    CAPACITY_COLLECTOR_ENABLED/STATSPACK_COLLECTOR_ENABLED/CONFIG_COLLECTOR_ENABLED are
    independent off-switches on top of COLLECTOR_ENABLED, so an operator can pause just one
    collector."""
    global _scheduler
    settings = get_settings()
    if not settings.collector_enabled:
        logger.info("collector_scheduler_disabled")
        return None

    collector = CollectorService(connect)
    analytics = AnalyticsService()
    sql_regression = SqlRegressionService()
    capacity_alerts = CapacityAlertService()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        collector.run_once_all_active,
        "interval",
        seconds=settings.collector_interval_seconds,
        id="oracle_collector",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        analytics.run_once_all_active,
        "interval",
        seconds=settings.collector_interval_seconds,
        id="analytics_engine",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _process_pending_notifications,
        "interval",
        seconds=settings.notification_interval_seconds,
        id="notification_processor",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _process_pending_integrations,
        "interval",
        seconds=settings.notification_interval_seconds,
        id="integration_processor",
        max_instances=1,
        coalesce=True,
    )
    if settings.sql_collector_enabled:
        scheduler.add_job(
            collector.run_sql_once_all_active,
            "interval",
            seconds=settings.sql_collector_interval_seconds,
            id="sql_collector",
            max_instances=1,
            coalesce=True,
            kwargs={"top_n": settings.sql_top_n, "capture_text": settings.sql_capture_text},
        )
        scheduler.add_job(
            sql_regression.run_once_all_active,
            "interval",
            seconds=settings.sql_collector_interval_seconds,
            id="sql_regression_detector",
            max_instances=1,
            coalesce=True,
        )
    if settings.capacity_collector_enabled:
        scheduler.add_job(
            collector.run_capacity_once_all_active,
            "interval",
            seconds=settings.capacity_collector_interval_seconds,
            id="capacity_collector",
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            capacity_alerts.run_once_all_active,
            "interval",
            seconds=settings.capacity_collector_interval_seconds,
            id="capacity_alert_detector",
            max_instances=1,
            coalesce=True,
        )
    if settings.statspack_collector_enabled:
        scheduler.add_job(
            collector.run_statspack_once_all_active,
            "interval",
            seconds=settings.statspack_collector_interval_seconds,
            id="statspack_collector",
            max_instances=1,
            coalesce=True,
            kwargs={"max_snapshots": settings.statspack_max_snapshots_per_sync},
        )
    if settings.config_collector_enabled:
        scheduler.add_job(
            collector.run_config_once_all_active,
            "interval",
            seconds=settings.config_collector_interval_seconds,
            id="config_collector",
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "collector_scheduler_started",
        interval_seconds=settings.collector_interval_seconds,
        notification_interval_seconds=settings.notification_interval_seconds,
        sql_collector_enabled=settings.sql_collector_enabled,
        sql_collector_interval_seconds=settings.sql_collector_interval_seconds,
        capacity_collector_enabled=settings.capacity_collector_enabled,
        capacity_collector_interval_seconds=settings.capacity_collector_interval_seconds,
        statspack_collector_enabled=settings.statspack_collector_enabled,
        statspack_collector_interval_seconds=settings.statspack_collector_interval_seconds,
        config_collector_enabled=settings.config_collector_enabled,
        config_collector_interval_seconds=settings.config_collector_interval_seconds,
    )
    return scheduler


def stop_collector_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
