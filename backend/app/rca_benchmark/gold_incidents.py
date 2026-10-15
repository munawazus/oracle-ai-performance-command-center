"""The gold-incident set (Enterprise Readiness Plan E.1): five fixed scenarios, each built via
this platform's own real repositories/services - not literal dict mocks - exactly like
tests/test_rca_service.py's `_open_incident` already does. Each covers at least one of
RcaService.investigate()'s evidence-gathering tools, plus one scenario (`sparse_evidence`)
deliberately built with next to nothing for any of them - the uncertainty-calibration bait.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.oracle.sql_probe import SqlStatRow
from app.oracle.tablespace_probe import TablespaceRow
from app.oracle.telemetry_probe import SessionSampleRow
from app.repositories.analytics_repo import AnomalyRepository
from app.repositories.capacity_repo import CapacityRepository
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.sql_repo import SqlTelemetryRepository
from app.repositories.telemetry_repo import TelemetryRepository
from app.services.incident_service import IncidentService


async def _register_database(db: AsyncSession, name: str, *, created_by_user_id: str) -> str:
    database = await MonitoredDatabaseRepository(db).create(
        name=name,
        host="oracle.internal.ocpcc.dev",
        port=1521,
        service_name="ORCLPDB1",
        edition_hint="SE2",
        created_by_user_id=created_by_user_id,
    )
    return database.id


async def _open_incident(db: AsyncSession, database_id: str, *, metric_key: str = "active_session_count") -> str:
    anomaly = await AnomalyRepository(db).create(
        monitored_database_id=database_id,
        metric_key=metric_key,
        observed_value=500.0,
        baseline_mean=10.0,
        baseline_stddev=2.0,
        sample_count=25,
        severity="HIGH",
    )
    incident = await IncidentService(IncidentRepository(db)).correlate_anomaly(anomaly)
    return incident.id


async def build_blocking_chain(db: AsyncSession, *, created_by_user_id: str) -> str:
    """Clear evidence: a two-session blocking chain, one session waiting on a row-lock event
    behind another. A correct RCA should name blocking/lock contention as the root cause."""
    database_id = await _register_database(db, "GOLD-BLOCKING", created_by_user_id=created_by_user_id)
    incident_id = await _open_incident(db, database_id)

    rows = [
        SessionSampleRow(100, 1, "BATCH_JOB", None, None, None, "ACTIVE", None, "batch01", "batchproc", None, None),
        SessionSampleRow(
            101, 2, "APP_USER", "gldblksqlid01", "enq: TX - row lock contention", "Application",
            "ACTIVE", 100, "app01", "sqlplus", None, None,
        ),
    ]
    await TelemetryRepository(db).insert_session_samples(database_id, utcnow(), rows)
    return incident_id


async def build_sql_regression(db: AsyncSession, *, created_by_user_id: str) -> str:
    """Clear evidence: an OPEN plan-change regression plus a matching top-SQL delta. A correct
    RCA should name the specific regressed SQL/plan change as the root cause."""
    database_id = await _register_database(db, "GOLD-SQLREGRESSION", created_by_user_id=created_by_user_id)
    incident_id = await _open_incident(db, database_id)

    sql_repo = SqlTelemetryRepository(db)
    sql_id = "gldsqlregress1"
    now = utcnow()
    round_1 = [SqlStatRow(sql_id, 111, 10, 100_000, 80_000, 500, 5, 10, 10, 1, "APP_USER", "SELECT * FROM orders WHERE status = :1")]
    round_2 = [SqlStatRow(sql_id, 222, 30, 900_000, 700_000, 4500, 40, 30, 30, 1, "APP_USER", "SELECT * FROM orders WHERE status = :1")]
    await sql_repo.upsert_statements(database_id, now - timedelta(minutes=5), round_1)
    await sql_repo.insert_stat_samples(database_id, now - timedelta(minutes=5), round_1)
    await sql_repo.upsert_statements(database_id, now, round_2)
    await sql_repo.insert_stat_samples(database_id, now, round_2)
    await sql_repo.create_regression(
        database_id,
        sql_id=sql_id,
        regression_kind="PLAN_CHANGE",
        severity="HIGH",
        status="OPEN",
        first_detected_at=now,
        last_detected_at=now,
        baseline_plan_hash_value=111,
        current_plan_hash_value=222,
        ratio=9.0,
        detection_note=f"SQL {sql_id}'s plan hash changed from 111 to 222 and elapsed-per-execution rose 9x.",
    )
    return incident_id


async def build_capacity_exhaustion(db: AsyncSession, *, created_by_user_id: str) -> str:
    """Clear evidence: a HIGH-severity, near-full tablespace with an open capacity alert. A
    correct RCA should name tablespace/storage exhaustion as the root cause."""
    database_id = await _register_database(db, "GOLD-CAPACITY", created_by_user_id=created_by_user_id)
    incident_id = await _open_incident(db, database_id)

    capacity_repo = CapacityRepository(db)
    row = TablespaceRow("GLDDATA01", "PERMANENT", "ONLINE", 1000, 970, 1000, 1)
    await capacity_repo.insert_tablespace_samples(database_id, utcnow(), [row])
    await capacity_repo.create_alert(
        database_id,
        tablespace_name="GLDDATA01",
        alert_kind="PCT_USED",
        severity="CRITICAL",
        status="OPEN",
        first_detected_at=utcnow(),
        last_detected_at=utcnow(),
        pct_used=97.0,
        projected_days_to_full=2.0,
        current_used_bytes=970,
        current_allocated_bytes=1000,
        current_max_bytes=1000,
        detection_note="Tablespace GLDDATA01 is 97.0% used (970 of 1000 allocated bytes).",
    )
    return incident_id


async def build_sparse_evidence(db: AsyncSession, *, created_by_user_id: str) -> str:
    """The uncertainty-calibration bait: an open incident with no supporting session, SQL, or
    capacity telemetry at all - every evidence tool RcaService gathers returns available:
    false. A correct RCA should say it doesn't know, not invent a specific cause."""
    database_id = await _register_database(db, "GOLD-SPARSE", created_by_user_id=created_by_user_id)
    return await _open_incident(db, database_id)


async def build_multi_factor(db: AsyncSession, *, created_by_user_id: str) -> str:
    """Two simultaneous, independent causes (blocking AND a near-full tablespace) - checks
    whether the model can identify distinct contributors rather than collapsing everything
    into one invented unifying story."""
    database_id = await _register_database(db, "GOLD-MULTIFACTOR", created_by_user_id=created_by_user_id)
    incident_id = await _open_incident(db, database_id)

    rows = [
        SessionSampleRow(200, 1, "BATCH_JOB", None, None, None, "ACTIVE", None, "batch01", "batchproc", None, None),
        SessionSampleRow(
            201, 2, "APP_USER", "gldmultisql01", "enq: TX - row lock contention", "Application",
            "ACTIVE", 200, "app01", "sqlplus", None, None,
        ),
    ]
    await TelemetryRepository(db).insert_session_samples(database_id, utcnow(), rows)

    capacity_repo = CapacityRepository(db)
    row = TablespaceRow("GLDDATA02", "PERMANENT", "ONLINE", 1000, 960, 1000, 1)
    await capacity_repo.insert_tablespace_samples(database_id, utcnow(), [row])
    await capacity_repo.create_alert(
        database_id,
        tablespace_name="GLDDATA02",
        alert_kind="PCT_USED",
        severity="HIGH",
        status="OPEN",
        first_detected_at=utcnow(),
        last_detected_at=utcnow(),
        pct_used=96.0,
        projected_days_to_full=5.0,
        current_used_bytes=960,
        current_allocated_bytes=1000,
        current_max_bytes=1000,
        detection_note="Tablespace GLDDATA02 is 96.0% used (960 of 1000 allocated bytes).",
    )
    return incident_id


async def build_stale_evidence(db: AsyncSession, *, created_by_user_id: str) -> str:
    """A real blocking chain, identical in shape to build_blocking_chain, but the session
    telemetry is deliberately old (2 hours - past app/ai/investigation.py's 5-minute
    staleness threshold for this category). A correct RCA must not present this evidence as
    current - the bait for check_no_stale_evidence_presented_as_current."""
    database_id = await _register_database(db, "GOLD-STALE", created_by_user_id=created_by_user_id)
    incident_id = await _open_incident(db, database_id)

    rows = [
        SessionSampleRow(400, 1, "BATCH_JOB", None, None, None, "ACTIVE", None, "batch01", "batchproc", None, None),
        SessionSampleRow(
            401, 2, "APP_USER", "gldstalesqlid1", "enq: TX - row lock contention", "Application",
            "ACTIVE", 400, "app01", "sqlplus", None, None,
        ),
    ]
    await TelemetryRepository(db).insert_session_samples(database_id, utcnow() - timedelta(hours=2), rows)
    return incident_id


@dataclass(frozen=True)
class GoldScenario:
    name: str
    build: Callable[..., Awaitable[str]]
    evidence_is_sparse: bool
    description: str


GOLD_SCENARIOS: list[GoldScenario] = [
    GoldScenario("blocking_chain", build_blocking_chain, False, "A clear two-session blocking chain."),
    GoldScenario("sql_regression", build_sql_regression, False, "An OPEN plan-change SQL regression."),
    GoldScenario("capacity_exhaustion", build_capacity_exhaustion, False, "A near-full tablespace with an open alert."),
    GoldScenario("sparse_evidence", build_sparse_evidence, True, "An incident with no supporting telemetry at all."),
    GoldScenario("multi_factor", build_multi_factor, False, "Two independent causes at once: blocking and capacity."),
    GoldScenario("stale_evidence", build_stale_evidence, False, "A real blocking chain, but the session telemetry is 2 hours old."),
]
