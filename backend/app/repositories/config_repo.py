from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_telemetry import ConfigParameterSample
from app.oracle.config_probe import ParameterRow


class ConfigTelemetryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- writes ---------------------------------------------------------------------------

    async def insert_parameter_samples(
        self, monitored_database_id: str, sampled_at: datetime, rows: list[ParameterRow]
    ) -> None:
        self.db.add_all(
            [
                ConfigParameterSample(
                    monitored_database_id=monitored_database_id,
                    sampled_at=sampled_at,
                    parameter_name=row.parameter_name,
                    parameter_value=row.parameter_value,
                )
                for row in rows
            ]
        )
        await self.db.flush()

    # -- reads ------------------------------------------------------------------------------

    async def latest_two_rounds(self, monitored_database_id: str):
        """Direct analogue of SqlTelemetryRepository.latest_two_sql_rounds: [(sampled_at,
        rows), (sampled_at, rows)] oldest first, or None if fewer than two rounds exist."""
        ts_result = await self.db.execute(
            select(ConfigParameterSample.sampled_at)
            .where(ConfigParameterSample.monitored_database_id == monitored_database_id)
            .distinct()
            .order_by(ConfigParameterSample.sampled_at.desc())
            .limit(2)
        )
        timestamps = [row[0] for row in ts_result.all()]
        if len(timestamps) < 2:
            return None
        timestamps.sort()

        rounds = []
        for ts in timestamps:
            rows_result = await self.db.execute(
                select(ConfigParameterSample).where(
                    ConfigParameterSample.monitored_database_id == monitored_database_id,
                    ConfigParameterSample.sampled_at == ts,
                )
            )
            rounds.append((ts, list(rows_result.scalars().all())))
        return rounds

    async def recent_rounds(
        self, monitored_database_id: str, *, since: datetime, until: datetime | None, limit: int
    ) -> list[tuple[datetime, list[ConfigParameterSample]]]:
        """Every distinct collection round's timestamp in the window (oldest first, capped at
        the `limit` most recent if the window holds more), each paired with its full row set -
        same shape as latest_two_rounds, generalized to a real window instead of hardcoding 2.
        No even-sampling here unlike the 15s-tick telemetry repos: this collector runs hourly
        by default, so even a 30-day window holds a few hundred rounds at most - the silent-
        truncation risk that even-sampling exists for doesn't apply at this frequency."""
        query = (
            select(ConfigParameterSample.sampled_at)
            .where(
                ConfigParameterSample.monitored_database_id == monitored_database_id,
                ConfigParameterSample.sampled_at >= since,
            )
            .distinct()
            .order_by(ConfigParameterSample.sampled_at.desc())
            .limit(limit)
        )
        if until is not None:
            query = query.where(ConfigParameterSample.sampled_at <= until)
        ts_result = await self.db.execute(query)
        timestamps = [row[0] for row in ts_result.all()]
        timestamps.sort()

        rounds = []
        for ts in timestamps:
            rows_result = await self.db.execute(
                select(ConfigParameterSample).where(
                    ConfigParameterSample.monitored_database_id == monitored_database_id,
                    ConfigParameterSample.sampled_at == ts,
                )
            )
            rounds.append((ts, list(rows_result.scalars().all())))
        return rounds

    async def has_any_samples(self, monitored_database_id: str) -> bool:
        result = await self.db.execute(
            select(ConfigParameterSample.id)
            .where(ConfigParameterSample.monitored_database_id == monitored_database_id)
            .limit(1)
        )
        return result.first() is not None
