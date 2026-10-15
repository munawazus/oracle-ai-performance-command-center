from datetime import datetime, timedelta
from typing import Literal, Sequence, cast

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models.base import utcnow
from app.models.capacity_telemetry import CapacityAlertEvent
from app.models.database import DatabaseCapability, MonitoredDatabase
from app.repositories.audit_repo import AuditRepository
from app.repositories.database_repo import (
    CapabilityRepository,
    DatabaseCredentialRepository,
    DatabaseGroupRepository,
    MonitoredDatabaseRepository,
)
from app.analytics.capacity_metrics import (
    GROWTH_TREND_WINDOW,
    GrowthTrend,
    TablespaceSampleLike,
    TablespaceUsage,
    compute_growth_trend,
    describe_usage,
)
from app.analytics.baseline_engine import EMPTY_STATS, MIN_SAMPLES_FOR_ANOMALY, BaselineStats, classify_severity, z_score
from app.analytics.metrics import WaitRow, aggregate_by_wait_class, compute_wait_class_rates
from app.analytics.sql_metrics import SqlStatRowLike, compute_sql_deltas, rank_sql_deltas
from app.analytics.config_metrics import compute_parameter_changes
from app.analytics.statspack_metrics import adapt_sql_samples
from app.oracle.connector import OracleConnector, get_oracle_connector
from app.repositories.analytics_repo import AnomalyRepository, BaselineRepository
from app.repositories.capacity_repo import CapacityRepository
from app.repositories.config_repo import ConfigTelemetryRepository
from app.repositories.secret_repo import SecretRepository
from app.repositories.sql_repo import SqlTelemetryRepository
from app.repositories.statspack_repo import StatspackRepository
from app.repositories.telemetry_repo import CollectorStatusRepository, TelemetryRepository
from app.schemas.database import (
    AssignGroupsRequest,
    BulkCreateDatabasesRequest,
    BulkCreateDatabasesResponse,
    BulkCreateResult,
    CapabilityProfileResponse,
    CreateDatabaseRequest,
    DatabaseResponse,
    TestConnectionResponse,
    UpdateDatabaseRequest,
)
from app.schemas.telemetry import (
    AnomalyResponse,
    BlockingChain,
    BlockingSnapshotResponse,
    CapacityAlertResponse,
    CapacityOverviewResponse,
    CapacityThresholdsRequest,
    CapacityThresholdsResponse,
    CollectorStatusResponse,
    ConfigChangeEvent,
    ConfigurationSummaryResponse,
    CpuHistoryPoint,
    InstanceHealthPointResponse,
    MetricCompareResponse,
    ParameterChangeItem,
    SessionResponse,
    SessionsSnapshotResponse,
    SqlDetailResponse,
    SqlPlanLine,
    SqlPlanResponse,
    SqlPlanSummary,
    SqlRegressionResponse,
    SqlSessionReference,
    SqlSortKey,
    StatspackSnapshotRef,
    StatspackSqlRow,
    StatspackSummaryResponse,
    StatspackWaitClassRate,
    TablespaceDetailResponse,
    TablespaceHistoryPoint,
    TablespaceUsageRow,
    TopSqlResponse,
    TopSqlRow,
    WaitClassHistoryPoint,
    WaitClassRate,
    WaitProfileResponse,
)
from app.security.deps import get_client_ip, require_permission, require_rate_limit
from app.services.analytics_service import AnalyticsService
from app.services.audit_service import AuditService
from app.services.database_service import DatabaseService
from app.services.incident_service import humanize_metric_key
from app.services.oracle_capability_service import OracleCapabilityService
from app.services.secret_service import LocalEncryptedSecretProvider
from app.telemetry_views import build_blocking_chains, latest_round

router = APIRouter(prefix="/databases", tags=["databases"])


def _build_service(db: AsyncSession) -> DatabaseService:
    return DatabaseService(
        MonitoredDatabaseRepository(db),
        DatabaseCredentialRepository(db),
        DatabaseGroupRepository(db),
        LocalEncryptedSecretProvider(SecretRepository(db)),
        AuditService(AuditRepository(db)),
    )


def _build_capability_service(db: AsyncSession, connector: OracleConnector) -> OracleCapabilityService:
    return OracleCapabilityService(
        MonitoredDatabaseRepository(db),
        DatabaseCredentialRepository(db),
        CapabilityRepository(db),
        LocalEncryptedSecretProvider(SecretRepository(db)),
        AuditService(AuditRepository(db)),
        connector,
    )


@router.get("", response_model=list[DatabaseResponse])
async def list_databases(
    group_id: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> list[MonitoredDatabase]:
    """`group_id`/`tag` are the Master Plan Phase 22 fleet filters - both optional, both
    composable with each other."""
    service = _build_service(db)
    return await service.list_databases(group_id=group_id, tag=tag)


@router.post("/bulk", response_model=BulkCreateDatabasesResponse, status_code=201)
async def bulk_create_databases(
    payload: BulkCreateDatabasesRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("database:add")),
    _rate_limit=Depends(require_rate_limit("database_bulk_create", limit=5, window_seconds=60)),
) -> BulkCreateDatabasesResponse:
    """Bulk onboarding "where safe" (master plan Sec 35 Phase 22): one row's failure (e.g. a
    duplicate name) never aborts the rest of the batch - every row gets the exact same
    validation/secret-storage/audit path as a single POST /databases. Registered before
    GET /{database_id} is irrelevant here since this is POST-only and no other POST route
    matches a single literal "bulk" segment, but is placed early for readability."""
    service = _build_service(db)
    raw_results = await service.bulk_create_databases(
        [item.model_dump() for item in payload.databases],
        actor_user_id=actor.id,
        ip_address=get_client_ip(request),
    )
    results = [
        BulkCreateResult(index=r["index"], status=r["status"], database=r["database"], error=r["error"])
        for r in raw_results
    ]
    return BulkCreateDatabasesResponse(
        results=results,
        created_count=sum(1 for r in results if r.status == "created"),
        failed_count=sum(1 for r in results if r.status == "failed"),
    )


@router.get("/{database_id}", response_model=DatabaseResponse)
async def get_database(
    database_id: str, db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("database:view"))
) -> MonitoredDatabase:
    service = _build_service(db)
    return await service.get_database(database_id)


@router.post("", response_model=DatabaseResponse, status_code=201)
async def create_database(
    payload: CreateDatabaseRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("database:add")),
) -> MonitoredDatabase:
    service = _build_service(db)
    return await service.create_database(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        service_name=payload.service_name,
        edition_hint=payload.edition_hint,
        username=payload.username,
        password=payload.password,
        tags=payload.tags,
        actor_user_id=actor.id,
        ip_address=get_client_ip(request),
    )


@router.get("/{database_id}/groups", response_model=list[str])
async def get_database_groups(
    database_id: str, db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("database:view"))
) -> list[str]:
    service = _build_service(db)
    return await service.get_group_memberships(database_id)


@router.put("/{database_id}/groups", response_model=list[str])
async def set_database_groups(
    database_id: str,
    payload: AssignGroupsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("database:edit")),
) -> list[str]:
    service = _build_service(db)
    return await service.set_group_memberships(
        database_id, payload.group_ids, actor_user_id=actor.id, ip_address=get_client_ip(request)
    )


@router.patch("/{database_id}", response_model=DatabaseResponse)
async def update_database(
    database_id: str,
    payload: UpdateDatabaseRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("database:edit")),
) -> MonitoredDatabase:
    service = _build_service(db)
    return await service.update_database(
        database_id,
        name=payload.name,
        host=payload.host,
        port=payload.port,
        service_name=payload.service_name,
        edition_hint=payload.edition_hint,
        is_active=payload.is_active,
        username=payload.username,
        password=payload.password,
        tags=payload.tags,
        actor_user_id=actor.id,
        ip_address=get_client_ip(request),
    )


@router.delete("/{database_id}", status_code=204)
async def delete_database(
    database_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("database:delete")),
) -> None:
    service = _build_service(db)
    await service.delete_database(database_id, actor_user_id=actor.id, ip_address=get_client_ip(request))


@router.post("/{database_id}/test-connection", response_model=TestConnectionResponse)
async def test_connection(
    database_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("database:edit")),
    connector: OracleConnector = Depends(get_oracle_connector),
) -> TestConnectionResponse:
    """Attempts a real connection with the stored credentials. Always 200 - success or
    failure is reported in the body, since a failed test is an expected diagnostic outcome,
    not a server error."""
    service = _build_capability_service(db, connector)
    result = await service.test_connection(database_id, actor_user_id=actor.id, ip_address=get_client_ip(request))
    return TestConnectionResponse(ok=result.ok, category=result.category, message=result.message)


@router.post("/{database_id}/detect-capabilities", response_model=CapabilityProfileResponse)
async def detect_capabilities(
    database_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("database:edit")),
    connector: OracleConnector = Depends(get_oracle_connector),
) -> DatabaseCapability:
    """Connects and probes only the Section 7 base-view allowlist - never Diagnostics/Tuning
    Pack-gated views. Raises 502 if the database can't be reached, since this is an active
    action expected to follow a successful test-connection."""
    service = _build_capability_service(db, connector)
    return await service.detect_capabilities(database_id, actor_user_id=actor.id, ip_address=get_client_ip(request))


@router.get("/{database_id}/capabilities", response_model=CapabilityProfileResponse)
async def get_capabilities(
    database_id: str, db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("database:view"))
) -> DatabaseCapability | CapabilityProfileResponse:
    """Returns the persisted profile, or an all-unknown one if it's never been probed -
    that's an honest, expected state, not a 404."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)  # 404s if the database itself doesn't exist

    capability = await CapabilityRepository(db).get_by_database_id(database_id)
    if capability is None:
        return CapabilityProfileResponse(
            detected_version=None,
            detected_edition=None,
            is_rac=None,
            statspack_available=None,
            diagnostics_tuning_pack_licensed_confirmed=False,
            privilege_report=None,
            last_probed_at=None,
            cpu_count=None,
        )
    return capability


@router.get("/{database_id}/collector-status", response_model=CollectorStatusResponse)
async def get_collector_status(
    database_id: str, db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("database:view"))
) -> CollectorStatusResponse:
    """Self-observability: has the scheduled collector run for this database, and is it
    succeeding? Returns an honest all-null profile if it has never run - not a 404."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)  # 404s if the database itself doesn't exist

    status_row = await CollectorStatusRepository(db).get_by_database_id(database_id)
    if status_row is None:
        return CollectorStatusResponse(
            last_run_at=None,
            last_success_at=None,
            last_failure_at=None,
            consecutive_failures=0,
            last_error_category=None,
            last_run_duration_ms=None,
            last_sql_run_at=None,
            last_sql_success_at=None,
            last_sql_error_category=None,
            last_capacity_run_at=None,
            last_capacity_success_at=None,
            last_capacity_error_category=None,
            last_statspack_run_at=None,
            last_statspack_success_at=None,
            last_statspack_error_category=None,
            last_config_run_at=None,
            last_config_success_at=None,
            last_config_error_category=None,
        )
    return CollectorStatusResponse(
        last_run_at=status_row.last_run_at,
        last_success_at=status_row.last_success_at,
        last_failure_at=status_row.last_failure_at,
        consecutive_failures=status_row.consecutive_failures,
        last_error_category=status_row.last_error_category,
        last_run_duration_ms=status_row.last_run_duration_ms,
        last_sql_run_at=status_row.last_sql_run_at,
        last_sql_success_at=status_row.last_sql_success_at,
        last_sql_error_category=status_row.last_sql_error_category,
        last_capacity_run_at=status_row.last_capacity_run_at,
        last_capacity_success_at=status_row.last_capacity_success_at,
        last_capacity_error_category=status_row.last_capacity_error_category,
        last_statspack_run_at=status_row.last_statspack_run_at,
        last_statspack_success_at=status_row.last_statspack_success_at,
        last_statspack_error_category=status_row.last_statspack_error_category,
        last_config_run_at=status_row.last_config_run_at,
        last_config_success_at=status_row.last_config_success_at,
        last_config_error_category=status_row.last_config_error_category,
    )


@router.get("/{database_id}/anomalies", response_model=list[AnomalyResponse])
async def list_anomalies(
    database_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> list[AnomalyResponse]:
    """Deterministic anomalies detected by AnalyticsService against each metric's rolling
    baseline. Each is correlated into an incident by IncidentService - see incident_id
    on the underlying row (not exposed here; use GET /api/incidents to browse incidents)."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)  # 404s if the database itself doesn't exist

    anomalies = await AnomalyRepository(db).list_recent(database_id, limit=limit)
    return [
        AnomalyResponse(
            id=a.id,
            detected_at=a.detected_at,
            metric_key=a.metric_key,
            observed_value=a.observed_value,
            baseline_mean=a.baseline_mean,
            baseline_stddev=a.baseline_stddev,
            sample_count=a.sample_count,
            severity=a.severity,
        )
        for a in anomalies
    ]


@router.get("/{database_id}/metrics/compare", response_model=MetricCompareResponse)
async def compare_metric_to_baseline(
    database_id: str,
    metric_key: str = Query(...),
    hour_of_day: int | None = Query(default=None, ge=0, le=23),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> MetricCompareResponse:
    """Current value of one metric vs. its rolling per-hour-of-day baseline - the same
    Welford mean/stddev and z-score classifier AnalyticsService judges real anomalies
    against, exposed as a live read for any metric_key regardless of whether it's anomalous
    right now (unlike GET .../anomalies, a historical snapshot attached only to detected
    anomalies). metric_key is a query param, not a path segment - a real Oracle wait class
    ("User I/O") can contain a literal "/", which a path segment can't carry."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    telemetry_repo = TelemetryRepository(db)
    current_metrics = await AnalyticsService().compute_current_metrics(telemetry_repo, database_id)
    observed_value = current_metrics.get(metric_key)
    if observed_value is None:
        return MetricCompareResponse(available=False, reason="No current telemetry for this metric yet.")

    hour = hour_of_day if hour_of_day is not None else utcnow().hour
    existing = await BaselineRepository(db).get(database_id, metric_key, hour)
    stats = BaselineStats(existing.sample_count, existing.mean, existing.m2) if existing else EMPTY_STATS
    z = z_score(stats, observed_value)

    return MetricCompareResponse(
        available=True,
        metric_key=metric_key,
        metric_label=humanize_metric_key(metric_key),
        hour_of_day=hour,
        observed_value=observed_value,
        baseline_mean=stats.mean if existing else None,
        baseline_stddev=stats.stddev if existing else None,
        sample_count=stats.sample_count,
        min_samples_for_anomaly=MIN_SAMPLES_FOR_ANOMALY,
        z_score=z,
        severity=classify_severity(z) if z is not None else None,
        baseline_updated_at=existing.updated_at if existing else None,
    )


@router.get("/{database_id}/sessions", response_model=SessionsSnapshotResponse)
async def get_sessions(
    database_id: str, db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("database:view"))
) -> SessionsSnapshotResponse:
    """The most recent V$SESSION sampling round - not a live query, whatever the collector
    last wrote (see collector-status for how fresh that is)."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    samples = await TelemetryRepository(db).recent_session_samples(database_id, limit=500)
    if not samples:
        return SessionsSnapshotResponse(available=False, reason="no session telemetry collected yet")

    current_round = latest_round(samples)
    return SessionsSnapshotResponse(
        available=True,
        sampled_at=current_round[0].sampled_at,
        session_count=len(current_round),
        sessions=[
            SessionResponse(
                sid=s.sid,
                serial_num=s.serial_num,
                username=s.username,
                sql_id=s.sql_id,
                event=s.event,
                wait_class=s.wait_class,
                status=s.status,
                blocking_session=s.blocking_session,
                machine=s.machine,
                program=s.program,
                module=s.module,
                action=s.action,
                state=s.state,
                last_call_et=s.last_call_et,
            )
            for s in current_round
        ],
    )


@router.get("/{database_id}/waits", response_model=WaitProfileResponse)
async def get_wait_profile_route(
    database_id: str, db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("database:view"))
) -> WaitProfileResponse:
    """Per-wait-class time-waited rate, computed from the two most recent wait_snapshots
    rounds - needs at least two collection rounds to compute a delta."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    rounds = await TelemetryRepository(db).latest_two_wait_rounds(database_id)
    if rounds is None:
        return WaitProfileResponse(
            available=False, reason="fewer than two collection rounds - no rate can be computed yet"
        )

    (t1, rows1), (t2, rows2) = rounds
    elapsed_seconds = (t2 - t1).total_seconds()
    rates = compute_wait_class_rates(aggregate_by_wait_class(rows1), aggregate_by_wait_class(rows2), elapsed_seconds)

    return WaitProfileResponse(
        available=True,
        sampled_at=t2,
        elapsed_seconds=elapsed_seconds,
        wait_classes=[
            WaitClassRate(wait_class=wc, centisec_waited_per_sec=round(rate, 2))
            for wc, rate in sorted(rates.items(), key=lambda kv: kv[1], reverse=True)
        ],
    )


@router.get("/{database_id}/blocking", response_model=BlockingSnapshotResponse)
async def get_blocking_route(
    database_id: str, db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("database:view"))
) -> BlockingSnapshotResponse:
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    samples = await TelemetryRepository(db).recent_session_samples(database_id, limit=500)
    if not samples:
        return BlockingSnapshotResponse(available=False, reason="no session telemetry collected yet")

    current_round = latest_round(samples)
    return BlockingSnapshotResponse(
        available=True,
        sampled_at=current_round[0].sampled_at,
        blocking_chains=[BlockingChain(**c) for c in build_blocking_chains(current_round)],
    )


@router.get("/{database_id}/health-history", response_model=list[InstanceHealthPointResponse])
async def get_health_history(
    database_id: str,
    hours: float = Query(default=1.0, gt=0, le=720),
    until: datetime | None = Query(default=None, description="Defaults to now - hours before this."),
    limit: int = Query(default=1000, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> list[InstanceHealthPointResponse]:
    """Instance health/active-session-count over time - the Performance page's primary
    timeline. Oldest first, so charting libraries don't need to reverse it. When the window
    holds more than `limit` samples, an even spread across the whole window is returned
    rather than only the most recent `limit` (see TelemetryRepository._even_sample)."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    anchor = until or utcnow()
    since = anchor - timedelta(hours=hours)
    points = await TelemetryRepository(db).recent_instance_health(database_id, limit=limit, since=since, until=until)
    return [
        InstanceHealthPointResponse(
            sampled_at=p.sampled_at,
            reachability=p.reachability,
            instance_status=p.instance_status,
            active_session_count=p.active_session_count,
        )
        for p in reversed(points)
    ]


@router.get("/{database_id}/waits/history", response_model=list[WaitClassHistoryPoint])
async def get_wait_history(
    database_id: str,
    hours: float = Query(default=1.0, gt=0, le=720),
    until: datetime | None = Query(default=None, description="Defaults to now - hours before this."),
    limit: int = Query(default=500, ge=2, le=5000),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> list[WaitClassHistoryPoint]:
    """Per-wait-class rate over time - the historical counterpart to /waits, which only ever
    computes one rate from the two most recent rounds. Same delta-of-cumulative-counters math
    as /waits (compute_wait_class_rates), walked across every consecutive round pair in the
    window instead of just the latest one. Oldest first. Empty list (not an `available`
    wrapper, matching /health-history's own convention) when fewer than two rounds exist."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    anchor = until or utcnow()
    since = anchor - timedelta(hours=hours)
    rounds = await TelemetryRepository(db).recent_wait_rounds(database_id, since=since, until=until, limit=limit)
    if len(rounds) < 2:
        return []

    points: list[WaitClassHistoryPoint] = []
    for (t_prev, rows_prev), (t_cur, rows_cur) in zip(rounds, rounds[1:]):
        elapsed_seconds = (t_cur - t_prev).total_seconds()
        rates = compute_wait_class_rates(
            aggregate_by_wait_class(rows_prev), aggregate_by_wait_class(rows_cur), elapsed_seconds
        )
        points.append(
            WaitClassHistoryPoint(
                sampled_at=t_cur,
                elapsed_seconds=elapsed_seconds,
                wait_classes=[
                    WaitClassRate(wait_class=wc, centisec_waited_per_sec=round(rate, 2))
                    for wc, rate in sorted(rates.items(), key=lambda kv: kv[1], reverse=True)
                ],
            )
        )
    return points


@router.get("/{database_id}/cpu/history", response_model=list[CpuHistoryPoint])
async def get_cpu_history(
    database_id: str,
    hours: float = Query(default=1.0, gt=0, le=720),
    until: datetime | None = Query(default=None, description="Defaults to now - hours before this."),
    limit: int = Query(default=500, ge=2, le=5000),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> list[CpuHistoryPoint]:
    """Database CPU consumption rate over time (Finding 7, docs/QA_LIVE_FINDINGS.md) - same
    delta-of-a-cumulative-counter math as /waits/history, over V$SYSSTAT's 'CPU used by this
    session' (instance-wide despite the name) instead of V$SYSTEM_EVENT. Reuses
    recent_instance_health directly - no dedicated repo method needed, since a rate here only
    needs each pair's own real elapsed time, correct regardless of how the round list was
    sampled. Oldest first. Empty list when fewer than two rounds (or fewer than two with a
    real CPU reading) exist."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    anchor = until or utcnow()
    since = anchor - timedelta(hours=hours)
    rows_desc = await TelemetryRepository(db).recent_instance_health(
        database_id, limit=limit, since=since, until=until
    )
    rows = list(reversed(rows_desc))  # oldest first, matching /health-history's own convention

    points: list[CpuHistoryPoint] = []
    for prev, cur in zip(rows, rows[1:]):
        if prev.cpu_centisecs_used is None or cur.cpu_centisecs_used is None:
            continue
        elapsed_seconds = (cur.sampled_at - prev.sampled_at).total_seconds()
        if elapsed_seconds <= 0:
            continue
        delta = cur.cpu_centisecs_used - prev.cpu_centisecs_used
        if delta < 0:
            continue  # counter reset/reload - same guard as compute_sql_deltas
        points.append(
            CpuHistoryPoint(
                sampled_at=cur.sampled_at,
                elapsed_seconds=elapsed_seconds,
                cpu_centisec_used_per_sec=round(delta / elapsed_seconds, 2),
            )
        )
    return points


@router.get("/{database_id}/top-sql", response_model=TopSqlResponse)
async def get_top_sql_route(
    database_id: str,
    sort_by: SqlSortKey = Query(default="elapsed_time"),
    limit: int = Query(default=25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> TopSqlResponse:
    """Top SQL by per-interval delta between the two most recent SQL collection rounds
    (master plan Phase 9) - never a live V$SQL query. The SQL collector runs on its own,
    slower interval (SQL_COLLECTOR_INTERVAL_SECONDS, 300s by default), so `sampled_at` here
    will normally lag well behind the Sessions/Performance pages' 15s telemetry - that's
    expected, not staleness."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    sql_repo = SqlTelemetryRepository(db)
    rounds = await sql_repo.latest_two_sql_rounds(database_id)
    if rounds is None:
        return TopSqlResponse(
            available=False,
            reason="fewer than two SQL collection rounds - no per-interval delta can be computed yet",
            sort_by=sort_by,
        )

    (t1, rows1), (t2, rows2) = rounds
    elapsed_seconds = (t2 - t1).total_seconds()
    deltas = rank_sql_deltas(compute_sql_deltas(rows1, rows2, elapsed_seconds), sort_by=sort_by, limit=limit)

    sql_ids = [d.sql_id for d in deltas]
    statements = await sql_repo.statements_for_sql_ids(database_id, sql_ids)
    plan_counts = await sql_repo.plan_counts_for_sql_ids(database_id, sql_ids)
    open_regressions = {r.sql_id: r for r in await sql_repo.list_regressions(database_id, status="OPEN", limit=200)}

    rows = []
    for d in deltas:
        stmt = statements.get(d.sql_id)
        reg = open_regressions.get(d.sql_id)
        rows.append(
            TopSqlRow(
                sql_id=d.sql_id,
                plan_hash_value=d.plan_hash_value,
                sql_text=stmt.sql_text if stmt else None,
                sql_text_truncated=stmt.sql_text_truncated if stmt else False,
                parsing_schema_name=stmt.parsing_schema_name if stmt else None,
                child_cursor_count=d.child_cursor_count,
                executions_delta=d.executions,
                elapsed_time_us_delta=d.elapsed_time_us,
                cpu_time_us_delta=d.cpu_time_us,
                buffer_gets_delta=d.buffer_gets,
                disk_reads_delta=d.disk_reads,
                rows_processed_delta=d.rows_processed,
                elapsed_per_exec_us=d.elapsed_per_exec_us,
                elapsed_time_us_per_sec=d.rate_per_second("elapsed_time_us", elapsed_seconds),
                plan_count=plan_counts.get(d.sql_id, 0),
                open_regression_kind=reg.regression_kind if reg else None,
                open_regression_severity=reg.severity if reg else None,
            )
        )

    return TopSqlResponse(
        available=True,
        sampled_at=t2,
        previous_sampled_at=t1,
        elapsed_seconds=elapsed_seconds,
        sort_by=sort_by,
        rows=rows,
    )


@router.get("/{database_id}/sql/{sql_id}", response_model=SqlDetailResponse)
async def get_sql_detail_route(
    database_id: str,
    sql_id: str = Path(min_length=1, max_length=20, pattern=r"^[0-9a-zA-Z]+$"),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> SqlDetailResponse:
    """One statement's identity, its captured plan history, and recent sessions that ran it
    - the last joined from session_samples.sql_id, already collected by the main 15s
    collector, so no extra Oracle query is needed for it. Never a live Oracle query on read."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    sql_repo = SqlTelemetryRepository(db)
    statement = await sql_repo.get_statement(database_id, sql_id)
    if statement is None:
        return SqlDetailResponse(
            available=False, reason="no SQL telemetry collected for this statement yet", sql_id=sql_id
        )

    snapshots = await sql_repo.list_plan_snapshots(database_id, sql_id)

    latest_sample = await sql_repo.latest_stat_sample(database_id, sql_id)
    current_plan_hash = latest_sample.plan_hash_value if latest_sample else None

    plans = [
        SqlPlanSummary(
            plan_hash_value=s.plan_hash_value,
            first_seen_at=s.first_seen_at,
            last_seen_at=s.last_seen_at,
            plan_line_count=s.plan_line_count,
            is_current=(s.plan_hash_value == current_plan_hash),
        )
        for s in snapshots
    ]

    recent_samples = await TelemetryRepository(db).recent_session_samples(database_id, limit=500)
    recent_sessions = [
        SqlSessionReference(
            sampled_at=s.sampled_at,
            sid=s.sid,
            username=s.username,
            program=s.program,
            module=s.module,
            event=s.event,
            wait_class=s.wait_class,
        )
        for s in recent_samples
        if s.sql_id == sql_id
    ][:20]

    open_regressions = [
        SqlRegressionResponse(
            id=r.id,
            sql_id=r.sql_id,
            sql_text=statement.sql_text,
            regression_kind=r.regression_kind,
            severity=r.severity,
            status=r.status,
            first_detected_at=r.first_detected_at,
            last_detected_at=r.last_detected_at,
            resolved_at=r.resolved_at,
            baseline_elapsed_per_exec_us=r.baseline_elapsed_per_exec_us,
            current_elapsed_per_exec_us=r.current_elapsed_per_exec_us,
            ratio=r.ratio,
            baseline_executions=r.baseline_executions,
            current_executions=r.current_executions,
            baseline_plan_hash_value=r.baseline_plan_hash_value,
            current_plan_hash_value=r.current_plan_hash_value,
            detection_note=r.detection_note,
        )
        for r in await sql_repo.list_regressions(database_id, status="OPEN", limit=200)
        if r.sql_id == sql_id
    ]

    return SqlDetailResponse(
        available=True,
        sql_id=statement.sql_id,
        sql_text=statement.sql_text,
        sql_text_truncated=statement.sql_text_truncated,
        parsing_schema_name=statement.parsing_schema_name,
        first_seen_at=statement.first_seen_at,
        last_seen_at=statement.last_seen_at,
        plans=plans,
        recent_sessions=recent_sessions,
        open_regressions=open_regressions,
    )


@router.get("/{database_id}/sql/{sql_id}/plan/{plan_hash_value}", response_model=SqlPlanResponse)
async def get_sql_plan_route(
    database_id: str,
    sql_id: str = Path(min_length=1, max_length=20, pattern=r"^[0-9a-zA-Z]+$"),
    plan_hash_value: int = Path(ge=0),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> SqlPlanResponse:
    """Served from the persisted plan snapshot captured when this plan was first observed -
    never a live V$SQL_PLAN query. A plan remains viewable here after its real cursor has
    aged out of Oracle's shared pool, which a live query could not do."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    snapshot = await SqlTelemetryRepository(db).get_plan_snapshot(database_id, sql_id, plan_hash_value)
    if snapshot is None:
        return SqlPlanResponse(
            available=False, reason="no plan snapshot captured for this (sql_id, plan_hash_value) yet"
        )

    return SqlPlanResponse(
        available=True,
        sql_id=snapshot.sql_id,
        plan_hash_value=snapshot.plan_hash_value,
        first_seen_at=snapshot.first_seen_at,
        last_seen_at=snapshot.last_seen_at,
        plan_lines=[SqlPlanLine(**line) for line in snapshot.plan_lines],
    )


@router.get("/{database_id}/sql-regressions", response_model=list[SqlRegressionResponse])
async def list_sql_regressions_route(
    database_id: str,
    status: Literal["OPEN", "RESOLVED"] | None = Query(default="OPEN"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> list[SqlRegressionResponse]:
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    sql_repo = SqlTelemetryRepository(db)
    events = await sql_repo.list_regressions(database_id, status=status, limit=limit)
    statements = await sql_repo.statements_for_sql_ids(database_id, [e.sql_id for e in events])

    results = []
    for e in events:
        stmt = statements.get(e.sql_id)
        results.append(
            SqlRegressionResponse(
                id=e.id,
                sql_id=e.sql_id,
                sql_text=stmt.sql_text if stmt else None,
                regression_kind=e.regression_kind,
                severity=e.severity,
                status=e.status,
                first_detected_at=e.first_detected_at,
                last_detected_at=e.last_detected_at,
                resolved_at=e.resolved_at,
                baseline_elapsed_per_exec_us=e.baseline_elapsed_per_exec_us,
                current_elapsed_per_exec_us=e.current_elapsed_per_exec_us,
                ratio=e.ratio,
                baseline_executions=e.baseline_executions,
                current_executions=e.current_executions,
                baseline_plan_hash_value=e.baseline_plan_hash_value,
                current_plan_hash_value=e.current_plan_hash_value,
                detection_note=e.detection_note,
            )
        )
    return results


_ALERT_SEVERITY_RANK = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def _best_open_alert(alerts: list[CapacityAlertEvent]) -> CapacityAlertEvent | None:
    """Picks the single worst-severity open alert for a tablespace's summary row - a
    tablespace can have both an open PCT_USED and PROJECTED_FULL alert at once (they're
    independent, non-suppressing kinds - see app/analytics/capacity_alerts.py), so the
    overview table shows the more urgent one rather than an arbitrary pick."""
    if not alerts:
        return None
    return max(alerts, key=lambda a: _ALERT_SEVERITY_RANK.get(a.severity, 0))


def _tablespace_usage_row(
    usage: TablespaceUsage, status: str | None, trend: GrowthTrend, open_alert: CapacityAlertEvent | None
) -> TablespaceUsageRow:
    return TablespaceUsageRow(
        tablespace_name=usage.tablespace_name,
        contents=usage.contents,
        status=status,
        allocated_bytes=usage.allocated_bytes,
        used_bytes=usage.used_bytes,
        max_bytes=usage.max_bytes,
        file_count=usage.file_count,
        pct_used=usage.pct_used,
        pct_of_max=usage.pct_of_max,
        growth_available=trend.available,
        growth_reason=trend.reason,
        growth_bytes_per_day=trend.bytes_per_day,
        projected_days_to_full=trend.projected_days_to_full,
        projection_reason=trend.projection_reason,
        open_alert_kind=open_alert.alert_kind if open_alert else None,
        open_alert_severity=open_alert.severity if open_alert else None,
    )


@router.get("/{database_id}/capacity", response_model=CapacityOverviewResponse)
async def get_capacity_route(
    database_id: str,
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> CapacityOverviewResponse:
    """Current per-tablespace usage plus growth trend (master plan Phase 5, Enterprise
    Readiness Plan D.2) - never a live Oracle query on read. Unlike top-sql, a single
    collection round is enough for `available=True`: %used is a snapshot fact (used/
    allocated), not a delta between cumulative counters - only the per-row growth trend
    needs multiple rounds, and reports its own honest unavailability when it lacks them."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    capacity_repo = CapacityRepository(db)
    latest = await capacity_repo.latest_round(database_id)
    if latest is None:
        return CapacityOverviewResponse(
            available=False, reason="no capacity telemetry collected yet for this database"
        )

    sampled_at, current_rows = latest
    now = utcnow()
    open_alerts_by_tablespace: dict[str, list] = {}
    for alert in await capacity_repo.list_alerts(database_id, status="OPEN", limit=200):
        open_alerts_by_tablespace.setdefault(alert.tablespace_name, []).append(alert)

    rows = []
    for row in current_rows:
        # cast, not a real type mismatch: TablespaceSample satisfies TablespaceSampleLike's
        # Protocol at runtime (same fields, same types) - mypy's SQLAlchemy plugin just
        # doesn't reconcile Mapped[int]/Mapped[str] against a Protocol parameter cleanly
        # (same quirk app/services/capacity_alert_service.py already documents).
        usage = describe_usage(cast(TablespaceSampleLike, row))
        history = await capacity_repo.samples_in_window(
            database_id, row.tablespace_name, now - GROWTH_TREND_WINDOW, now
        )
        trend = compute_growth_trend(
            cast(Sequence[TablespaceSampleLike], history),
            now,
            current_used_bytes=row.used_bytes,
            max_bytes=row.max_bytes,
        )
        best_alert = _best_open_alert(open_alerts_by_tablespace.get(row.tablespace_name, []))
        rows.append(_tablespace_usage_row(usage, row.status, trend, best_alert))

    return CapacityOverviewResponse(available=True, sampled_at=sampled_at, rows=rows)


@router.get("/{database_id}/capacity/{tablespace_name}", response_model=TablespaceDetailResponse)
async def get_tablespace_detail_route(
    database_id: str,
    tablespace_name: str = Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_$#]+$"),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> TablespaceDetailResponse:
    """One tablespace's current usage, growth trend, recent history, and open alerts. Served
    entirely from persisted samples - never a live Oracle query on read."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    capacity_repo = CapacityRepository(db)
    latest = await capacity_repo.latest_round(database_id)
    current_row = None
    if latest is not None:
        _, current_rows = latest
        current_row = next((r for r in current_rows if r.tablespace_name == tablespace_name), None)
    if current_row is None:
        return TablespaceDetailResponse(
            available=False,
            reason="no capacity telemetry collected for this tablespace yet",
            tablespace_name=tablespace_name,
        )

    now = utcnow()
    # A longer window than GROWTH_TREND_WINDOW for the displayed history table - the trend
    # function filters to its own window internally, so passing a superset here is safe.
    history_rows = await capacity_repo.samples_in_window(
        database_id, tablespace_name, now - timedelta(days=30), now
    )
    usage = describe_usage(cast(TablespaceSampleLike, current_row))
    trend = compute_growth_trend(
        cast(Sequence[TablespaceSampleLike], history_rows),
        now,
        current_used_bytes=current_row.used_bytes,
        max_bytes=current_row.max_bytes,
    )
    open_alerts = [
        a for a in await capacity_repo.list_alerts(database_id, status="OPEN", limit=200)
        if a.tablespace_name == tablespace_name
    ]
    best_alert = _best_open_alert(open_alerts)

    return TablespaceDetailResponse(
        available=True,
        tablespace_name=current_row.tablespace_name,
        contents=current_row.contents,
        status=current_row.status,
        usage=_tablespace_usage_row(usage, current_row.status, trend, best_alert),
        history=[
            TablespaceHistoryPoint(
                sampled_at=h.sampled_at, allocated_bytes=h.allocated_bytes, used_bytes=h.used_bytes, max_bytes=h.max_bytes
            )
            for h in history_rows
        ],
        open_alerts=[
            CapacityAlertResponse(
                id=a.id,
                tablespace_name=a.tablespace_name,
                alert_kind=a.alert_kind,
                severity=a.severity,
                status=a.status,
                first_detected_at=a.first_detected_at,
                last_detected_at=a.last_detected_at,
                resolved_at=a.resolved_at,
                pct_used=a.pct_used,
                projected_days_to_full=a.projected_days_to_full,
                current_used_bytes=a.current_used_bytes,
                current_allocated_bytes=a.current_allocated_bytes,
                current_max_bytes=a.current_max_bytes,
                detection_note=a.detection_note,
            )
            for a in open_alerts
        ],
    )


@router.get("/{database_id}/capacity-alerts", response_model=list[CapacityAlertResponse])
async def list_capacity_alerts_route(
    database_id: str,
    status: Literal["OPEN", "RESOLVED"] | None = Query(default="OPEN"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> list[CapacityAlertResponse]:
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    capacity_repo = CapacityRepository(db)
    events = await capacity_repo.list_alerts(database_id, status=status, limit=limit)
    return [
        CapacityAlertResponse(
            id=e.id,
            tablespace_name=e.tablespace_name,
            alert_kind=e.alert_kind,
            severity=e.severity,
            status=e.status,
            first_detected_at=e.first_detected_at,
            last_detected_at=e.last_detected_at,
            resolved_at=e.resolved_at,
            pct_used=e.pct_used,
            pct_basis=e.pct_basis,
            projected_days_to_full=e.projected_days_to_full,
            current_used_bytes=e.current_used_bytes,
            current_allocated_bytes=e.current_allocated_bytes,
            current_max_bytes=e.current_max_bytes,
            detection_note=e.detection_note,
        )
        for e in events
    ]


def _capacity_thresholds_response(database: MonitoredDatabase) -> CapacityThresholdsResponse:
    settings = get_settings()
    return CapacityThresholdsResponse(
        warn_pct_used=database.capacity_warn_pct_used,
        critical_pct_used=database.capacity_critical_pct_used,
        warn_days_to_full=database.capacity_warn_days_to_full,
        critical_days_to_full=database.capacity_critical_days_to_full,
        default_warn_pct_used=settings.capacity_warn_pct_used,
        default_critical_pct_used=settings.capacity_critical_pct_used,
        default_warn_days_to_full=settings.capacity_warn_days_to_full,
        default_critical_days_to_full=settings.capacity_critical_days_to_full,
    )


@router.get("/{database_id}/capacity-thresholds", response_model=CapacityThresholdsResponse)
async def get_capacity_thresholds_route(
    database_id: str,
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> CapacityThresholdsResponse:
    """Per-database overrides of the global warn/critical thresholds (app/config.py) - null
    fields mean "using the global default," returned alongside the actual default values so
    the UI can show what a cleared override reverts to without hardcoding its own copy."""
    database_service = _build_service(db)
    database = await database_service.get_database(database_id)
    return _capacity_thresholds_response(database)


@router.put("/{database_id}/capacity-thresholds", response_model=CapacityThresholdsResponse)
async def set_capacity_thresholds_route(
    database_id: str,
    payload: CapacityThresholdsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("database:edit")),
) -> CapacityThresholdsResponse:
    """Every field is always applied, None included - PUT (not PATCH) semantics, since
    clearing a field back to the global default is exactly as valid an action as setting it."""
    database_service = _build_service(db)
    database = await database_service.set_capacity_thresholds(
        database_id,
        warn_pct_used=payload.warn_pct_used,
        critical_pct_used=payload.critical_pct_used,
        warn_days_to_full=payload.warn_days_to_full,
        critical_days_to_full=payload.critical_days_to_full,
        actor_user_id=actor.id,
        ip_address=get_client_ip(request),
    )
    return _capacity_thresholds_response(database)


@router.get("/{database_id}/statspack", response_model=StatspackSummaryResponse)
async def get_statspack_summary_route(
    database_id: str,
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> StatspackSummaryResponse:
    """Top wait-class and top-SQL deltas between the two most recently synced Statspack
    snapshots (master plan Phase 11, Enterprise Readiness Plan D.3) - never a live Oracle
    query on read, and never attempted at all for the large majority of databases that don't
    have Statspack (that's an honest `available: false`, not an error)."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    capability = await CapabilityRepository(db).get_by_database_id(database_id)
    if not capability or not capability.statspack_available:
        return StatspackSummaryResponse(
            available=False, reason="Statspack is not installed on this database"
        )

    statspack_repo = StatspackRepository(db)
    rounds = await statspack_repo.latest_two_synced_snapshots(database_id)
    if rounds is None:
        if await statspack_repo.has_any_snapshots(database_id):
            reason = "only one Statspack snapshot has been synced so far - a delta needs at least two"
        else:
            reason = "Statspack is installed, but no snapshots have been synced yet"
        return StatspackSummaryResponse(available=False, reason=reason)

    from_snap, to_snap = rounds
    elapsed_seconds = (to_snap.snap_time - from_snap.snap_time).total_seconds()

    previous_waits = await statspack_repo.wait_event_samples_for_snapshot(database_id, from_snap.snap_id)
    current_waits = await statspack_repo.wait_event_samples_for_snapshot(database_id, to_snap.snap_id)
    # cast, not a real type mismatch: StatspackWaitEventSample satisfies WaitRow's Protocol
    # at runtime (same fields, same types) - same documented mypy+SQLAlchemy quirk as
    # app/services/capacity_alert_service.py.
    wait_rates = compute_wait_class_rates(
        aggregate_by_wait_class(cast(list[WaitRow], previous_waits)),
        aggregate_by_wait_class(cast(list[WaitRow], current_waits)),
        elapsed_seconds,
    )
    wait_classes = [
        StatspackWaitClassRate(wait_class=wc, centisec_waited_per_hour=round(rate * 3600, 2))
        for wc, rate in sorted(wait_rates.items(), key=lambda kv: kv[1], reverse=True)
    ]

    previous_sql = await statspack_repo.sql_samples_for_snapshot(database_id, from_snap.snap_id)
    current_sql = await statspack_repo.sql_samples_for_snapshot(database_id, to_snap.snap_id)
    sql_deltas = rank_sql_deltas(
        compute_sql_deltas(
            cast(Sequence[SqlStatRowLike], adapt_sql_samples(previous_sql)),
            cast(Sequence[SqlStatRowLike], adapt_sql_samples(current_sql)),
            elapsed_seconds,
        ),
        sort_by="elapsed_time",
        limit=25,
    )
    statements = await SqlTelemetryRepository(db).statements_for_sql_ids(
        database_id, [d.sql_id for d in sql_deltas]
    )
    top_sql = [
        StatspackSqlRow(
            sql_id=d.sql_id,
            plan_hash_value=d.plan_hash_value,
            sql_text=statements[d.sql_id].sql_text if d.sql_id in statements else None,
            executions_delta=d.executions,
            elapsed_time_us_delta=d.elapsed_time_us,
            elapsed_per_exec_us=d.elapsed_per_exec_us,
        )
        for d in sql_deltas
    ]

    return StatspackSummaryResponse(
        available=True,
        from_snapshot=StatspackSnapshotRef(snap_id=from_snap.snap_id, snap_time=from_snap.snap_time),
        to_snapshot=StatspackSnapshotRef(snap_id=to_snap.snap_id, snap_time=to_snap.snap_time),
        elapsed_hours=round(elapsed_seconds / 3600.0, 2) if elapsed_seconds > 0 else 0.0,
        wait_classes=wait_classes,
        top_sql=top_sql,
    )


@router.get("/{database_id}/configuration", response_model=ConfigurationSummaryResponse)
async def get_configuration_summary(
    database_id: str,
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> ConfigurationSummaryResponse:
    """Configuration parameter drift between the two most recently collected rounds (master
    plan Phase 14, Enterprise Readiness Plan D.4) - never a live Oracle query on read. Honestly
    `available: false` until at least two collection rounds exist, never an error."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    config_repo = ConfigTelemetryRepository(db)
    rounds = await config_repo.latest_two_rounds(database_id)
    if rounds is None:
        if await config_repo.has_any_samples(database_id):
            reason = "only one configuration collection round exists so far - a diff needs at least two"
        else:
            reason = "no configuration telemetry has been collected yet"
        return ConfigurationSummaryResponse(available=False, reason=reason)

    (from_sampled_at, previous_rows), (to_sampled_at, current_rows) = rounds
    changes = compute_parameter_changes(previous_rows, current_rows)

    return ConfigurationSummaryResponse(
        available=True,
        from_sampled_at=from_sampled_at,
        to_sampled_at=to_sampled_at,
        changes=[
            ParameterChangeItem(
                parameter_name=c.parameter_name,
                previous_value=c.previous_value,
                current_value=c.current_value,
                change_type=c.change_type,
            )
            for c in changes
        ],
    )


@router.get("/{database_id}/configuration/history", response_model=list[ConfigChangeEvent])
async def get_configuration_history(
    database_id: str,
    hours: float = Query(default=1.0, gt=0, le=720),
    until: datetime | None = Query(default=None, description="Defaults to now - hours before this."),
    limit: int = Query(default=200, ge=2, le=1000),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("database:view")),
) -> list[ConfigChangeEvent]:
    """The real history behind ConfigurationSummaryResponse's always-just-the-latest-two-
    rounds view: walks every consecutive round pair in the window (same compute_parameter_
    changes diff, reused - never reimplemented), so a change that happened and reverted
    between two older rounds still shows up as two separate events instead of silently
    cancelling out. Most recent first. Empty list when fewer than two rounds exist in the
    window, matching /configuration's own "not an error" convention."""
    database_service = _build_service(db)
    await database_service.get_database(database_id)

    config_repo = ConfigTelemetryRepository(db)
    anchor = until or utcnow()
    since = anchor - timedelta(hours=hours)
    rounds = await config_repo.recent_rounds(database_id, since=since, until=until, limit=limit)
    if len(rounds) < 2:
        return []

    events: list[ConfigChangeEvent] = []
    for (_, prev_rows), (curr_ts, curr_rows) in zip(rounds, rounds[1:]):
        for c in compute_parameter_changes(prev_rows, curr_rows):
            events.append(
                ConfigChangeEvent(
                    detected_at=curr_ts,
                    parameter_name=c.parameter_name,
                    previous_value=c.previous_value,
                    current_value=c.current_value,
                    change_type=c.change_type,
                )
            )
    events.sort(key=lambda e: e.detected_at, reverse=True)
    return events
