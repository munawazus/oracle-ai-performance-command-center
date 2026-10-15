from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.statspack_telemetry import (
    StatspackSnapshot,
    StatspackSqlSample,
    StatspackSyncState,
    StatspackWaitEventSample,
)
from app.oracle.statspack_probe import SnapshotRow, SqlRow, WaitEventRow


class StatspackRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- sync state -------------------------------------------------------------------------

    async def get_sync_state(self, monitored_database_id: str) -> StatspackSyncState | None:
        result = await self.db.execute(
            select(StatspackSyncState).where(StatspackSyncState.monitored_database_id == monitored_database_id)
        )
        return result.scalar_one_or_none()

    async def upsert_sync_state(
        self, monitored_database_id: str, last_synced_snap_id: int, last_synced_snap_time: datetime
    ) -> StatspackSyncState:
        existing = await self.get_sync_state(monitored_database_id)
        if existing:
            existing.last_synced_snap_id = last_synced_snap_id
            existing.last_synced_snap_time = last_synced_snap_time
        else:
            existing = StatspackSyncState(
                monitored_database_id=monitored_database_id,
                last_synced_snap_id=last_synced_snap_id,
                last_synced_snap_time=last_synced_snap_time,
            )
            self.db.add(existing)
        await self.db.flush()
        return existing

    # -- writes -----------------------------------------------------------------------------

    async def insert_snapshots(
        self, monitored_database_id: str, synced_at: datetime, rows: list[SnapshotRow]
    ) -> None:
        """Capture-on-sync: a snap_id is immutable once taken by the DBA's own
        statspack.snap job, so this only ever inserts snapshots not already synced - the
        caller (the collector) is expected to pass only genuinely-new rows (from
        sample_new_snapshots, which already filters on the stored high-water-mark), so no
        existing-row check is needed here unlike SqlPlanSnapshot's capture-on-change."""
        if not rows:
            return
        self.db.add_all(
            [
                StatspackSnapshot(
                    monitored_database_id=monitored_database_id,
                    snap_id=row.snap_id,
                    snap_time=row.snap_time,
                    synced_at=synced_at,
                )
                for row in rows
            ]
        )
        await self.db.flush()

    async def insert_wait_event_samples(self, monitored_database_id: str, rows: list[WaitEventRow]) -> None:
        if not rows:
            return
        self.db.add_all(
            [
                StatspackWaitEventSample(
                    monitored_database_id=monitored_database_id,
                    snap_id=row.snap_id,
                    event_name=row.event_name,
                    wait_class=row.wait_class,
                    total_waits=row.total_waits,
                    time_waited_centisecs=row.time_waited_centisecs,
                )
                for row in rows
            ]
        )
        await self.db.flush()

    async def insert_sql_samples(self, monitored_database_id: str, rows: list[SqlRow]) -> None:
        if not rows:
            return
        self.db.add_all(
            [
                StatspackSqlSample(
                    monitored_database_id=monitored_database_id,
                    snap_id=row.snap_id,
                    sql_id=row.sql_id,
                    plan_hash_value=row.plan_hash_value,
                    executions=row.executions,
                    elapsed_time_us=row.elapsed_time_us,
                    cpu_time_us=row.cpu_time_us,
                    buffer_gets=row.buffer_gets,
                    disk_reads=row.disk_reads,
                    rows_processed=row.rows_processed,
                    parse_calls=row.parse_calls,
                )
                for row in rows
            ]
        )
        await self.db.flush()

    # -- reads ------------------------------------------------------------------------------

    async def has_any_snapshots(self, monitored_database_id: str) -> bool:
        result = await self.db.execute(
            select(StatspackSnapshot.id).where(StatspackSnapshot.monitored_database_id == monitored_database_id).limit(1)
        )
        return result.first() is not None

    async def latest_two_synced_snapshots(
        self, monitored_database_id: str
    ) -> tuple[StatspackSnapshot, StatspackSnapshot] | None:
        """The two most recently synced snapshots (oldest first) - the "rounds" a Statspack
        delta is computed between. None if fewer than two have ever been synced."""
        result = await self.db.execute(
            select(StatspackSnapshot)
            .where(StatspackSnapshot.monitored_database_id == monitored_database_id)
            .order_by(StatspackSnapshot.snap_id.desc())
            .limit(2)
        )
        snapshots = list(result.scalars().all())
        if len(snapshots) < 2:
            return None
        snapshots.sort(key=lambda s: s.snap_id)
        return snapshots[0], snapshots[1]

    async def wait_event_samples_for_snapshot(
        self, monitored_database_id: str, snap_id: int
    ) -> list[StatspackWaitEventSample]:
        result = await self.db.execute(
            select(StatspackWaitEventSample).where(
                StatspackWaitEventSample.monitored_database_id == monitored_database_id,
                StatspackWaitEventSample.snap_id == snap_id,
            )
        )
        return list(result.scalars().all())

    async def sql_samples_for_snapshot(self, monitored_database_id: str, snap_id: int) -> list[StatspackSqlSample]:
        result = await self.db.execute(
            select(StatspackSqlSample).where(
                StatspackSqlSample.monitored_database_id == monitored_database_id,
                StatspackSqlSample.snap_id == snap_id,
            )
        )
        return list(result.scalars().all())
