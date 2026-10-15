from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import Anomaly, MetricBaseline


class BaselineRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, monitored_database_id: str, metric_key: str, hour_of_day: int) -> MetricBaseline | None:
        result = await self.db.execute(
            select(MetricBaseline).where(
                MetricBaseline.monitored_database_id == monitored_database_id,
                MetricBaseline.metric_key == metric_key,
                MetricBaseline.hour_of_day == hour_of_day,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        monitored_database_id: str,
        metric_key: str,
        hour_of_day: int,
        sample_count: int,
        mean: float,
        m2: float,
    ) -> MetricBaseline:
        existing = await self.get(monitored_database_id, metric_key, hour_of_day)
        if existing is None:
            existing = MetricBaseline(
                monitored_database_id=monitored_database_id, metric_key=metric_key, hour_of_day=hour_of_day
            )
            self.db.add(existing)

        existing.sample_count = sample_count
        existing.mean = mean
        existing.m2 = m2
        await self.db.flush()
        return existing

    async def list_for_database(self, monitored_database_id: str) -> list[MetricBaseline]:
        result = await self.db.execute(
            select(MetricBaseline).where(MetricBaseline.monitored_database_id == monitored_database_id)
        )
        return list(result.scalars().all())


class AnomalyRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        monitored_database_id: str,
        metric_key: str,
        observed_value: float,
        baseline_mean: float,
        baseline_stddev: float,
        sample_count: int,
        severity: str,
    ) -> Anomaly:
        anomaly = Anomaly(
            monitored_database_id=monitored_database_id,
            metric_key=metric_key,
            observed_value=observed_value,
            baseline_mean=baseline_mean,
            baseline_stddev=baseline_stddev,
            sample_count=sample_count,
            severity=severity,
        )
        self.db.add(anomaly)
        await self.db.flush()
        return anomaly

    async def list_recent(
        self, monitored_database_id: str, limit: int = 100, since=None, until=None
    ) -> list[Anomaly]:
        query = (
            select(Anomaly)
            .where(Anomaly.monitored_database_id == monitored_database_id)
            .order_by(Anomaly.detected_at.desc())
            .limit(limit)
        )
        if since is not None:
            query = query.where(Anomaly.detected_at >= since)
        if until is not None:
            query = query.where(Anomaly.detected_at <= until)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_for_incident(self, incident_id: str) -> list[Anomaly]:
        result = await self.db.execute(
            select(Anomaly).where(Anomaly.incident_id == incident_id).order_by(Anomaly.detected_at)
        )
        return list(result.scalars().all())

    async def list_recent_fleet(
        self, limit: int = 200, since=None, until=None, monitored_database_ids: list[str] | None = None
    ) -> list[Anomaly]:
        """Cross-database counterpart to list_recent (Activity tab Phase 2) - mirrors
        IncidentRepository.list_all's filter shape: `monitored_database_ids=None` means
        unfiltered (every active database), an empty list is a legitimate "matched no
        database" result, not "no filter"."""
        query = select(Anomaly).order_by(Anomaly.detected_at.desc()).limit(limit)
        if since is not None:
            query = query.where(Anomaly.detected_at >= since)
        if until is not None:
            query = query.where(Anomaly.detected_at <= until)
        if monitored_database_ids is not None:
            query = query.where(Anomaly.monitored_database_id.in_(monitored_database_ids))
        result = await self.db.execute(query)
        return list(result.scalars().all())
