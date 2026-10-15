from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import ApiModel

SqlSortKey = Literal["elapsed_time", "cpu_time", "buffer_gets", "disk_reads", "executions", "elapsed_per_exec"]


class CollectorStatusResponse(ApiModel):
    last_run_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    consecutive_failures: int
    last_error_category: str | None
    last_run_duration_ms: int | None
    # SQL Intelligence (master plan Phase 9) runs as its own collector job, own interval -
    # own self-observability fields, separate from the main collector's above.
    last_sql_run_at: datetime | None = None
    last_sql_success_at: datetime | None = None
    last_sql_error_category: str | None = None
    # Capacity collector (master plan Phase 5, Enterprise Readiness Plan D.2) - also its own
    # collector job on its own interval, own self-observability fields.
    last_capacity_run_at: datetime | None = None
    last_capacity_success_at: datetime | None = None
    last_capacity_error_category: str | None = None
    # Statspack integration (master plan Phase 11, Enterprise Readiness Plan D.3) - also its
    # own sync job on its own interval, own self-observability fields.
    last_statspack_run_at: datetime | None = None
    last_statspack_success_at: datetime | None = None
    last_statspack_error_category: str | None = None
    # Change Intelligence (master plan Phase 14, Enterprise Readiness Plan D.4) - also its
    # own collector job on its own interval, own self-observability fields.
    last_config_run_at: datetime | None = None
    last_config_success_at: datetime | None = None
    last_config_error_category: str | None = None


class AnomalyResponse(ApiModel):
    id: str
    detected_at: datetime
    metric_key: str
    observed_value: float
    baseline_mean: float
    baseline_stddev: float
    sample_count: int
    severity: str


class MetricCompareResponse(ApiModel):
    """Live "current value vs. this hour's rolling baseline" read for any metric_key -
    unlike AnomalyResponse (a historical snapshot attached only to detected anomalies), this
    is available whether or not the metric is currently anomalous."""

    available: bool
    reason: str | None = None
    metric_key: str | None = None
    metric_label: str | None = None
    hour_of_day: int | None = None
    observed_value: float | None = None
    baseline_mean: float | None = None
    baseline_stddev: float | None = None
    sample_count: int = 0
    min_samples_for_anomaly: int = 0
    z_score: float | None = None
    severity: str | None = None
    baseline_updated_at: datetime | None = None


class SessionResponse(ApiModel):
    sid: int
    serial_num: int | None
    username: str | None
    sql_id: str | None
    event: str | None
    wait_class: str | None
    status: str | None
    blocking_session: int | None
    machine: str | None
    program: str | None
    module: str | None
    action: str | None
    state: str | None = None
    last_call_et: int | None = None


class SessionsSnapshotResponse(ApiModel):
    available: bool
    reason: str | None = None
    sampled_at: datetime | None = None
    session_count: int = 0
    sessions: list[SessionResponse] = []


class WaitClassRate(ApiModel):
    wait_class: str
    centisec_waited_per_sec: float


class WaitProfileResponse(ApiModel):
    available: bool
    reason: str | None = None
    sampled_at: datetime | None = None
    elapsed_seconds: float | None = None
    wait_classes: list[WaitClassRate] = []


class WaitClassHistoryPoint(ApiModel):
    """One round's per-wait-class rate, computed against the round before it - the historical
    counterpart to WaitProfileResponse, which only ever holds the single latest round."""

    sampled_at: datetime
    elapsed_seconds: float
    wait_classes: list[WaitClassRate]


class CpuHistoryPoint(ApiModel):
    """One round's CPU-consumption rate, computed against the round before it - same shape as
    WaitClassHistoryPoint, one metric instead of a per-wait-class breakdown (Finding 7,
    docs/QA_LIVE_FINDINGS.md)."""

    sampled_at: datetime
    elapsed_seconds: float
    cpu_centisec_used_per_sec: float


class BlockingChain(ApiModel):
    blocked_sid: int
    blocked_username: str | None
    blocked_sql_id: str | None
    blocker_sid: int
    blocker_username: str | None
    blocker_sql_id: str | None
    blocker_in_this_sample: bool


class BlockingSnapshotResponse(ApiModel):
    available: bool
    reason: str | None = None
    sampled_at: datetime | None = None
    blocking_chains: list[BlockingChain] = []


class InstanceHealthPointResponse(ApiModel):
    sampled_at: datetime
    reachability: str
    instance_status: str | None
    active_session_count: int | None


class TopSqlRow(ApiModel):
    sql_id: str
    plan_hash_value: int
    sql_text: str | None
    sql_text_truncated: bool
    parsing_schema_name: str | None
    child_cursor_count: int
    executions_delta: int
    elapsed_time_us_delta: int
    cpu_time_us_delta: int
    buffer_gets_delta: int
    disk_reads_delta: int
    rows_processed_delta: int
    elapsed_per_exec_us: float | None
    elapsed_time_us_per_sec: float
    plan_count: int
    open_regression_kind: str | None = None
    open_regression_severity: str | None = None


class TopSqlResponse(ApiModel):
    available: bool
    reason: str | None = None
    sampled_at: datetime | None = None
    previous_sampled_at: datetime | None = None
    elapsed_seconds: float | None = None
    sort_by: str = "elapsed_time"
    rows: list[TopSqlRow] = []


class SqlPlanLine(ApiModel):
    id: int
    parent_id: int | None
    depth: int | None
    position: int | None
    operation: str | None
    options: str | None
    object_owner: str | None
    object_name: str | None
    object_type: str | None
    cost: int | None
    cardinality: int | None
    bytes: int | None


class SqlPlanResponse(ApiModel):
    available: bool
    reason: str | None = None
    sql_id: str | None = None
    plan_hash_value: int | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    plan_lines: list[SqlPlanLine] = []


class SqlPlanSummary(ApiModel):
    plan_hash_value: int
    first_seen_at: datetime
    last_seen_at: datetime
    plan_line_count: int
    is_current: bool


class SqlSessionReference(ApiModel):
    sampled_at: datetime
    sid: int
    username: str | None
    program: str | None
    module: str | None
    event: str | None
    wait_class: str | None


class SqlRegressionResponse(ApiModel):
    id: str
    sql_id: str
    sql_text: str | None
    regression_kind: str
    severity: str
    status: str
    first_detected_at: datetime
    last_detected_at: datetime
    resolved_at: datetime | None
    baseline_elapsed_per_exec_us: float | None
    current_elapsed_per_exec_us: float | None
    ratio: float | None
    baseline_executions: int | None
    current_executions: int | None
    baseline_plan_hash_value: int | None
    current_plan_hash_value: int | None
    detection_note: str


class SqlDetailResponse(ApiModel):
    available: bool
    reason: str | None = None
    sql_id: str | None = None
    sql_text: str | None = None
    sql_text_truncated: bool = False
    parsing_schema_name: str | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    plans: list[SqlPlanSummary] = []
    recent_sessions: list[SqlSessionReference] = []
    open_regressions: list[SqlRegressionResponse] = []


# -- Capacity/tablespace collector (master plan Phase 5, Enterprise Readiness Plan D.2) -----


class TablespaceUsageRow(ApiModel):
    tablespace_name: str
    contents: str
    status: str | None
    allocated_bytes: int
    used_bytes: int
    max_bytes: int | None
    file_count: int
    pct_used: float
    pct_of_max: float | None
    growth_available: bool
    growth_reason: str | None = None
    growth_bytes_per_day: float | None = None
    projected_days_to_full: float | None = None
    projection_reason: str | None = None
    open_alert_kind: str | None = None
    open_alert_severity: str | None = None


class CapacityOverviewResponse(ApiModel):
    available: bool
    reason: str | None = None
    sampled_at: datetime | None = None
    rows: list[TablespaceUsageRow] = []


class CapacityAlertResponse(ApiModel):
    id: str
    tablespace_name: str
    alert_kind: str
    severity: str
    status: str
    first_detected_at: datetime
    last_detected_at: datetime
    resolved_at: datetime | None
    pct_used: float | None
    pct_basis: str | None = None
    projected_days_to_full: float | None
    current_used_bytes: int | None
    current_allocated_bytes: int | None
    current_max_bytes: int | None
    detection_note: str


class TablespaceHistoryPoint(ApiModel):
    sampled_at: datetime
    allocated_bytes: int
    used_bytes: int
    max_bytes: int | None


class TablespaceDetailResponse(ApiModel):
    available: bool
    reason: str | None = None
    tablespace_name: str | None = None
    contents: str | None = None
    status: str | None = None
    usage: TablespaceUsageRow | None = None
    history: list[TablespaceHistoryPoint] = []
    open_alerts: list[CapacityAlertResponse] = []


class CapacityThresholdsRequest(ApiModel):
    """Every field is always applied on write, None included - explicitly clearing an
    override back to the global default (app/config.py) is a first-class action here, not
    just setting a new number. Cross-field ordering is only checked when both members of a
    pair are given together in the same request; a lone field is trusted against whichever
    global default currently holds the other side of its pair."""

    warn_pct_used: float | None = Field(default=None, ge=0, le=100)
    critical_pct_used: float | None = Field(default=None, ge=0, le=100)
    warn_days_to_full: float | None = Field(default=None, gt=0)
    critical_days_to_full: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check_ordering(self) -> "CapacityThresholdsRequest":
        if (
            self.warn_pct_used is not None
            and self.critical_pct_used is not None
            and self.warn_pct_used >= self.critical_pct_used
        ):
            raise ValueError("warn_pct_used must be lower than critical_pct_used")
        if (
            self.warn_days_to_full is not None
            and self.critical_days_to_full is not None
            and self.warn_days_to_full <= self.critical_days_to_full
        ):
            raise ValueError("warn_days_to_full must be greater than critical_days_to_full")
        return self


class CapacityThresholdsResponse(ApiModel):
    warn_pct_used: float | None
    critical_pct_used: float | None
    warn_days_to_full: float | None
    critical_days_to_full: float | None
    default_warn_pct_used: float
    default_critical_pct_used: float
    default_warn_days_to_full: float
    default_critical_days_to_full: float


# -- Statspack integration (master plan Phase 11, Enterprise Readiness Plan D.3) ------------


class StatspackSnapshotRef(ApiModel):
    snap_id: int
    snap_time: datetime


class StatspackWaitClassRate(ApiModel):
    wait_class: str
    centisec_waited_per_hour: float


class StatspackSqlRow(ApiModel):
    sql_id: str
    plan_hash_value: int
    sql_text: str | None
    executions_delta: int
    elapsed_time_us_delta: int
    elapsed_per_exec_us: float | None


class StatspackSummaryResponse(ApiModel):
    available: bool
    reason: str | None = None
    from_snapshot: StatspackSnapshotRef | None = None
    to_snapshot: StatspackSnapshotRef | None = None
    elapsed_hours: float | None = None
    wait_classes: list[StatspackWaitClassRate] = []
    top_sql: list[StatspackSqlRow] = []


# -- Change Intelligence (master plan Phase 14, Enterprise Readiness Plan D.4) --------------


class ParameterChangeItem(ApiModel):
    parameter_name: str
    previous_value: str | None
    current_value: str | None
    change_type: str  # newly_set | changed | reverted_to_default


class ConfigurationSummaryResponse(ApiModel):
    available: bool
    reason: str | None = None
    from_sampled_at: datetime | None = None
    to_sampled_at: datetime | None = None
    changes: list[ParameterChangeItem] = []


class ConfigChangeEvent(ApiModel):
    """One detected transition, walked across every consecutive round pair in a window -
    unlike ConfigurationSummaryResponse (always just the latest two rounds), this is the
    real history: a change that happened and reverted between two older rounds still shows
    up here as two separate events, each at the round where it was actually detected."""

    detected_at: datetime
    parameter_name: str
    previous_value: str | None
    current_value: str | None
    change_type: str  # newly_set | changed | reverted_to_default
