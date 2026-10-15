"""Scheduled Oracle telemetry collection. Runs outside any HTTP request, so each database's
collection cycle opens and commits its own DB session (same pattern as app/seed.py) rather
than relying on the FastAPI get_db() dependency. One database's failure never aborts
another's - each is caught and recorded independently.

Every real Oracle interaction (connect, every probe/query, close) is synchronous, blocking
python-oracledb code - none of it is `await`-able. Each individual blocking call is therefore
wrapped in its own `await asyncio.to_thread(...)` right where it already happens - otherwise
that blocking work runs directly on the single Uvicorn event loop and freezes the whole
application for every user for as long as one database's connection/query attempt takes
(Enterprise Readiness Plan F.1 - proved live: an unrelated `GET /api/health` call took 3-8s
instead of milliseconds while a handful of unreachable-host databases were registered). Same
pattern app/security/ssrf_guard.py already uses for `socket.getaddrinfo`. Wrapping each call
individually (rather than bundling connect+query+close into one helper) keeps every existing
try/except/finally structure, log message, and failure-category recording exactly where it
already is - a minimal, mechanical, behavior-preserving change, not a rewrite.
"""

import asyncio
import time

from app.db import SessionLocal
from app.logging_config import get_logger
from app.models.base import utcnow
from app.oracle.config_probe import sample_non_default_parameters
from app.oracle.connector import OracleConnectError, OracleConnector
from app.oracle.params import build_connection_params
from app.oracle.sql_probe import sample_sql_plans, sample_top_sql
from app.oracle.statspack_probe import (
    sample_new_snapshots,
    sample_sql_for_snapshots,
    sample_wait_events_for_snapshots,
)
from app.oracle.tablespace_probe import sample_permanent_tablespaces, sample_temp_tablespaces
from app.oracle.telemetry_probe import sample_instance_health, sample_sessions, sample_wait_events
from app.repositories.capacity_repo import CapacityRepository
from app.repositories.config_repo import ConfigTelemetryRepository
from app.repositories.database_repo import (
    CapabilityRepository,
    DatabaseCredentialRepository,
    MonitoredDatabaseRepository,
)
from app.repositories.secret_repo import SecretRepository
from app.repositories.sql_repo import SqlTelemetryRepository
from app.repositories.statspack_repo import StatspackRepository
from app.repositories.telemetry_repo import CollectorStatusRepository, TelemetryRepository
from app.services.secret_service import LocalEncryptedSecretProvider

logger = get_logger(__name__)


class CollectorService:
    def __init__(self, connector: OracleConnector):
        self.connector = connector

    async def run_once_for_database(self, database_id: str) -> None:
        start = time.monotonic()

        async with SessionLocal() as db:
            db_repo = MonitoredDatabaseRepository(db)
            cred_repo = DatabaseCredentialRepository(db)
            secrets = LocalEncryptedSecretProvider(SecretRepository(db))
            telemetry_repo = TelemetryRepository(db)
            status_repo = CollectorStatusRepository(db)

            try:
                params = await build_connection_params(db_repo, cred_repo, secrets, database_id)
            except Exception:
                logger.error("collector_params_unavailable", database_id=database_id)
                await db.commit()
                return

            sampled_at = utcnow()
            try:
                conn = await asyncio.to_thread(self.connector, params)
            except OracleConnectError as exc:
                logger.warning("collector_connect_failed", database_id=database_id, category=exc.category)
                await telemetry_repo.insert_instance_health(database_id, sampled_at, exc.category, None)
                await status_repo.record_failure(database_id, exc.category, self._elapsed_ms(start))
                await db.commit()
                return

            try:
                sessions = await asyncio.to_thread(sample_sessions, conn)
                waits = await asyncio.to_thread(sample_wait_events, conn)
                health = await asyncio.to_thread(sample_instance_health, conn)
            except Exception:
                logger.error("collector_sample_failed", database_id=database_id)
                await status_repo.record_failure(database_id, "unknown_error", self._elapsed_ms(start))
                await db.commit()
                return
            finally:
                await asyncio.to_thread(conn.close)

            await telemetry_repo.insert_session_samples(database_id, sampled_at, sessions)
            await telemetry_repo.insert_wait_snapshots(database_id, sampled_at, waits)
            await telemetry_repo.insert_instance_health(database_id, sampled_at, "up", health)
            await status_repo.record_success(database_id, self._elapsed_ms(start))
            await db.commit()

    async def run_once_all_active(self) -> None:
        async with SessionLocal() as db:
            db_repo = MonitoredDatabaseRepository(db)
            database_ids = await db_repo.list_active_ids()

        for database_id in database_ids:
            try:
                await self.run_once_for_database(database_id)
            except Exception:
                logger.error("collector_run_crashed", database_id=database_id)

    async def run_sql_once_for_database(self, database_id: str, *, top_n: int, capture_text: bool = True) -> None:
        """A deliberately separate job from run_once_for_database (Enterprise Readiness Plan
        D.1): its own connection, own failure isolation, own interval. A monitoring account
        missing SELECT on V$SQL_PLAN specifically must not be able to kill session/wait/
        health collection too - the broad except below only ever affects this job's own
        SQL-intelligence tables, never the ones the 15s collector owns. It also does not
        touch record_success/record_failure or insert_instance_health - reachability
        reporting stays the main collector's job, so the two never double-write the same
        availability signal."""
        async with SessionLocal() as db:
            db_repo = MonitoredDatabaseRepository(db)
            cred_repo = DatabaseCredentialRepository(db)
            secrets = LocalEncryptedSecretProvider(SecretRepository(db))
            sql_repo = SqlTelemetryRepository(db)
            status_repo = CollectorStatusRepository(db)

            try:
                params = await build_connection_params(db_repo, cred_repo, secrets, database_id)
            except Exception:
                logger.error("sql_collector_params_unavailable", database_id=database_id)
                await db.commit()
                return

            sampled_at = utcnow()
            try:
                conn = await asyncio.to_thread(self.connector, params)
            except OracleConnectError as exc:
                logger.warning("sql_collector_connect_failed", database_id=database_id, category=exc.category)
                await status_repo.record_sql_failure(database_id, exc.category)
                await db.commit()
                return

            try:
                try:
                    stats = await asyncio.to_thread(sample_top_sql, conn, top_n)
                except Exception:
                    logger.error("sql_collector_sample_failed", database_id=database_id)
                    await status_repo.record_sql_failure(database_id, "unknown_error")
                    await db.commit()
                    return

                # A separate, nested try: a missing SELECT on V$SQL_PLAN specifically (a
                # narrower grant than V$SQL) still lets top-SQL stats persist - partial
                # capability, honestly recorded, rather than losing everything for want of
                # one view's privilege.
                try:
                    plans = await asyncio.to_thread(sample_sql_plans, conn, top_n)
                except Exception:
                    logger.warning("sql_plan_sample_failed", database_id=database_id)
                    plans = []
            finally:
                await asyncio.to_thread(conn.close)

            await sql_repo.upsert_statements(database_id, sampled_at, stats, capture_text=capture_text)
            await sql_repo.insert_stat_samples(database_id, sampled_at, stats)
            new_plan_count = await sql_repo.upsert_plan_snapshots(database_id, sampled_at, plans)
            await status_repo.record_sql_success(database_id)
            await db.commit()
            logger.info(
                "sql_collector_run_completed",
                database_id=database_id,
                stat_count=len(stats),
                new_plan_count=new_plan_count,
            )

    async def run_sql_once_all_active(self, *, top_n: int, capture_text: bool = True) -> None:
        async with SessionLocal() as db:
            db_repo = MonitoredDatabaseRepository(db)
            database_ids = await db_repo.list_active_ids()

        for database_id in database_ids:
            try:
                await self.run_sql_once_for_database(database_id, top_n=top_n, capture_text=capture_text)
            except Exception:
                logger.error("sql_collector_run_crashed", database_id=database_id)

    async def run_capacity_once_for_database(self, database_id: str) -> None:
        """Another separate job (Enterprise Readiness Plan D.2): own connection, own failure
        isolation, own interval - the same reasoning run_sql_once_for_database gives. The
        outer probe (PERMANENT/UNDO tablespaces) must succeed for anything to persist -
        tablespace/UNDO exhaustion is the more universally critical DBA concern. The inner,
        nested probe (TEMPORARY tablespaces) is best-effort: a missing grant on
        DBA_TEMP_FILES/V$TEMP_SPACE_HEADER specifically still lets PERMANENT/UNDO data
        persist - partial capability, honestly recorded, same rule as the SQL collector's
        stats-vs-plans split. Never touches record_success/record_failure or
        insert_instance_health - reachability stays the main collector's job."""
        async with SessionLocal() as db:
            db_repo = MonitoredDatabaseRepository(db)
            cred_repo = DatabaseCredentialRepository(db)
            secrets = LocalEncryptedSecretProvider(SecretRepository(db))
            capacity_repo = CapacityRepository(db)
            status_repo = CollectorStatusRepository(db)

            try:
                params = await build_connection_params(db_repo, cred_repo, secrets, database_id)
            except Exception:
                logger.error("capacity_collector_params_unavailable", database_id=database_id)
                await db.commit()
                return

            sampled_at = utcnow()
            try:
                conn = await asyncio.to_thread(self.connector, params)
            except OracleConnectError as exc:
                logger.warning("capacity_collector_connect_failed", database_id=database_id, category=exc.category)
                await status_repo.record_capacity_failure(database_id, exc.category)
                await db.commit()
                return

            try:
                try:
                    permanent_and_undo = await asyncio.to_thread(sample_permanent_tablespaces, conn)
                except Exception:
                    logger.error("capacity_collector_sample_failed", database_id=database_id)
                    await status_repo.record_capacity_failure(database_id, "unknown_error")
                    await db.commit()
                    return

                try:
                    temporary = await asyncio.to_thread(sample_temp_tablespaces, conn)
                except Exception:
                    logger.warning("capacity_temp_sample_failed", database_id=database_id)
                    temporary = []
            finally:
                await asyncio.to_thread(conn.close)

            rows = permanent_and_undo + temporary
            await capacity_repo.insert_tablespace_samples(database_id, sampled_at, rows)
            await status_repo.record_capacity_success(database_id)
            await db.commit()
            logger.info("capacity_collector_run_completed", database_id=database_id, tablespace_count=len(rows))

    async def run_capacity_once_all_active(self) -> None:
        async with SessionLocal() as db:
            db_repo = MonitoredDatabaseRepository(db)
            database_ids = await db_repo.list_active_ids()

        for database_id in database_ids:
            try:
                await self.run_capacity_once_for_database(database_id)
            except Exception:
                logger.error("capacity_collector_run_crashed", database_id=database_id)

    async def run_statspack_once_for_database(self, database_id: str, *, max_snapshots: int) -> None:
        """Another separate job (Enterprise Readiness Plan D.3): own connection, own failure
        isolation, own interval - same reasoning as the SQL/capacity collectors. The FIRST
        thing this does is check DatabaseCapability.statspack_available from Postgres only -
        if not True, return immediately without opening any Oracle connection, so a fleet
        where most databases don't have Statspack costs nothing extra. Within a sync attempt,
        the snapshot-listing query must succeed for anything to persist; the wait-event and
        SQL-summary queries each have their own nested try/except (partial capability, same
        pattern as the SQL collector's stats-vs-plans split) - a missing grant on
        STATS$SQL_SUMMARY specifically still lets wait-event data persist. Never touches
        record_success/record_failure/insert_instance_health, and never writes
        DatabaseCapability itself - only OracleCapabilityService.detect_capabilities() does
        that (single-writer invariant)."""
        async with SessionLocal() as db:
            capability = await CapabilityRepository(db).get_by_database_id(database_id)
            if not capability or not capability.statspack_available:
                return

            db_repo = MonitoredDatabaseRepository(db)
            cred_repo = DatabaseCredentialRepository(db)
            secrets = LocalEncryptedSecretProvider(SecretRepository(db))
            statspack_repo = StatspackRepository(db)
            status_repo = CollectorStatusRepository(db)

            try:
                params = await build_connection_params(db_repo, cred_repo, secrets, database_id)
            except Exception:
                logger.error("statspack_collector_params_unavailable", database_id=database_id)
                await db.commit()
                return

            try:
                conn = await asyncio.to_thread(self.connector, params)
            except OracleConnectError as exc:
                logger.warning(
                    "statspack_collector_connect_failed", database_id=database_id, category=exc.category
                )
                await status_repo.record_statspack_failure(database_id, exc.category)
                await db.commit()
                return

            try:
                sync_state = await statspack_repo.get_sync_state(database_id)
                since_snap_id = sync_state.last_synced_snap_id if sync_state else None

                try:
                    new_snapshots = await asyncio.to_thread(sample_new_snapshots, conn, since_snap_id, max_snapshots)
                except Exception:
                    logger.error("statspack_snapshot_sample_failed", database_id=database_id)
                    await status_repo.record_statspack_failure(database_id, "unknown_error")
                    await db.commit()
                    return

                if not new_snapshots:
                    await status_repo.record_statspack_success(database_id)
                    await db.commit()
                    return

                snap_ids = [s.snap_id for s in new_snapshots]

                try:
                    wait_events = await asyncio.to_thread(sample_wait_events_for_snapshots, conn, snap_ids)
                except Exception:
                    logger.warning("statspack_wait_event_sample_failed", database_id=database_id)
                    wait_events = []

                try:
                    sql_rows = await asyncio.to_thread(sample_sql_for_snapshots, conn, snap_ids)
                except Exception:
                    logger.warning("statspack_sql_sample_failed", database_id=database_id)
                    sql_rows = []
            finally:
                await asyncio.to_thread(conn.close)

            synced_at = utcnow()
            await statspack_repo.insert_snapshots(database_id, synced_at, new_snapshots)
            await statspack_repo.insert_wait_event_samples(database_id, wait_events)
            await statspack_repo.insert_sql_samples(database_id, sql_rows)
            last_snapshot = new_snapshots[-1]
            await statspack_repo.upsert_sync_state(database_id, last_snapshot.snap_id, last_snapshot.snap_time)
            await status_repo.record_statspack_success(database_id)
            await db.commit()
            logger.info(
                "statspack_collector_run_completed",
                database_id=database_id,
                new_snapshot_count=len(new_snapshots),
            )

    async def run_statspack_once_all_active(self, *, max_snapshots: int) -> None:
        async with SessionLocal() as db:
            db_repo = MonitoredDatabaseRepository(db)
            database_ids = await db_repo.list_active_ids()

        for database_id in database_ids:
            try:
                await self.run_statspack_once_for_database(database_id, max_snapshots=max_snapshots)
            except Exception:
                logger.error("statspack_collector_run_crashed", database_id=database_id)

    async def run_config_once_for_database(self, database_id: str) -> None:
        """Change Intelligence (Enterprise Readiness Plan D.4): own connection, own failure
        isolation, own interval - same reasoning as the capacity/Statspack collectors. Simplest
        of the four: a single query (V$SYSTEM_PARAMETER), so no partial-capability nesting is
        needed - either it succeeds and the round persists, or it fails and nothing does. No
        capability pre-check either: unlike Statspack, this view is always present, so a
        missing grant is just an ordinary probe failure. Never touches
        record_success/record_failure/insert_instance_health - reachability stays the main
        collector's job."""
        async with SessionLocal() as db:
            db_repo = MonitoredDatabaseRepository(db)
            cred_repo = DatabaseCredentialRepository(db)
            secrets = LocalEncryptedSecretProvider(SecretRepository(db))
            config_repo = ConfigTelemetryRepository(db)
            status_repo = CollectorStatusRepository(db)

            try:
                params = await build_connection_params(db_repo, cred_repo, secrets, database_id)
            except Exception:
                logger.error("config_collector_params_unavailable", database_id=database_id)
                await db.commit()
                return

            sampled_at = utcnow()
            try:
                conn = await asyncio.to_thread(self.connector, params)
            except OracleConnectError as exc:
                logger.warning("config_collector_connect_failed", database_id=database_id, category=exc.category)
                await status_repo.record_config_failure(database_id, exc.category)
                await db.commit()
                return

            try:
                try:
                    rows = await asyncio.to_thread(sample_non_default_parameters, conn)
                except Exception:
                    logger.error("config_collector_sample_failed", database_id=database_id)
                    await status_repo.record_config_failure(database_id, "unknown_error")
                    await db.commit()
                    return
            finally:
                await asyncio.to_thread(conn.close)

            await config_repo.insert_parameter_samples(database_id, sampled_at, rows)
            await status_repo.record_config_success(database_id)
            await db.commit()
            logger.info("config_collector_run_completed", database_id=database_id, parameter_count=len(rows))

    async def run_config_once_all_active(self) -> None:
        async with SessionLocal() as db:
            db_repo = MonitoredDatabaseRepository(db)
            database_ids = await db_repo.list_active_ids()

        for database_id in database_ids:
            try:
                await self.run_config_once_for_database(database_id)
            except Exception:
                logger.error("config_collector_run_crashed", database_id=database_id)

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.monotonic() - start) * 1000)
