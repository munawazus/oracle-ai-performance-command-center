"""Deterministic analytics: turns the raw telemetry the collector wrote (Phase 3) into
current metric values, folds each into its rolling per-hour-of-day baseline, and flags
anomalies before the observation is folded in (so an anomalous point never dampens the very
baseline it's being judged against).
"""

from app.analytics.baseline_engine import EMPTY_STATS, BaselineStats, classify_severity, welford_update, z_score
from app.analytics.metrics import aggregate_by_wait_class, compute_wait_class_rates
from app.config import get_settings
from app.db import SessionLocal
from app.logging_config import get_logger
from app.models.base import utcnow
from app.repositories.analytics_repo import AnomalyRepository, BaselineRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.integration_repo import IntegrationDeliveryRepository, IntegrationRepository
from app.repositories.notification_repo import NotificationEventRepository, NotificationSettingsRepository
from app.repositories.secret_repo import SecretRepository
from app.repositories.telemetry_repo import TelemetryRepository
from app.services.audit_service import AuditService
from app.services.incident_service import IncidentService
from app.services.integration_service import IntegrationService
from app.services.notification_service import NotificationService
from app.services.secret_service import LocalEncryptedSecretProvider

logger = get_logger(__name__)


class AnalyticsService:
    async def run_once_for_database(self, database_id: str) -> None:
        async with SessionLocal() as db:
            telemetry_repo = TelemetryRepository(db)
            baseline_repo = BaselineRepository(db)
            anomaly_repo = AnomalyRepository(db)
            incident_repo = IncidentRepository(db)
            incident_service = IncidentService(incident_repo)
            notification_service = NotificationService(
                NotificationSettingsRepository(db),
                NotificationEventRepository(db),
                MonitoredDatabaseRepository(db),
                incident_repo,
                LocalEncryptedSecretProvider(SecretRepository(db)),
                dashboard_base_url=get_settings().frontend_base_url,
            )
            integration_service = IntegrationService(
                IntegrationRepository(db),
                IntegrationDeliveryRepository(db),
                MonitoredDatabaseRepository(db),
                LocalEncryptedSecretProvider(SecretRepository(db)),
                AuditService(AuditRepository(db)),
                dashboard_base_url=get_settings().frontend_base_url,
            )

            # Checked before metrics: a reachability failure means zero telemetry can even be
            # sampled, so compute_current_metrics would return {} and this whole function
            # would silently no-op otherwise - the one gap that let a database go down with
            # no anomaly, no incident, and no notification ever firing (only a passive
            # "Failing (N)" collector-status badge nobody's watching).
            latest_health = await telemetry_repo.latest_instance_health(database_id)
            if latest_health is not None and latest_health.reachability != "up":
                incident = await incident_service.open_or_attach_reachability_incident(
                    database_id, category=latest_health.reachability, detected_at=latest_health.sampled_at
                )
                await notification_service.notify_if_threshold_reached(incident)
                await integration_service.notify_if_threshold_reached(incident)
                logger.warning(
                    "reachability_incident", database_id=database_id, category=latest_health.reachability
                )
                await db.commit()
                return

            metrics = await self.compute_current_metrics(telemetry_repo, database_id)
            if not metrics:
                await db.commit()
                return

            hour = utcnow().hour
            for metric_key, observed_value in metrics.items():
                existing = await baseline_repo.get(database_id, metric_key, hour)
                stats = (
                    BaselineStats(existing.sample_count, existing.mean, existing.m2) if existing else EMPTY_STATS
                )

                z = z_score(stats, observed_value)
                if z is not None:
                    severity = classify_severity(z)
                    if severity is not None:
                        anomaly = await anomaly_repo.create(
                            monitored_database_id=database_id,
                            metric_key=metric_key,
                            observed_value=observed_value,
                            baseline_mean=stats.mean,
                            baseline_stddev=stats.stddev,
                            sample_count=stats.sample_count,
                            severity=severity,
                        )
                        incident = await incident_service.correlate_anomaly(anomaly)
                        await notification_service.notify_if_threshold_reached(incident)
                        await integration_service.notify_if_threshold_reached(incident)
                        logger.warning(
                            "anomaly_detected",
                            database_id=database_id,
                            metric_key=metric_key,
                            severity=severity,
                            z_score=round(z, 2),
                        )

                updated = welford_update(stats, observed_value)
                await baseline_repo.upsert(
                    database_id, metric_key, hour, updated.sample_count, updated.mean, updated.m2
                )

            await db.commit()

    async def run_once_all_active(self) -> None:
        async with SessionLocal() as db:
            db_repo = MonitoredDatabaseRepository(db)
            database_ids = await db_repo.list_active_ids()

        for database_id in database_ids:
            try:
                await self.run_once_for_database(database_id)
            except Exception:
                logger.error("analytics_run_crashed", database_id=database_id)

    async def compute_current_metrics(
        self, telemetry_repo: TelemetryRepository, database_id: str
    ) -> dict[str, float]:
        """Public - also called directly by the Compare Mode route
        (app/api/routes/databases.py) to get a live value for one metric_key, outside the
        anomaly-detection loop this method was originally written for."""
        metrics: dict[str, float] = {}

        health = await telemetry_repo.latest_instance_health(database_id)
        if health is not None and health.active_session_count is not None:
            metrics["active_session_count"] = float(health.active_session_count)

        rounds = await telemetry_repo.latest_two_wait_rounds(database_id)
        if rounds is not None:
            (t1, rows1), (t2, rows2) = rounds
            elapsed_seconds = (t2 - t1).total_seconds()
            previous = aggregate_by_wait_class(rows1)
            current = aggregate_by_wait_class(rows2)
            for wait_class, rate in compute_wait_class_rates(previous, current, elapsed_seconds).items():
                metrics[f"wait_rate_centisec_per_sec:{wait_class}"] = rate

        # Database CPU usage rate (Finding 7, docs/QA_LIVE_FINDINGS.md) - same
        # delta-of-cumulative-counter shape as the wait rates above, over V$SYSSTAT instead of
        # V$SYSTEM_EVENT.
        health_points = await telemetry_repo.latest_two_instance_health(database_id)
        if health_points is not None:
            current_health, previous_health = health_points
            if current_health.cpu_centisecs_used is not None and previous_health.cpu_centisecs_used is not None:
                elapsed_seconds = (current_health.sampled_at - previous_health.sampled_at).total_seconds()
                if elapsed_seconds > 0:
                    delta = current_health.cpu_centisecs_used - previous_health.cpu_centisecs_used
                    if delta >= 0:
                        metrics["cpu_centisec_used_per_sec"] = delta / elapsed_seconds

        return metrics
