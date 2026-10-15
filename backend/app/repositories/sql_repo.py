from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.sql_telemetry import SqlPlanSnapshot, SqlRegressionEvent, SqlStatSample, SqlStatement
from app.oracle.sql_probe import SqlPlanRow, SqlStatRow


class SqlTelemetryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- writes ---------------------------------------------------------------------------

    async def upsert_statements(
        self, monitored_database_id: str, sampled_at: datetime, rows: list[SqlStatRow], *, capture_text: bool = True
    ) -> None:
        """capture_text=False (SQL_CAPTURE_TEXT=false) still tracks/ranks/regression-detects
        every statement identically - only sql_text itself is never written, for an operator
        who cannot retain query text at all."""
        if not rows:
            return
        sql_ids = [row.sql_id for row in rows]
        existing_result = await self.db.execute(
            select(SqlStatement).where(
                SqlStatement.monitored_database_id == monitored_database_id,
                SqlStatement.sql_id.in_(sql_ids),
            )
        )
        existing_by_id = {s.sql_id: s for s in existing_result.scalars().all()}

        for row in rows:
            existing = existing_by_id.get(row.sql_id)
            sql_text = row.sql_text if capture_text else None
            truncated = len(sql_text) >= 1000 if sql_text else False
            if existing:
                existing.last_seen_at = sampled_at
                if sql_text and not existing.sql_text:
                    existing.sql_text = sql_text
                    existing.sql_text_truncated = truncated
                if row.parsing_schema_name and not existing.parsing_schema_name:
                    existing.parsing_schema_name = row.parsing_schema_name
            else:
                self.db.add(
                    SqlStatement(
                        monitored_database_id=monitored_database_id,
                        sql_id=row.sql_id,
                        sql_text=sql_text,
                        sql_text_truncated=truncated,
                        parsing_schema_name=row.parsing_schema_name,
                        first_seen_at=sampled_at,
                        last_seen_at=sampled_at,
                    )
                )
        await self.db.flush()

    async def insert_stat_samples(self, monitored_database_id: str, sampled_at: datetime, rows: list[SqlStatRow]) -> None:
        self.db.add_all(
            [
                SqlStatSample(
                    monitored_database_id=monitored_database_id,
                    sampled_at=sampled_at,
                    sql_id=row.sql_id,
                    plan_hash_value=row.plan_hash_value,
                    child_cursor_count=row.child_cursor_count,
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

    async def upsert_plan_snapshots(
        self, monitored_database_id: str, captured_at: datetime, plans: list[SqlPlanRow]
    ) -> int:
        """Capture-on-change: a plan hash's body is immutable, so an already-known
        (sql_id, plan_hash_value) only gets its last_seen_at touched. Returns the count of
        genuinely new plans captured this round - a useful log/verification signal."""
        if not plans:
            return 0
        keys = [(p.sql_id, p.plan_hash_value) for p in plans]
        existing_result = await self.db.execute(
            select(SqlPlanSnapshot).where(
                SqlPlanSnapshot.monitored_database_id == monitored_database_id,
                SqlPlanSnapshot.sql_id.in_({k[0] for k in keys}),
            )
        )
        existing_by_key = {(s.sql_id, s.plan_hash_value): s for s in existing_result.scalars().all()}

        new_count = 0
        for plan in plans:
            key = (plan.sql_id, plan.plan_hash_value)
            existing = existing_by_key.get(key)
            if existing:
                existing.last_seen_at = captured_at
                continue
            new_count += 1
            self.db.add(
                SqlPlanSnapshot(
                    monitored_database_id=monitored_database_id,
                    sql_id=plan.sql_id,
                    plan_hash_value=plan.plan_hash_value,
                    first_seen_at=captured_at,
                    last_seen_at=captured_at,
                    plan_line_count=len(plan.lines),
                    plan_lines=[
                        {
                            "id": line.id,
                            "parent_id": line.parent_id,
                            "depth": line.depth,
                            "position": line.position,
                            "operation": line.operation,
                            "options": line.options,
                            "object_owner": line.object_owner,
                            "object_name": line.object_name,
                            "object_type": line.object_type,
                            "cost": line.cost,
                            "cardinality": line.cardinality,
                            "bytes": line.bytes,
                        }
                        for line in plan.lines
                    ],
                )
            )
        await self.db.flush()
        return new_count

    # -- reads ------------------------------------------------------------------------------

    async def latest_two_sql_rounds(self, monitored_database_id: str):
        """Direct analogue of TelemetryRepository.latest_two_wait_rounds: [(sampled_at,
        rows), (sampled_at, rows)] oldest first, or None if fewer than two rounds exist."""
        ts_result = await self.db.execute(
            select(SqlStatSample.sampled_at)
            .where(SqlStatSample.monitored_database_id == monitored_database_id)
            .distinct()
            .order_by(SqlStatSample.sampled_at.desc())
            .limit(2)
        )
        timestamps = [row[0] for row in ts_result.all()]
        if len(timestamps) < 2:
            return None
        timestamps.sort()

        rounds = []
        for ts in timestamps:
            rows_result = await self.db.execute(
                select(SqlStatSample).where(
                    SqlStatSample.monitored_database_id == monitored_database_id,
                    SqlStatSample.sampled_at == ts,
                )
            )
            rounds.append((ts, list(rows_result.scalars().all())))
        return rounds

    async def latest_stat_sample(self, monitored_database_id: str, sql_id: str) -> SqlStatSample | None:
        """The current plan hash for a statement needs only the single most recent round,
        not a two-round diff (unlike top-sql's ranking, which needs a delta) - a database
        with only one collection round so far still has a real "current plan"."""
        result = await self.db.execute(
            select(SqlStatSample)
            .where(SqlStatSample.monitored_database_id == monitored_database_id, SqlStatSample.sql_id == sql_id)
            .order_by(SqlStatSample.sampled_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def stat_samples_in_window(
        self, monitored_database_id: str, start: datetime, end: datetime, sql_id: str | None = None
    ) -> list[SqlStatSample]:
        query = select(SqlStatSample).where(
            SqlStatSample.monitored_database_id == monitored_database_id,
            SqlStatSample.sampled_at >= start,
            SqlStatSample.sampled_at < end,
        )
        if sql_id is not None:
            query = query.where(SqlStatSample.sql_id == sql_id)
        result = await self.db.execute(query.order_by(SqlStatSample.sampled_at))
        return list(result.scalars().all())

    async def has_any_stat_samples(self, monitored_database_id: str) -> bool:
        result = await self.db.execute(
            select(SqlStatSample.id).where(SqlStatSample.monitored_database_id == monitored_database_id).limit(1)
        )
        return result.first() is not None

    async def get_statement(self, monitored_database_id: str, sql_id: str) -> SqlStatement | None:
        result = await self.db.execute(
            select(SqlStatement).where(
                SqlStatement.monitored_database_id == monitored_database_id, SqlStatement.sql_id == sql_id
            )
        )
        return result.scalar_one_or_none()

    async def statements_for_sql_ids(self, monitored_database_id: str, sql_ids: list[str]) -> dict[str, SqlStatement]:
        if not sql_ids:
            return {}
        result = await self.db.execute(
            select(SqlStatement).where(
                SqlStatement.monitored_database_id == monitored_database_id, SqlStatement.sql_id.in_(sql_ids)
            )
        )
        return {s.sql_id: s for s in result.scalars().all()}

    async def plan_counts_for_sql_ids(self, monitored_database_id: str, sql_ids: list[str]) -> dict[str, int]:
        if not sql_ids:
            return {}
        result = await self.db.execute(
            select(SqlPlanSnapshot.sql_id, func.count())
            .where(SqlPlanSnapshot.monitored_database_id == monitored_database_id, SqlPlanSnapshot.sql_id.in_(sql_ids))
            .group_by(SqlPlanSnapshot.sql_id)
        )
        return {row[0]: row[1] for row in result.all()}

    async def list_plan_snapshots(self, monitored_database_id: str, sql_id: str) -> list[SqlPlanSnapshot]:
        result = await self.db.execute(
            select(SqlPlanSnapshot)
            .where(SqlPlanSnapshot.monitored_database_id == monitored_database_id, SqlPlanSnapshot.sql_id == sql_id)
            .order_by(SqlPlanSnapshot.first_seen_at)
        )
        return list(result.scalars().all())

    async def get_plan_snapshot(
        self, monitored_database_id: str, sql_id: str, plan_hash_value: int
    ) -> SqlPlanSnapshot | None:
        result = await self.db.execute(
            select(SqlPlanSnapshot).where(
                SqlPlanSnapshot.monitored_database_id == monitored_database_id,
                SqlPlanSnapshot.sql_id == sql_id,
                SqlPlanSnapshot.plan_hash_value == plan_hash_value,
            )
        )
        return result.scalar_one_or_none()

    async def list_regressions(
        self, monitored_database_id: str, status: str | None = "OPEN", limit: int = 50
    ) -> list[SqlRegressionEvent]:
        query = select(SqlRegressionEvent).where(SqlRegressionEvent.monitored_database_id == monitored_database_id)
        if status is not None:
            query = query.where(SqlRegressionEvent.status == status)
        query = query.order_by(SqlRegressionEvent.last_detected_at.desc()).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_regressions_fleet(
        self, status: str | None = "OPEN", limit: int = 200, monitored_database_ids: list[str] | None = None
    ) -> list[SqlRegressionEvent]:
        """Cross-database counterpart to list_regressions (Activity tab Phase 2) - mirrors
        IncidentRepository.list_all's filter shape: `monitored_database_ids=None` means
        unfiltered, an empty list is a legitimate "matched no database" result."""
        query = select(SqlRegressionEvent).order_by(SqlRegressionEvent.last_detected_at.desc()).limit(limit)
        if status is not None:
            query = query.where(SqlRegressionEvent.status == status)
        if monitored_database_ids is not None:
            query = query.where(SqlRegressionEvent.monitored_database_id.in_(monitored_database_ids))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def find_open_regression(
        self, monitored_database_id: str, sql_id: str, regression_kind: str
    ) -> SqlRegressionEvent | None:
        result = await self.db.execute(
            select(SqlRegressionEvent).where(
                SqlRegressionEvent.monitored_database_id == monitored_database_id,
                SqlRegressionEvent.sql_id == sql_id,
                SqlRegressionEvent.regression_kind == regression_kind,
                SqlRegressionEvent.status == "OPEN",
            )
        )
        return result.scalar_one_or_none()

    async def create_regression(self, monitored_database_id: str, **fields) -> SqlRegressionEvent:
        event = SqlRegressionEvent(monitored_database_id=monitored_database_id, **fields)
        self.db.add(event)
        await self.db.flush()
        return event

    async def touch_regression(self, event: SqlRegressionEvent, **fields) -> SqlRegressionEvent:
        event.last_detected_at = utcnow()
        for key, value in fields.items():
            setattr(event, key, value)
        await self.db.flush()
        return event

    async def resolve_stale_regressions(self, monitored_database_id: str, older_than: datetime) -> int:
        result = await self.db.execute(
            select(SqlRegressionEvent).where(
                SqlRegressionEvent.monitored_database_id == monitored_database_id,
                SqlRegressionEvent.status == "OPEN",
                SqlRegressionEvent.last_detected_at < older_than,
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
