import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Drawer } from "../components/ui/Drawer";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Loading } from "../components/ui/Loading";
import { Select } from "../components/ui/Select";
import { Table, type Column } from "../components/ui/Table";
import { DatabaseSelector, useDatabases } from "../components/telemetry/DatabaseSelector";
import { apiFetch, errorMessage } from "../lib/api";
import { SEVERITY_RANK, SEVERITY_TONE } from "../lib/severity";
import type {
  CapabilityProfile,
  IncidentSeverity,
  Session,
  SessionsSnapshot,
  SqlDetail,
  SqlPlanLine,
  SqlPlanResponse,
  SqlRegression,
  SqlSortKey,
  StatspackSummaryResponse,
  TopSqlResponse,
  TopSqlRow,
} from "../types/api";

type SessionDimension = "wait_class" | "module" | "action" | "username" | "machine";

interface SessionFilter {
  dimension: SessionDimension;
  value: string;
}

const DIMENSION_LABEL: Record<SessionDimension, string> = {
  wait_class: "Wait class",
  module: "Module",
  action: "Action",
  username: "User",
  machine: "Machine",
};

const MAX_SESSIONS_SHOWN = 50;

const SORT_OPTIONS: { value: SqlSortKey; label: string }[] = [
  { value: "elapsed_time", label: "Elapsed time" },
  { value: "cpu_time", label: "CPU time" },
  { value: "buffer_gets", label: "Buffer gets" },
  { value: "disk_reads", label: "Disk reads" },
  { value: "executions", label: "Executions" },
  { value: "elapsed_per_exec", label: "Elapsed / exec" },
];

function truncateSql(text: string | null, max = 80): string {
  if (!text) return "—";
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function formatMicros(us: number | null): string {
  if (us === null) return "—";
  if (us >= 1_000_000) return `${(us / 1_000_000).toFixed(2)}s`;
  if (us >= 1_000) return `${(us / 1_000).toFixed(2)}ms`;
  return `${Math.round(us)}µs`;
}

type CostTier = "critical" | "warning" | null;

/** Highlights a plan line's cost relative to *that plan's own* other lines, not a fixed
 * number, so it generalizes across differently sized databases (same reasoning as UI.1's
 * severity work) - the 97th-percentile-and-up line(s) are "critical", 90th-to-97th are
 * "warning". Lines with no cost recorded are never highlighted. */
export function computeCostTiers(lines: SqlPlanLine[]): Map<number, CostTier> {
  const costs = lines.map((l) => l.cost).filter((c): c is number => c !== null).sort((a, b) => a - b);
  const tiers = new Map<number, CostTier>();
  if (costs.length === 0) {
    for (const line of lines) tiers.set(line.id, null);
    return tiers;
  }
  const percentile = (p: number) => costs[Math.min(costs.length - 1, Math.floor(costs.length * p))];
  const p90 = percentile(0.9);
  const p97 = percentile(0.97);
  for (const line of lines) {
    if (line.cost === null) tiers.set(line.id, null);
    else if (line.cost >= p97) tiers.set(line.id, "critical");
    else if (line.cost >= p90) tiers.set(line.id, "warning");
    else tiers.set(line.id, null);
  }
  return tiers;
}

/** A plan change that lands on a *faster* plan still counts as a "regression" finding at
 * severity LOW (backend/tests/test_sql_regression_detection.py:
 * test_plan_change_severity_is_low_when_the_new_plan_is_not_worse) - the fleet-wide banner
 * must reflect the worst real severity present, not treat every open regression as critical. */
export function worstRegressionSeverity(regressions: SqlRegression[]): IncidentSeverity | null {
  if (regressions.length === 0) return null;
  return regressions.reduce<IncidentSeverity>(
    (worst, r) => (SEVERITY_RANK[r.severity] > SEVERITY_RANK[worst] ? r.severity : worst),
    regressions[0].severity,
  );
}

/** Alert only has 4 tones (no "neutral"/"critical" the way Badge does) - HIGH and CRITICAL
 * both read as the alert's own most severe "crit" tone, matching how a fleet-wide banner
 * (vs. a per-row Badge) only needs to draw the eye, not distinguish every severity step. */
export function regressionAlertTone(severity: IncidentSeverity): "info" | "warn" | "crit" {
  if (severity === "HIGH" || severity === "CRITICAL") return "crit";
  if (severity === "MEDIUM") return "warn";
  return "info";
}

function sessionDimensionValue(session: Session, dimension: SessionDimension): string | null {
  switch (dimension) {
    case "wait_class":
      return session.wait_class;
    case "module":
      return session.module;
    case "action":
      return session.action;
    case "username":
      return session.username;
    case "machine":
      return session.machine;
  }
}

export function SqlIntelligencePage() {
  const databasesQuery = useDatabases();
  const [databaseId, setDatabaseId] = useState("");
  const [sortBy, setSortBy] = useState<SqlSortKey>("elapsed_time");
  const [selectedSqlId, setSelectedSqlId] = useState<string | null>(null);

  useEffect(() => {
    if (!databaseId && databasesQuery.data?.length) setDatabaseId(databasesQuery.data[0].id);
  }, [databaseId, databasesQuery.data]);

  // The SQL collector runs on its own, slower interval (300s by default) - polling faster
  // than the backing data changes would just be wasted requests.
  const topSqlQuery = useQuery({
    queryKey: ["top-sql", databaseId, sortBy],
    queryFn: () => apiFetch<TopSqlResponse>(`/api/databases/${databaseId}/top-sql?sort_by=${sortBy}`),
    enabled: Boolean(databaseId),
    refetchInterval: 60_000,
  });

  const regressionsQuery = useQuery({
    queryKey: ["sql-regressions", databaseId],
    queryFn: () => apiFetch<SqlRegression[]>(`/api/databases/${databaseId}/sql-regressions?status=OPEN`),
    enabled: Boolean(databaseId),
    refetchInterval: 60_000,
  });

  // Same query key + interval as SessionsPage.tsx, so navigating between the two pages
  // shares one cache entry.
  const sessionsQuery = useQuery({
    queryKey: ["sessions", databaseId],
    queryFn: () => apiFetch<SessionsSnapshot>(`/api/databases/${databaseId}/sessions`),
    enabled: Boolean(databaseId),
    refetchInterval: 15_000,
  });

  const [sessionFilters, setSessionFilters] = useState<SessionFilter[]>([]);
  const toggleSessionFilter = (dimension: SessionDimension, value: string) => {
    setSessionFilters((prev) =>
      prev.some((f) => f.dimension === dimension && f.value === value)
        ? prev.filter((f) => !(f.dimension === dimension && f.value === value))
        : [...prev, { dimension, value }],
    );
  };

  const columns: Column<TopSqlRow>[] = [
    {
      header: "SQL",
      cell: (r) => (
        <div>
          <p className="font-mono text-xs text-slate-800 dark:text-slate-200">{truncateSql(r.sql_text)}</p>
          <p className="text-xs text-slate-400 dark:text-slate-500">{r.sql_id}</p>
        </div>
      ),
    },
    {
      header: "Regression",
      cell: (r) =>
        r.open_regression_severity ? (
          <Badge tone={SEVERITY_TONE[r.open_regression_severity]}>{r.open_regression_kind}</Badge>
        ) : (
          "—"
        ),
    },
    { header: "Executions Δ", cell: (r) => r.executions_delta.toLocaleString() },
    { header: "Elapsed Δ", cell: (r) => formatMicros(r.elapsed_time_us_delta) },
    { header: "CPU time Δ", cell: (r) => formatMicros(r.cpu_time_us_delta) },
    { header: "Elapsed / exec", cell: (r) => formatMicros(r.elapsed_per_exec_us) },
    { header: "Buffer gets Δ", cell: (r) => r.buffer_gets_delta.toLocaleString() },
    { header: "Disk reads Δ", cell: (r) => r.disk_reads_delta.toLocaleString() },
    { header: "Plans seen", cell: (r) => r.plan_count },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <DatabaseSelector databaseId={databaseId} onChange={setDatabaseId} databases={databasesQuery.data ?? []} />
        <div className="w-52">
          <Select
            label="Rank by"
            options={SORT_OPTIONS}
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as SqlSortKey)}
          />
        </div>
      </div>

      {!databaseId && <EmptyState title="Select a database to view its top SQL" />}

      {databaseId && regressionsQuery.isSuccess && regressionsQuery.data.length > 0 && (
        <Alert tone={regressionAlertTone(worstRegressionSeverity(regressionsQuery.data)!)}>
          {regressionsQuery.data.length} open SQL regression(s) detected -{" "}
          {regressionsQuery.data
            .slice(0, 3)
            .map((r) => r.sql_id)
            .join(", ")}
          {regressionsQuery.data.length > 3 ? ", …" : ""}
        </Alert>
      )}

      {databaseId && topSqlQuery.isPending && <Loading label="Loading top SQL…" />}
      {databaseId && topSqlQuery.isError && (
        <ErrorState message={errorMessage(topSqlQuery.error)} onRetry={() => topSqlQuery.refetch()} />
      )}
      {databaseId && topSqlQuery.data && !topSqlQuery.data.available && (
        <EmptyState
          title="Not enough SQL telemetry yet"
          description={topSqlQuery.data.reason ?? "The SQL collector needs two collection rounds to compute a delta."}
        />
      )}
      {databaseId && topSqlQuery.data?.available && topSqlQuery.data.rows.length === 0 && (
        <EmptyState title="No SQL activity in the latest interval" description="The database appears idle." />
      )}
      {databaseId && topSqlQuery.data?.available && topSqlQuery.data.rows.length > 0 && (
        <>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Per-interval delta over the last {Math.round(topSqlQuery.data.elapsed_seconds ?? 0)}s, as of{" "}
            {new Date(topSqlQuery.data.sampled_at!).toLocaleTimeString()}.
          </p>
          <Table
            columns={columns}
            rows={topSqlQuery.data.rows}
            rowKey={(r) => `${r.sql_id}-${r.plan_hash_value}`}
            onRowClick={(r) => setSelectedSqlId(r.sql_id)}
          />
        </>
      )}

      {databaseId && sessionsQuery.isPending && <Loading label="Loading sessions…" />}
      {databaseId && sessionsQuery.isError && (
        <ErrorState message={errorMessage(sessionsQuery.error)} onRetry={() => sessionsQuery.refetch()} />
      )}
      {databaseId && sessionsQuery.data && !sessionsQuery.data.available && (
        <EmptyState title="No session telemetry yet" description={sessionsQuery.data.reason ?? undefined} />
      )}
      {databaseId && sessionsQuery.data?.available && (
        <SessionsPanel
          sessions={sessionsQuery.data.sessions}
          filters={sessionFilters}
          onToggleFilter={toggleSessionFilter}
          onSqlIdClick={setSelectedSqlId}
        />
      )}

      {databaseId && <StatspackPanel databaseId={databaseId} />}

      {databaseId && selectedSqlId && (
        <SqlDetailDrawer databaseId={databaseId} sqlId={selectedSqlId} onClose={() => setSelectedSqlId(null)} />
      )}
    </div>
  );
}

function SessionsPanel({
  sessions,
  filters,
  onToggleFilter,
  onSqlIdClick,
}: {
  sessions: Session[];
  filters: SessionFilter[];
  onToggleFilter: (dimension: SessionDimension, value: string) => void;
  onSqlIdClick: (sqlId: string) => void;
}) {
  const filtered = sessions.filter((s) => filters.every((f) => sessionDimensionValue(s, f.dimension) === f.value));
  const sorted = [...filtered].sort((a, b) => {
    const aActive = a.status === "ACTIVE" ? 0 : 1;
    const bActive = b.status === "ACTIVE" ? 0 : 1;
    return aActive !== bActive ? aActive - bActive : a.sid - b.sid;
  });
  const shown = sorted.slice(0, MAX_SESSIONS_SHOWN);

  const dimensionCell = (session: Session, dimension: SessionDimension) => {
    const value = sessionDimensionValue(session, dimension);
    if (!value) return "—";
    const isActive = filters.some((f) => f.dimension === dimension && f.value === value);
    return (
      <button
        type="button"
        onClick={() => onToggleFilter(dimension, value)}
        className={`-mx-1 rounded px-1 text-left hover:underline ${
          isActive ? "bg-brand-50 text-brand-700 dark:bg-brand-900/30 dark:text-brand-300" : ""
        }`}
      >
        {value}
      </button>
    );
  };

  const columns: Column<Session>[] = [
    { header: "SID", cell: (s) => s.sid },
    { header: "Username", cell: (s) => s.username ?? "—" },
    {
      header: "SQL ID",
      cell: (s) =>
        s.sql_id ? (
          <button
            type="button"
            onClick={() => onSqlIdClick(s.sql_id!)}
            className="font-mono text-xs text-brand-600 hover:underline dark:text-brand-400"
          >
            {s.sql_id}
          </button>
        ) : (
          "—"
        ),
    },
    { header: "Wait class", cell: (s) => dimensionCell(s, "wait_class") },
    {
      header: "State",
      cell: (s) => (
        <span title="V$SESSION.STATE - WAITING means blocked on Wait class right now; WAITED ... means it recently finished a wait and may genuinely be on CPU.">
          {s.state ?? "—"}
        </span>
      ),
    },
    { header: "Module", cell: (s) => dimensionCell(s, "module") },
    { header: "Action", cell: (s) => dimensionCell(s, "action") },
    { header: "Machine", cell: (s) => dimensionCell(s, "machine") },
  ];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-200">
          Sessions
          {sorted.length > MAX_SESSIONS_SHOWN && (
            <span className="ml-2 font-normal text-xs text-slate-400 dark:text-slate-500">
              Showing {shown.length} of {sorted.length}
            </span>
          )}
        </h2>
        {filters.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            {filters.map((f) => (
              <button
                key={`${f.dimension}:${f.value}`}
                type="button"
                onClick={() => onToggleFilter(f.dimension, f.value)}
                className="flex items-center gap-1 rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700 hover:bg-brand-100 dark:bg-brand-900/30 dark:text-brand-300 dark:hover:bg-brand-900/50"
              >
                {DIMENSION_LABEL[f.dimension]}: {f.value}
                <span aria-hidden="true">×</span>
              </button>
            ))}
          </div>
        )}
      </div>
      {shown.length === 0 ? (
        <EmptyState
          title={filters.length > 0 ? "No sessions match the active filters" : "No sessions in the latest sample"}
        />
      ) : (
        <Table columns={columns} rows={shown} rowKey={(s) => String(s.sid)} />
      )}
    </div>
  );
}

function StatspackPanel({ databaseId }: { databaseId: string }) {
  // Reuses the same query key CapabilityPanel.tsx already fetches under, so the two share
  // one cache entry - Statspack is an opt-in DBA feature likely present on a small minority
  // of a fleet, so this panel renders nothing at all rather than an always-empty section
  // for the common case of a database that doesn't have it.
  const capabilitiesQuery = useQuery({
    queryKey: ["capabilities", databaseId],
    queryFn: () => apiFetch<CapabilityProfile>(`/api/databases/${databaseId}/capabilities`),
  });

  if (!capabilitiesQuery.data?.statspack_available) return null;

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <h2 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Statspack</h2>
      <StatspackSummary databaseId={databaseId} />
    </div>
  );
}

function StatspackSummary({ databaseId }: { databaseId: string }) {
  // Statspack snapshots are taken on the DBA's own irregular schedule (often hourly,
  // sometimes not at all) - polling faster than a typical snap cadence would be wasted.
  const summaryQuery = useQuery({
    queryKey: ["statspack", databaseId],
    queryFn: () => apiFetch<StatspackSummaryResponse>(`/api/databases/${databaseId}/statspack`),
    refetchInterval: 600_000,
  });

  if (summaryQuery.isPending) return <Loading label="Loading Statspack summary…" />;
  if (summaryQuery.isError) {
    return <ErrorState message={errorMessage(summaryQuery.error)} onRetry={() => summaryQuery.refetch()} />;
  }
  if (!summaryQuery.data.available) {
    return (
      <EmptyState
        title="No Statspack delta available yet"
        description={summaryQuery.data.reason ?? undefined}
      />
    );
  }

  const { from_snapshot, to_snapshot, elapsed_hours, wait_classes, top_sql } = summaryQuery.data;

  return (
    <div className="flex flex-col gap-4 text-sm">
      <p className="text-xs text-slate-500 dark:text-slate-400">
        Delta between snap {from_snapshot?.snap_id} ({new Date(from_snapshot!.snap_time).toLocaleString()}) and
        snap {to_snapshot?.snap_id} ({new Date(to_snapshot!.snap_time).toLocaleString()}) - {elapsed_hours?.toFixed(1)}h
        apart.
      </p>

      <div>
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Top wait classes</h3>
        {wait_classes.length === 0 ? (
          <p className="text-xs text-slate-500 dark:text-slate-400">No wait activity between these snapshots.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {wait_classes.slice(0, 10).map((w) => (
              <li
                key={w.wait_class}
                className="flex items-center justify-between rounded-md border border-slate-200 px-2 py-1 text-xs dark:border-slate-800"
              >
                <span className="text-slate-700 dark:text-slate-300">{w.wait_class}</span>
                <span className="text-slate-400 dark:text-slate-500">{w.centisec_waited_per_hour.toFixed(0)} centisec/h</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Top SQL</h3>
        {top_sql.length === 0 ? (
          <p className="text-xs text-slate-500 dark:text-slate-400">No SQL activity between these snapshots.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {top_sql.slice(0, 10).map((s) => (
              <li
                key={`${s.sql_id}-${s.plan_hash_value}`}
                className="rounded-md border border-slate-200 px-2 py-1 text-xs dark:border-slate-800"
              >
                <p className="font-mono text-slate-800 dark:text-slate-200">{truncateSql(s.sql_text)}</p>
                <p className="text-slate-400 dark:text-slate-500">
                  {s.sql_id} - {formatMicros(s.elapsed_time_us_delta)} elapsed, {s.executions_delta.toLocaleString()}{" "}
                  execution(s)
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function SqlDetailDrawer({
  databaseId,
  sqlId,
  onClose,
}: {
  databaseId: string;
  sqlId: string;
  onClose: () => void;
}) {
  const [selectedPlanHash, setSelectedPlanHash] = useState<number | null>(null);

  const detailQuery = useQuery({
    queryKey: ["sql-detail", databaseId, sqlId],
    queryFn: () => apiFetch<SqlDetail>(`/api/databases/${databaseId}/sql/${sqlId}`),
  });

  return (
    <Drawer title={sqlId} isOpen onClose={onClose}>
      {detailQuery.isPending && <Loading label="Loading statement detail…" />}
      {detailQuery.isError && (
        <ErrorState message={errorMessage(detailQuery.error)} onRetry={() => detailQuery.refetch()} />
      )}
      {detailQuery.isSuccess && !detailQuery.data.available && (
        <EmptyState title="No SQL telemetry for this statement" description={detailQuery.data.reason ?? undefined} />
      )}
      {detailQuery.isSuccess && detailQuery.data.available && (
        <div className="flex flex-col gap-6 text-sm">
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">SQL text</h3>
            <pre className="whitespace-pre-wrap rounded-md border border-slate-200 bg-slate-50 p-2 font-mono text-xs text-slate-800 dark:border-slate-800 dark:bg-slate-800 dark:text-slate-200">
              {detailQuery.data.sql_text ?? "—"}
              {detailQuery.data.sql_text_truncated && (
                <span className="text-slate-400 dark:text-slate-500"> … (truncated at 1000 characters)</span>
              )}
            </pre>
          </div>

          {detailQuery.data.open_regressions.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
                Open regressions
              </h3>
              <ul className="flex flex-col gap-2">
                {detailQuery.data.open_regressions.map((r) => (
                  <li key={r.id} className="rounded-md border border-slate-200 p-2 text-xs dark:border-slate-800">
                    <div className="mb-1 flex items-center gap-2">
                      <Badge tone={SEVERITY_TONE[r.severity]}>{r.severity}</Badge>
                      <span className="text-slate-500 dark:text-slate-400">{r.regression_kind}</span>
                    </div>
                    <p className="text-slate-700 dark:text-slate-300">{r.detection_note}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Plan history</h3>
            <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">
              {detailQuery.data.plans.length} distinct plan(s) captured. Click one for its plan tree.
            </p>
            <ul className="flex flex-col gap-1">
              {detailQuery.data.plans.map((p) => (
                <li key={p.plan_hash_value}>
                  <button
                    type="button"
                    onClick={() => setSelectedPlanHash(p.plan_hash_value)}
                    className={`w-full rounded-md border px-2 py-1.5 text-left text-xs hover:bg-slate-50 dark:hover:bg-slate-800 ${
                      selectedPlanHash === p.plan_hash_value
                        ? "border-brand-500 bg-brand-50 dark:border-brand-400 dark:bg-brand-900/30"
                        : "border-slate-200 dark:border-slate-800"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono dark:text-slate-200">{p.plan_hash_value}</span>
                      <span className="flex items-center gap-1">
                        {p.is_current && <Badge tone="good">current</Badge>}
                        <span className="text-slate-400 dark:text-slate-500">{p.plan_line_count} line(s)</span>
                      </span>
                    </div>
                    <p className="text-slate-400 dark:text-slate-500">
                      First seen {new Date(p.first_seen_at).toLocaleString()}, last seen{" "}
                      {new Date(p.last_seen_at).toLocaleString()}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
            {selectedPlanHash !== null && (
              <PlanTree databaseId={databaseId} sqlId={sqlId} planHashValue={selectedPlanHash} />
            )}
          </div>

          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
              Recent sessions running this SQL
            </h3>
            {detailQuery.data.recent_sessions.length === 0 ? (
              <p className="text-xs text-slate-500 dark:text-slate-400">No session sample has recorded this sql_id recently.</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {detailQuery.data.recent_sessions.map((s, i) => (
                  <li key={i} className="rounded-md border border-slate-200 p-2 text-xs dark:border-slate-800">
                    SID {s.sid} - {s.username ?? "—"} - {[s.program, s.module].filter(Boolean).join(" / ") || "—"} -{" "}
                    {new Date(s.sampled_at).toLocaleTimeString()}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </Drawer>
  );
}

function PlanTree({ databaseId, sqlId, planHashValue }: { databaseId: string; sqlId: string; planHashValue: number }) {
  const planQuery = useQuery({
    queryKey: ["sql-plan", databaseId, sqlId, planHashValue],
    queryFn: () => apiFetch<SqlPlanResponse>(`/api/databases/${databaseId}/sql/${sqlId}/plan/${planHashValue}`),
  });

  return (
    <div className="mt-2 rounded-md border border-slate-200 bg-slate-50 p-2 dark:border-slate-800 dark:bg-slate-800">
      {planQuery.isPending && <Loading label="Loading plan…" />}
      {planQuery.isError && <ErrorState message={errorMessage(planQuery.error)} onRetry={() => planQuery.refetch()} />}
      {planQuery.isSuccess && !planQuery.data.available && (
        <p className="text-xs text-slate-500 dark:text-slate-400">{planQuery.data.reason ?? "Plan snapshot not available."}</p>
      )}
      {planQuery.isSuccess && planQuery.data.available && (
        <PlanTreeLines lines={planQuery.data.plan_lines} />
      )}
    </div>
  );
}

function PlanTreeLines({ lines }: { lines: SqlPlanLine[] }) {
  // Computed once for the whole plan (relative to its own lines), not per row.
  const tiers = computeCostTiers(lines);
  return (
    <ol className="flex flex-col gap-0.5 font-mono text-xs">
      {lines.map((line) => (
        <PlanLineRow key={line.id} line={line} tier={tiers.get(line.id) ?? null} />
      ))}
    </ol>
  );
}

const COST_TIER_CLASS: Record<Exclude<CostTier, null>, string> = {
  critical: "text-status-crit dark:text-red-400 font-semibold",
  warning: "text-status-warn dark:text-amber-400",
};

function PlanLineRow({ line, tier }: { line: SqlPlanLine; tier: CostTier }) {
  return (
    <li style={{ paddingLeft: `${(line.depth ?? 0) * 16}px` }} className="text-slate-700 dark:text-slate-300">
      {line.operation ?? "?"}
      {line.options ? ` ${line.options}` : ""}
      {line.object_name ? ` of ${line.object_name}` : ""}
      {line.cost !== null && (
        <span className={tier ? COST_TIER_CLASS[tier] : "text-slate-400 dark:text-slate-500"}> (cost={line.cost})</span>
      )}
    </li>
  );
}
