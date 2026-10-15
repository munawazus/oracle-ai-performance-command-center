export interface CurrentUser {
  id: string;
  email: string;
  display_name: string;
  roles: string[];
  permissions: string[];
}

export interface AccessTokenResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
}

export interface MonitoredDatabase {
  id: string;
  name: string;
  host: string;
  port: number;
  service_name: string;
  edition_hint: "SE2" | "EE" | "UNKNOWN";
  is_active: boolean;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export interface CreateDatabaseInput {
  name: string;
  host: string;
  port: number;
  service_name: string;
  edition_hint: "SE2" | "EE" | "UNKNOWN";
  username: string;
  password: string;
  tags?: string[];
}

export interface UpdateDatabaseInput {
  name?: string;
  host?: string;
  port?: number;
  service_name?: string;
  edition_hint?: "SE2" | "EE" | "UNKNOWN";
  is_active?: boolean;
  username?: string;
  password?: string;
  tags?: string[];
}

export interface DatabaseGroup {
  id: string;
  name: string;
  description: string;
  database_count: number;
  created_at: string;
  updated_at: string;
}

export interface CreateDatabaseGroupInput {
  name: string;
  description?: string;
}

export interface UpdateDatabaseGroupInput {
  name?: string;
  description?: string;
}

export interface BulkCreateResult {
  index: number;
  status: "created" | "failed";
  database: MonitoredDatabase | null;
  error: string | null;
}

export interface BulkCreateDatabasesResponse {
  results: BulkCreateResult[];
  created_count: number;
  failed_count: number;
}

export interface CollectorStatus {
  last_run_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  consecutive_failures: number;
  last_error_category: string | null;
  last_run_duration_ms: number | null;
}

export interface TestConnectionResult {
  ok: boolean;
  category: string | null;
  message: string;
}

export interface CapabilityProfile {
  detected_version: string | null;
  detected_edition: "SE2" | "EE" | "UNKNOWN" | null;
  is_rac: boolean | null;
  statspack_available: boolean | null;
  diagnostics_tuning_pack_licensed_confirmed: boolean;
  privilege_report: Record<string, boolean> | null;
  last_probed_at: string | null;
  cpu_count: number | null;
}

export interface Session {
  sid: number;
  serial_num: number | null;
  username: string | null;
  sql_id: string | null;
  event: string | null;
  wait_class: string | null;
  status: string | null;
  blocking_session: number | null;
  machine: string | null;
  program: string | null;
  module: string | null;
  action: string | null;
  state: string | null;
  last_call_et: number | null;
}

export interface SessionsSnapshot {
  available: boolean;
  reason: string | null;
  sampled_at: string | null;
  session_count: number;
  sessions: Session[];
}

export interface WaitClassRate {
  wait_class: string;
  centisec_waited_per_sec: number;
}

export interface WaitProfile {
  available: boolean;
  reason: string | null;
  sampled_at: string | null;
  elapsed_seconds: number | null;
  wait_classes: WaitClassRate[];
}

export interface WaitClassHistoryPoint {
  sampled_at: string;
  elapsed_seconds: number;
  wait_classes: WaitClassRate[];
}

export interface CpuHistoryPoint {
  sampled_at: string;
  elapsed_seconds: number;
  cpu_centisec_used_per_sec: number;
}

export interface BlockingChainEntry {
  blocked_sid: number;
  blocked_username: string | null;
  blocked_sql_id: string | null;
  blocker_sid: number;
  blocker_username: string | null;
  blocker_sql_id: string | null;
  blocker_in_this_sample: boolean;
}

export interface BlockingSnapshot {
  available: boolean;
  reason: string | null;
  sampled_at: string | null;
  blocking_chains: BlockingChainEntry[];
}

export interface InstanceHealthPoint {
  sampled_at: string;
  reachability: string;
  instance_status: string | null;
  active_session_count: number | null;
}

export interface Anomaly {
  id: string;
  detected_at: string;
  metric_key: string;
  observed_value: number;
  baseline_mean: number;
  baseline_stddev: number;
  sample_count: number;
  severity: IncidentSeverity;
}

export interface MetricCompareResponse {
  available: boolean;
  reason: string | null;
  metric_key: string | null;
  metric_label: string | null;
  hour_of_day: number | null;
  observed_value: number | null;
  baseline_mean: number | null;
  baseline_stddev: number | null;
  sample_count: number;
  min_samples_for_anomaly: number;
  z_score: number | null;
  severity: string | null;
  baseline_updated_at: string | null;
}

export const ROLE_NAMES = ["ADMIN", "DBA", "OPERATOR", "VIEWER", "AUDITOR"] as const;
export type RoleName = (typeof ROLE_NAMES)[number];

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  is_active: boolean;
  roles: string[];
  created_at: string;
}

export interface CreateUserInput {
  email: string;
  password: string;
  display_name: string;
  roles: RoleName[];
}

export type IncidentSeverity = "INFO" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type IncidentStatus = "OPEN" | "ACKNOWLEDGED" | "RESOLVED";

export type FleetOverallStatus = "PASS" | "WARN" | "FAIL" | "UNAVAILABLE";
export type CapacityTrend = "stable" | "warning" | "critical";

export interface FleetVitalsRow {
  database_id: string;
  database_name: string;
  overall_status: FleetOverallStatus;
  open_incident_count: number;
  blocking_present: boolean;
  capacity_trend: CapacityTrend;
}

export type ActivityEventCategory =
  | "anomaly"
  | "sql_regression"
  | "capacity_alert"
  | "incident_opened"
  | "incident_resolved"
  | "storm";

export interface ActivityEvent {
  event_id: string;
  category: ActivityEventCategory;
  severity: IncidentSeverity;
  occurred_at: string;
  database_id: string;
  database_name: string;
  summary: string;
  entities: Record<string, unknown>;
}

export interface Incident {
  id: string;
  monitored_database_id: string;
  status: IncidentStatus;
  severity: IncidentSeverity;
  title: string;
  opened_at: string;
  last_evidence_at: string;
  acknowledged_at: string | null;
  acknowledged_by_user_id: string | null;
  assigned_to_user_id: string | null;
  resolved_at: string | null;
  resolved_by_user_id: string | null;
  resolution_note: string | null;
}

export interface IncidentEvent {
  id: string;
  occurred_at: string;
  event_type: string;
  actor_user_id: string | null;
  detail: Record<string, unknown> | null;
}

export interface AssignableUser {
  id: string;
  display_name: string;
  email: string;
}

export interface NotificationSettings {
  smtp_host: string | null;
  smtp_port: number;
  smtp_use_tls: boolean;
  smtp_username: string | null;
  has_smtp_password: boolean;
  sender_email: string | null;
  sender_name: string;
  default_recipients: string[];
  escalation_recipients: string[];
}

export interface UpdateNotificationSettingsInput {
  smtp_host?: string;
  smtp_port?: number;
  smtp_use_tls?: boolean;
  smtp_username?: string;
  smtp_password?: string;
  sender_email?: string;
  sender_name?: string;
  default_recipients?: string[];
  escalation_recipients?: string[];
}

export type NotificationStatus = "PENDING" | "RETRYING" | "SENT" | "FAILED";

export interface NotificationEvent {
  id: string;
  incident_id: string;
  severity: IncidentSeverity;
  recipients: string[];
  subject: string;
  body_text: string;
  status: NotificationStatus;
  attempt_count: number;
  last_attempt_at: string | null;
  last_error_category: string | null;
  sent_at: string | null;
  created_at: string;
}

export interface CopilotAskInput {
  question: string;
  database_id?: string;
  incident_id?: string;
}

export interface ToolStatus {
  tool_name: string;
  called: boolean;
  returned_evidence: boolean;
  reason: string | null;
}

export interface Finding {
  finding_id: string;
  category: string;
  severity: string;
  summary: string;
  entities: Record<string, string | number | boolean | null>;
  sampled_at: string | null;
  age_seconds: number | null;
  is_stale: boolean | null;
}

export interface CopilotAnswer {
  answer: string;
  model: string | null;
  tools_used: string[];
  tool_statuses: ToolStatus[];
  evidence_coverage: string | null;
  findings: Finding[];
}

export type RcaConfidence = "low" | "medium" | "high" | null;

export interface RcaResult {
  probable_root_cause: string | null;
  confidence: RcaConfidence;
  evidence_summary: string;
  secondary_contributors: string[];
  alternative_hypotheses: string[];
  recommended_investigation: string[];
  recommended_action: string[];
  risk: string | null;
  data_completeness_note: string;
  model: string | null;
}

export type ReportType = "DAILY_DBA" | "INCIDENT" | "PERFORMANCE" | "SQL_REGRESSION" | "CAPACITY" | "HEALTH";

export interface GenerateReportInput {
  report_type: ReportType;
  database_id?: string;
  incident_id?: string;
  period_start?: string;
  period_end?: string;
  include_rca?: boolean;
}

export interface ReportSummary {
  id: string;
  report_type: ReportType;
  requested_by_email: string;
  monitored_database_id: string | null;
  database_name_snapshot: string | null;
  incident_id: string | null;
  incident_title_snapshot: string | null;
  period_start: string | null;
  period_end: string | null;
  timezone: string;
  generated_at: string;
  data_available: boolean;
}

export interface ReportDetail extends ReportSummary {
  data_completeness_note: string;
  limitations: string[];
  // Shape varies by report_type - rendered generically rather than typed exhaustively.
  content: Record<string, unknown>;
}

export type IntegrationType = "WEBHOOK" | "SLACK" | "TEAMS";

export interface Integration {
  id: string;
  type: IntegrationType;
  name: string;
  target_url: string;
  has_secret: boolean;
  enabled: boolean;
  trigger_severities: string[];
  created_at: string;
  updated_at: string;
}

export interface CreateIntegrationInput {
  type: IntegrationType;
  name: string;
  target_url: string;
  secret?: string;
  enabled: boolean;
  trigger_severities: string[];
}

export interface UpdateIntegrationInput {
  name?: string;
  target_url?: string;
  secret?: string;
  enabled?: boolean;
  trigger_severities?: string[];
}

export interface TestIntegrationResult {
  ok: boolean;
  status_code: number | null;
  message: string;
}

export type IntegrationDeliveryStatus = "PENDING" | "RETRYING" | "SENT" | "FAILED";

export interface IntegrationDelivery {
  id: string;
  integration_id: string;
  incident_id: string;
  event_type: string;
  status: IntegrationDeliveryStatus;
  attempt_count: number;
  last_error_category: string | null;
  response_status_code: number | null;
  sent_at: string | null;
  created_at: string;
}

export type SqlSortKey = "elapsed_time" | "cpu_time" | "buffer_gets" | "disk_reads" | "executions" | "elapsed_per_exec";

export interface TopSqlRow {
  sql_id: string;
  plan_hash_value: number;
  sql_text: string | null;
  sql_text_truncated: boolean;
  parsing_schema_name: string | null;
  child_cursor_count: number;
  executions_delta: number;
  elapsed_time_us_delta: number;
  cpu_time_us_delta: number;
  buffer_gets_delta: number;
  disk_reads_delta: number;
  rows_processed_delta: number;
  elapsed_per_exec_us: number | null;
  elapsed_time_us_per_sec: number;
  plan_count: number;
  open_regression_kind: string | null;
  open_regression_severity: IncidentSeverity | null;
}

export interface TopSqlResponse {
  available: boolean;
  reason: string | null;
  sampled_at: string | null;
  previous_sampled_at: string | null;
  elapsed_seconds: number | null;
  sort_by: SqlSortKey;
  rows: TopSqlRow[];
}

export interface SqlPlanLine {
  id: number;
  parent_id: number | null;
  depth: number | null;
  position: number | null;
  operation: string | null;
  options: string | null;
  object_owner: string | null;
  object_name: string | null;
  object_type: string | null;
  cost: number | null;
  cardinality: number | null;
  bytes: number | null;
}

export interface SqlPlanResponse {
  available: boolean;
  reason: string | null;
  sql_id: string | null;
  plan_hash_value: number | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  plan_lines: SqlPlanLine[];
}

export interface SqlPlanSummary {
  plan_hash_value: number;
  first_seen_at: string;
  last_seen_at: string;
  plan_line_count: number;
  is_current: boolean;
}

export interface SqlSessionReference {
  sampled_at: string;
  sid: number;
  username: string | null;
  program: string | null;
  module: string | null;
  event: string | null;
  wait_class: string | null;
}

export type SqlRegressionKind = "PLAN_CHANGE" | "ELAPSED_PER_EXEC";
export type SqlRegressionStatus = "OPEN" | "RESOLVED";

export interface SqlRegression {
  id: string;
  sql_id: string;
  sql_text: string | null;
  regression_kind: SqlRegressionKind;
  severity: IncidentSeverity;
  status: SqlRegressionStatus;
  first_detected_at: string;
  last_detected_at: string;
  resolved_at: string | null;
  baseline_elapsed_per_exec_us: number | null;
  current_elapsed_per_exec_us: number | null;
  ratio: number | null;
  baseline_executions: number | null;
  current_executions: number | null;
  baseline_plan_hash_value: number | null;
  current_plan_hash_value: number | null;
  detection_note: string;
}

export interface SqlDetail {
  available: boolean;
  reason: string | null;
  sql_id: string | null;
  sql_text: string | null;
  sql_text_truncated: boolean;
  parsing_schema_name: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  plans: SqlPlanSummary[];
  recent_sessions: SqlSessionReference[];
  open_regressions: SqlRegression[];
}

export type TablespaceContents = "PERMANENT" | "UNDO" | "TEMPORARY";

export interface TablespaceUsageRow {
  tablespace_name: string;
  contents: TablespaceContents;
  status: string | null;
  allocated_bytes: number;
  used_bytes: number;
  max_bytes: number | null;
  file_count: number;
  pct_used: number;
  pct_of_max: number | null;
  growth_available: boolean;
  growth_reason: string | null;
  growth_bytes_per_day: number | null;
  projected_days_to_full: number | null;
  projection_reason: string | null;
  open_alert_kind: string | null;
  open_alert_severity: IncidentSeverity | null;
}

export interface CapacityOverviewResponse {
  available: boolean;
  reason: string | null;
  sampled_at: string | null;
  rows: TablespaceUsageRow[];
}

export type CapacityAlertKind = "PCT_USED" | "PROJECTED_FULL";
export type CapacityAlertStatus = "OPEN" | "RESOLVED";

export interface CapacityAlert {
  id: string;
  tablespace_name: string;
  alert_kind: CapacityAlertKind;
  severity: IncidentSeverity;
  status: CapacityAlertStatus;
  first_detected_at: string;
  last_detected_at: string;
  resolved_at: string | null;
  pct_used: number | null;
  pct_basis: "max size" | "allocated" | null;
  projected_days_to_full: number | null;
  current_used_bytes: number | null;
  current_allocated_bytes: number | null;
  current_max_bytes: number | null;
  detection_note: string;
}

export interface TablespaceHistoryPoint {
  sampled_at: string;
  allocated_bytes: number;
  used_bytes: number;
  max_bytes: number | null;
}

export interface TablespaceDetailResponse {
  available: boolean;
  reason: string | null;
  tablespace_name: string | null;
  contents: TablespaceContents | null;
  status: string | null;
  usage: TablespaceUsageRow | null;
  history: TablespaceHistoryPoint[];
  open_alerts: CapacityAlert[];
}

export interface CapacityThresholds {
  warn_pct_used: number | null;
  critical_pct_used: number | null;
  warn_days_to_full: number | null;
  critical_days_to_full: number | null;
  default_warn_pct_used: number;
  default_critical_pct_used: number;
  default_warn_days_to_full: number;
  default_critical_days_to_full: number;
}

export interface StatspackSnapshotRef {
  snap_id: number;
  snap_time: string;
}

export interface StatspackWaitClassRate {
  wait_class: string;
  centisec_waited_per_hour: number;
}

export interface StatspackSqlRow {
  sql_id: string;
  plan_hash_value: number;
  sql_text: string | null;
  executions_delta: number;
  elapsed_time_us_delta: number;
  elapsed_per_exec_us: number | null;
}

export interface StatspackSummaryResponse {
  available: boolean;
  reason: string | null;
  from_snapshot: StatspackSnapshotRef | null;
  to_snapshot: StatspackSnapshotRef | null;
  elapsed_hours: number | null;
  wait_classes: StatspackWaitClassRate[];
  top_sql: StatspackSqlRow[];
}

export interface ParameterChangeItem {
  parameter_name: string;
  previous_value: string | null;
  current_value: string | null;
  change_type: "newly_set" | "changed" | "reverted_to_default";
}

export interface ConfigurationSummaryResponse {
  available: boolean;
  reason: string | null;
  from_sampled_at: string | null;
  to_sampled_at: string | null;
  changes: ParameterChangeItem[];
}

export interface ConfigChangeEvent {
  detected_at: string;
  parameter_name: string;
  previous_value: string | null;
  current_value: string | null;
  change_type: "newly_set" | "changed" | "reverted_to_default";
}

export interface AuditEvent {
  id: string;
  occurred_at: string;
  actor_user_id: string | null;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  success: boolean;
  ip_address: string | null;
  event_metadata: Record<string, unknown> | null;
}
