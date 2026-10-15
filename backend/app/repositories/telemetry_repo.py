from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.telemetry import CollectorStatus, InstanceHealthSnapshot, SessionSample, WaitSnapshot
from app.oracle.telemetry_probe import InstanceHealthSample, SessionSampleRow, WaitSnapshotRow

# A safety ceiling on how many rows/rounds a single history query fetches before even-sampling
# down to `limit` - generous enough to comfortably cover the longest range the UI offers as a
# preset (7 days at a 15s collector tick is ~40,320 rows), so that range is never truncated,
# while still bounding worst-case memory for a much longer custom range.
_MAX_FETCH_ROWS = 50_000

T = TypeVar("T")


def _even_sample(rows_desc: list[T], limit: int) -> list[T]:
    """`rows_desc` is ordered most-recent-first. If it already fits within `limit`, return it
    unchanged. Otherwise select up to `limit` entries evenly spaced across the *entire* list
    (always including the newest and oldest), rather than silently keeping only the first
    `limit` - which would just be the most recent slice, not a view spanning the whole range
    that was asked for."""
    if limit <= 0:
        return []
    if len(rows_desc) <= limit:
        return rows_desc
    if limit == 1:
        return rows_desc[:1]

    step = (len(rows_desc) - 1) / (limit - 1)
    seen: set[int] = set()
    result: list[T] = []
    for i in range(limit):
        idx = round(i * step)
        if idx not in seen:
            seen.add(idx)
            result.append(rows_desc[idx])
    return result


class TelemetryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert_session_samples(
        self, monitored_database_id: str, sampled_at, rows: list[SessionSampleRow]
    ) -> None:
        self.db.add_all(
            [
                SessionSample(
                    monitored_database_id=monitored_database_id,
                    sampled_at=sampled_at,
                    sid=row.sid,
                    serial_num=row.serial_num,
                    username=row.username,
                    sql_id=row.sql_id,
                    event=row.event,
                    wait_class=row.wait_class,
                    status=row.status,
                    blocking_session=row.blocking_session,
                    machine=row.machine,
                    program=row.program,
                    module=row.module,
                    action=row.action,
                    state=row.state,
                    last_call_et=row.last_call_et,
                )
                for row in rows
            ]
        )
        await self.db.flush()

    async def insert_wait_snapshots(self, monitored_database_id: str, sampled_at, rows: list[WaitSnapshotRow]) -> None:
        self.db.add_all(
            [
                WaitSnapshot(
                    monitored_database_id=monitored_database_id,
                    sampled_at=sampled_at,
                    event=row.event,
                    wait_class=row.wait_class,
                    total_waits=row.total_waits,
                    time_waited_centisecs=row.time_waited_centisecs,
                )
                for row in rows
            ]
        )
        await self.db.flush()

    async def insert_instance_health(
        self,
        monitored_database_id: str,
        sampled_at,
        reachability: str,
        sample: InstanceHealthSample | None,
    ) -> None:
        self.db.add(
            InstanceHealthSnapshot(
                monitored_database_id=monitored_database_id,
                sampled_at=sampled_at,
                reachability=reachability,
                instance_status=sample.status if sample else None,
                active_session_count=sample.active_session_count if sample else None,
                cpu_centisecs_used=sample.cpu_centisecs_used if sample else None,
            )
        )
        await self.db.flush()

    async def recent_session_samples(
        self, monitored_database_id: str, limit: int = 200, since=None, until=None
    ) -> list[SessionSample]:
        """Most-recent-first. A flat `ORDER BY sampled_at DESC LIMIT limit` would silently
        return an arbitrary partial subset of the newest round whenever that round alone has
        more rows than `limit` (every row in a round shares the same `sampled_at`, so there's
        no secondary ordering to make the cut deterministic or complete) - `latest_round()`
        callers (the /sessions and /blocking routes, the AI copilot, blocking-chain
        reconstruction) would then see a truncated "current state" with no indication, and a
        real blocker/waiter could vanish from a chain simply for landing on the wrong side of
        the cut. So the newest round in the window is always fetched in full, however large -
        a real V$SESSION round is at most a few thousand rows, never the unbounded scan
        `_MAX_FETCH_ROWS` guards against for actual multi-round history. Older rounds beyond
        that still respect `limit` (minus whatever the current round already used), matching
        the historical-sampling behavior report builders rely on."""
        latest_ts_query = select(SessionSample.sampled_at).where(
            SessionSample.monitored_database_id == monitored_database_id
        )
        if since is not None:
            latest_ts_query = latest_ts_query.where(SessionSample.sampled_at >= since)
        if until is not None:
            latest_ts_query = latest_ts_query.where(SessionSample.sampled_at <= until)
        latest_ts_query = latest_ts_query.order_by(SessionSample.sampled_at.desc()).limit(1)
        latest_ts = (await self.db.execute(latest_ts_query)).scalar_one_or_none()
        if latest_ts is None:
            return []

        current_round_query = (
            select(SessionSample)
            .where(
                SessionSample.monitored_database_id == monitored_database_id,
                SessionSample.sampled_at == latest_ts,
            )
            .order_by(SessionSample.sid)
        )
        current_round = list((await self.db.execute(current_round_query)).scalars().all())

        remaining = limit - len(current_round)
        older: list[SessionSample] = []
        if remaining > 0:
            older_query = (
                select(SessionSample)
                .where(
                    SessionSample.monitored_database_id == monitored_database_id,
                    SessionSample.sampled_at < latest_ts,
                )
                .order_by(SessionSample.sampled_at.desc())
                .limit(remaining)
            )
            if since is not None:
                older_query = older_query.where(SessionSample.sampled_at >= since)
            older = list((await self.db.execute(older_query)).scalars().all())

        return current_round + older

    async def recent_instance_health(
        self, monitored_database_id: str, limit: int = 50, since=None, until=None
    ) -> list[InstanceHealthSnapshot]:
        """Most-recent-first. When the window holds more than `limit` rows, returns an even
        spread across the *whole* window rather than just the most recent `limit` - see
        `_even_sample`."""
        query = (
            select(InstanceHealthSnapshot)
            .where(InstanceHealthSnapshot.monitored_database_id == monitored_database_id)
            .order_by(InstanceHealthSnapshot.sampled_at.desc())
            .limit(_MAX_FETCH_ROWS)
        )
        if since is not None:
            query = query.where(InstanceHealthSnapshot.sampled_at >= since)
        if until is not None:
            query = query.where(InstanceHealthSnapshot.sampled_at <= until)
        result = await self.db.execute(query)
        return _even_sample(list(result.scalars().all()), limit)

    async def latest_instance_health(self, monitored_database_id: str) -> InstanceHealthSnapshot | None:
        rows = await self.recent_instance_health(monitored_database_id, limit=1)
        return rows[0] if rows else None

    async def latest_two_instance_health(
        self, monitored_database_id: str
    ) -> tuple[InstanceHealthSnapshot, InstanceHealthSnapshot] | None:
        """The 2 most recent snapshots (most-recent-first), or None if fewer than two exist -
        deliberately NOT `recent_instance_health(limit=2)`, whose even-sampling spans the
        *whole* history once more than 2 rows exist (correct for a chart window, wrong here:
        a short rate needs two genuinely adjacent rounds, the same reason `latest_two_wait_
        rounds` exists as its own method rather than reusing `recent_wait_rounds(limit=2)`)."""
        result = await self.db.execute(
            select(InstanceHealthSnapshot)
            .where(InstanceHealthSnapshot.monitored_database_id == monitored_database_id)
            .order_by(InstanceHealthSnapshot.sampled_at.desc())
            .limit(2)
        )
        rows = list(result.scalars().all())
        return (rows[0], rows[1]) if len(rows) == 2 else None

    async def latest_two_wait_rounds(self, monitored_database_id: str):
        """Returns [(sampled_at, rows), (sampled_at, rows)] for the two most recent distinct
        collection rounds, oldest first - or None if fewer than two rounds exist yet."""
        ts_result = await self.db.execute(
            select(WaitSnapshot.sampled_at)
            .where(WaitSnapshot.monitored_database_id == monitored_database_id)
            .distinct()
            .order_by(WaitSnapshot.sampled_at.desc())
            .limit(2)
        )
        timestamps = [row[0] for row in ts_result.all()]
        if len(timestamps) < 2:
            return None
        timestamps.sort()

        rounds = []
        for ts in timestamps:
            rows_result = await self.db.execute(
                select(WaitSnapshot).where(
                    WaitSnapshot.monitored_database_id == monitored_database_id,
                    WaitSnapshot.sampled_at == ts,
                )
            )
            rounds.append((ts, list(rows_result.scalars().all())))
        return rounds

    async def recent_wait_rounds(
        self, monitored_database_id: str, since=None, until=None, limit: int = 500
    ) -> list[tuple]:
        """Returns [(sampled_at, rows), ...] oldest first, for up to `limit` distinct
        collection rounds in [since, until] - the historical counterpart to
        `latest_two_wait_rounds`, which is hardcoded to exactly 2. Same even-sampling as
        `recent_instance_health` when the window holds more distinct rounds than `limit`."""
        ts_query = (
            select(WaitSnapshot.sampled_at)
            .where(WaitSnapshot.monitored_database_id == monitored_database_id)
            .distinct()
            .order_by(WaitSnapshot.sampled_at.desc())
            .limit(_MAX_FETCH_ROWS)
        )
        if since is not None:
            ts_query = ts_query.where(WaitSnapshot.sampled_at >= since)
        if until is not None:
            ts_query = ts_query.where(WaitSnapshot.sampled_at <= until)
        ts_result = await self.db.execute(ts_query)
        timestamps_desc = [row[0] for row in ts_result.all()]
        timestamps = sorted(_even_sample(timestamps_desc, limit))
        if not timestamps:
            return []

        rows_result = await self.db.execute(
            select(WaitSnapshot).where(
                WaitSnapshot.monitored_database_id == monitored_database_id,
                WaitSnapshot.sampled_at.in_(timestamps),
            )
        )
        rows_by_ts: dict = {}
        for row in rows_result.scalars().all():
            rows_by_ts.setdefault(row.sampled_at, []).append(row)
        return [(ts, rows_by_ts.get(ts, [])) for ts in timestamps]


class CollectorStatusRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_database_id(self, monitored_database_id: str) -> CollectorStatus | None:
        result = await self.db.execute(
            select(CollectorStatus).where(CollectorStatus.monitored_database_id == monitored_database_id)
        )
        return result.scalar_one_or_none()

    async def record_success(self, monitored_database_id: str, duration_ms: int) -> CollectorStatus:
        status = await self._get_or_create(monitored_database_id)
        now = utcnow()
        status.last_run_at = now
        status.last_success_at = now
        status.consecutive_failures = 0
        status.last_error_category = None
        status.last_run_duration_ms = duration_ms
        await self.db.flush()
        return status

    async def record_failure(self, monitored_database_id: str, category: str, duration_ms: int) -> CollectorStatus:
        status = await self._get_or_create(monitored_database_id)
        now = utcnow()
        status.last_run_at = now
        status.last_failure_at = now
        status.consecutive_failures += 1
        status.last_error_category = category
        status.last_run_duration_ms = duration_ms
        await self.db.flush()
        return status

    async def record_sql_success(self, monitored_database_id: str) -> CollectorStatus:
        status = await self._get_or_create(monitored_database_id)
        now = utcnow()
        status.last_sql_run_at = now
        status.last_sql_success_at = now
        status.last_sql_error_category = None
        await self.db.flush()
        return status

    async def record_sql_failure(self, monitored_database_id: str, category: str) -> CollectorStatus:
        status = await self._get_or_create(monitored_database_id)
        status.last_sql_run_at = utcnow()
        status.last_sql_error_category = category
        await self.db.flush()
        return status

    async def record_capacity_success(self, monitored_database_id: str) -> CollectorStatus:
        status = await self._get_or_create(monitored_database_id)
        now = utcnow()
        status.last_capacity_run_at = now
        status.last_capacity_success_at = now
        status.last_capacity_error_category = None
        await self.db.flush()
        return status

    async def record_capacity_failure(self, monitored_database_id: str, category: str) -> CollectorStatus:
        status = await self._get_or_create(monitored_database_id)
        status.last_capacity_run_at = utcnow()
        status.last_capacity_error_category = category
        await self.db.flush()
        return status

    async def record_statspack_success(self, monitored_database_id: str) -> CollectorStatus:
        status = await self._get_or_create(monitored_database_id)
        now = utcnow()
        status.last_statspack_run_at = now
        status.last_statspack_success_at = now
        status.last_statspack_error_category = None
        await self.db.flush()
        return status

    async def record_statspack_failure(self, monitored_database_id: str, category: str) -> CollectorStatus:
        status = await self._get_or_create(monitored_database_id)
        status.last_statspack_run_at = utcnow()
        status.last_statspack_error_category = category
        await self.db.flush()
        return status

    async def record_config_success(self, monitored_database_id: str) -> CollectorStatus:
        status = await self._get_or_create(monitored_database_id)
        now = utcnow()
        status.last_config_run_at = now
        status.last_config_success_at = now
        status.last_config_error_category = None
        await self.db.flush()
        return status

    async def record_config_failure(self, monitored_database_id: str, category: str) -> CollectorStatus:
        status = await self._get_or_create(monitored_database_id)
        status.last_config_run_at = utcnow()
        status.last_config_error_category = category
        await self.db.flush()
        return status

    async def _get_or_create(self, monitored_database_id: str) -> CollectorStatus:
        existing = await self.get_by_database_id(monitored_database_id)
        if existing:
            return existing
        status = CollectorStatus(monitored_database_id=monitored_database_id)
        self.db.add(status)
        await self.db.flush()
        return status
