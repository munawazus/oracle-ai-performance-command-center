from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.capacity_telemetry import CapacityAlertEvent, TablespaceSample
from app.oracle.tablespace_probe import TablespaceRow


class CapacityRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- writes ---------------------------------------------------------------------------

    async def insert_tablespace_samples(
        self, monitored_database_id: str, sampled_at: datetime, rows: list[TablespaceRow]
    ) -> None:
        self.db.add_all(
            [
                TablespaceSample(
                    monitored_database_id=monitored_database_id,
                    sampled_at=sampled_at,
                    tablespace_name=row.tablespace_name,
                    contents=row.contents,
                    status=row.status,
                    allocated_bytes=row.allocated_bytes,
                    used_bytes=row.used_bytes,
                    max_bytes=row.max_bytes,
                    file_count=row.file_count,
                )
                for row in rows
            ]
        )
        await self.db.flush()

    # -- reads ------------------------------------------------------------------------------

    async def latest_round(self, monitored_database_id: str) -> tuple[datetime, list[TablespaceSample]] | None:
        """The current-state overview needs only the single most recent round - unlike
        top-SQL's per-interval delta, %used is a snapshot fact (used/allocated), not a delta
        between cumulative counters, so one round is always enough."""
        ts_result = await self.db.execute(
            select(TablespaceSample.sampled_at)
            .where(TablespaceSample.monitored_database_id == monitored_database_id)
            .order_by(TablespaceSample.sampled_at.desc())
            .limit(1)
        )
        latest_ts = ts_result.scalar_one_or_none()
        if latest_ts is None:
            return None

        rows_result = await self.db.execute(
            select(TablespaceSample).where(
                TablespaceSample.monitored_database_id == monitored_database_id,
                TablespaceSample.sampled_at == latest_ts,
            )
        )
        return latest_ts, list(rows_result.scalars().all())

    async def samples_in_window(
        self, monitored_database_id: str, tablespace_name: str, start: datetime, end: datetime
    ) -> list[TablespaceSample]:
        """A real multi-point series, not a 2-round delta - growth-rate needs more than two
        points to be a meaningful trend, unlike SQL's top-sql ranking."""
        result = await self.db.execute(
            select(TablespaceSample)
            .where(
                TablespaceSample.monitored_database_id == monitored_database_id,
                TablespaceSample.tablespace_name == tablespace_name,
                TablespaceSample.sampled_at >= start,
                TablespaceSample.sampled_at < end,
            )
            .order_by(TablespaceSample.sampled_at)
        )
        return list(result.scalars().all())

    async def has_any_samples(self, monitored_database_id: str) -> bool:
        result = await self.db.execute(
            select(TablespaceSample.id).where(TablespaceSample.monitored_database_id == monitored_database_id).limit(1)
        )
        return result.first() is not None

    async def list_alerts(
        self, monitored_database_id: str, status: str | None = "OPEN", limit: int = 50
    ) -> list[CapacityAlertEvent]:
        query = select(CapacityAlertEvent).where(CapacityAlertEvent.monitored_database_id == monitored_database_id)
        if status is not None:
            query = query.where(CapacityAlertEvent.status == status)
        query = query.order_by(CapacityAlertEvent.last_detected_at.desc()).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_alerts_fleet(
        self, status: str | None = "OPEN", limit: int = 200, monitored_database_ids: list[str] | None = None
    ) -> list[CapacityAlertEvent]:
        """Cross-database counterpart to list_alerts (Activity tab Phase 2) - mirrors
        IncidentRepository.list_all's filter shape: `monitored_database_ids=None` means
        unfiltered, an empty list is a legitimate "matched no database" result."""
        query = select(CapacityAlertEvent).order_by(CapacityAlertEvent.last_detected_at.desc()).limit(limit)
        if status is not None:
            query = query.where(CapacityAlertEvent.status == status)
        if monitored_database_ids is not None:
            query = query.where(CapacityAlertEvent.monitored_database_id.in_(monitored_database_ids))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def find_open_alert(
        self, monitored_database_id: str, tablespace_name: str, alert_kind: str
    ) -> CapacityAlertEvent | None:
        result = await self.db.execute(
            select(CapacityAlertEvent).where(
                CapacityAlertEvent.monitored_database_id == monitored_database_id,
                CapacityAlertEvent.tablespace_name == tablespace_name,
                CapacityAlertEvent.alert_kind == alert_kind,
                CapacityAlertEvent.status == "OPEN",
            )
        )
        return result.scalar_one_or_none()

    async def create_alert(self, monitored_database_id: str, **fields) -> CapacityAlertEvent:
        event = CapacityAlertEvent(monitored_database_id=monitored_database_id, **fields)
        self.db.add(event)
        await self.db.flush()
        return event

    async def touch_alert(self, event: CapacityAlertEvent, **fields) -> CapacityAlertEvent:
        event.last_detected_at = utcnow()
        for key, value in fields.items():
            setattr(event, key, value)
        await self.db.flush()
        return event

    async def resolve_stale_alerts(self, monitored_database_id: str, older_than: datetime) -> int:
        result = await self.db.execute(
            select(CapacityAlertEvent).where(
                CapacityAlertEvent.monitored_database_id == monitored_database_id,
                CapacityAlertEvent.status == "OPEN",
                CapacityAlertEvent.last_detected_at < older_than,
            )
        )
        stale = list(result.scalars().all())
        now = utcnow()
        for event in stale:
            event.status = "RESOLVED"
            event.resolved_at = now
        if stale:
            await self.db.flush()
        return len(stale)
