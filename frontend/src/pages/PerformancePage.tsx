import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { TimeRangeControl, type TimeRange } from "../components/charts/TimeRangeControl";
import { TrendChart } from "../components/charts/TrendChart";
import { Badge } from "../components/ui/Badge";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Loading } from "../components/ui/Loading";
import { CompareModePanel } from "../components/telemetry/CompareModePanel";
import { DatabaseSelector, useDatabases } from "../components/telemetry/DatabaseSelector";
import { apiFetch, errorMessage } from "../lib/api";
import { SEVERITY_TONE } from "../lib/severity";
import type { Anomaly, CpuHistoryPoint, InstanceHealthPoint, Session, SessionsSnapshot } from "../types/api";

const ACTIVE_SESSION_SERIES = [{ key: "active_session_count", label: "Active sessions" }];
const CPU_SERIES = [{ key: "cpu_centisec_used_per_sec", label: "CPU usage" }];

/** A session counts as "genuinely computing" when its real STATE (Finding 7,
 * docs/QA_LIVE_FINDINGS.md) is one of the WAITED variants - it recently finished a wait and
 * is on CPU right now, as opposed to WAITING (blocked on its current event right now) or a
 * session that isn't ACTIVE at all. Explains what a CPU reading actually corresponds to,
 * instead of leaving it as an abstract percentage with no context. */
export function countComputingSessions(sessions: Session[]): { active: number; computing: number } {
  const active = sessions.filter((s) => s.status === "ACTIVE");
  const computing = active.filter((s) => s.state?.startsWith("WAITED"));
  return { active: active.length, computing: computing.length };
}

export function PerformancePage() {
  const databasesQuery = useDatabases();
  const [databaseId, setDatabaseId] = useState("");
  const [range, setRange] = useState<TimeRange>({ hours: 1 });

  useEffect(() => {
    if (!databaseId && databasesQuery.data?.length) setDatabaseId(databasesQuery.data[0].id);
  }, [databaseId, databasesQuery.data]);

  const untilParam = range.until ? `&until=${encodeURIComponent(range.until)}` : "";
  const healthHistoryQuery = useQuery({
    queryKey: ["health-history", databaseId, range.hours, range.until],
    queryFn: () =>
      apiFetch<InstanceHealthPoint[]>(`/api/databases/${databaseId}/health-history?hours=${range.hours}${untilParam}`),
    enabled: Boolean(databaseId),
    refetchInterval: range.until ? false : 15_000,
  });

  const cpuHistoryQuery = useQuery({
    queryKey: ["cpu-history", databaseId, range.hours, range.until],
    queryFn: () =>
      apiFetch<CpuHistoryPoint[]>(`/api/databases/${databaseId}/cpu/history?hours=${range.hours}${untilParam}`),
    enabled: Boolean(databaseId),
    refetchInterval: range.until ? false : 15_000,
  });

  const anomaliesQuery = useQuery({
    queryKey: ["anomalies", databaseId],
    queryFn: () => apiFetch<Anomaly[]>(`/api/databases/${databaseId}/anomalies?limit=200`),
    enabled: Boolean(databaseId),
    refetchInterval: 15_000,
  });

  // Same query key SessionsPage/SqlIntelligencePage already use, so this shares cache with
  // whichever of those pages the user last visited.
  const sessionsQuery = useQuery({
    queryKey: ["sessions", databaseId],
    queryFn: () => apiFetch<SessionsSnapshot>(`/api/databases/${databaseId}/sessions`),
    enabled: Boolean(databaseId),
    refetchInterval: 15_000,
  });

  const windowAnomalies = useMemo(() => {
    if (!anomaliesQuery.data) return [];
    const untilMs = range.until ? new Date(range.until).getTime() : Date.now();
    const sinceMs = untilMs - range.hours * 3600_000;
    return anomaliesQuery.data.filter((a) => {
      const detectedMs = new Date(a.detected_at).getTime();
      return detectedMs >= sinceMs && detectedMs <= untilMs;
    });
  }, [anomaliesQuery.data, range]);

  const chartData = (healthHistoryQuery.data ?? []).map((p) => ({
    time: new Date(p.sampled_at).toLocaleString(),
    active_session_count: p.active_session_count,
  }));

  const cpuChartData = (cpuHistoryQuery.data ?? []).map((p) => ({
    time: new Date(p.sampled_at).toLocaleString(),
    cpu_centisec_used_per_sec: p.cpu_centisec_used_per_sec,
  }));

  const sessionActivity = sessionsQuery.data?.available ? countComputingSessions(sessionsQuery.data.sessions) : null;
  const cpuContext = sessionActivity && (
    <>
      {sessionActivity.active} active session{sessionActivity.active === 1 ? "" : "s"} ·{" "}
      {sessionActivity.computing} genuinely computing right now
    </>
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <DatabaseSelector databaseId={databaseId} onChange={setDatabaseId} databases={databasesQuery.data ?? []} />
        <TimeRangeControl value={range} onChange={setRange} />
      </div>

      {!databaseId && <EmptyState title="Select a database to view its performance timeline" />}

      {databaseId && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[320px_1fr]">
          <CompareModePanel databaseId={databaseId} metricKey="active_session_count" />
          <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">Active session count</p>
            {healthHistoryQuery.isPending && <Loading label="Loading timeline…" />}
            {healthHistoryQuery.isError && (
              <ErrorState message={errorMessage(healthHistoryQuery.error)} onRetry={() => healthHistoryQuery.refetch()} />
            )}
            {healthHistoryQuery.isSuccess && chartData.length === 0 && (
              <EmptyState title="No telemetry in this window" description="Try a wider time range, or check back once the collector has run." />
            )}
            {healthHistoryQuery.isSuccess && chartData.length > 0 && (
              <TrendChart
                data={chartData}
                xKey="time"
                series={ACTIVE_SESSION_SERIES}
                yAllowDecimals={false}
              />
            )}
          </div>
        </div>
      )}

      {databaseId && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[320px_1fr]">
          <CompareModePanel
            databaseId={databaseId}
            metricKey="cpu_centisec_used_per_sec"
            valueSuffix="%"
            extraContext={cpuContext}
          />
          <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">Database CPU usage (% of one CPU core)</p>
            {cpuHistoryQuery.isError && (
              <ErrorState message={errorMessage(cpuHistoryQuery.error)} onRetry={() => cpuHistoryQuery.refetch()} />
            )}
            {cpuHistoryQuery.isSuccess && cpuChartData.length === 0 && (
              <EmptyState
                title="Not enough telemetry for CPU usage yet"
                description="Rates need at least two collection rounds - try a wider range, or check back once the collector has run."
              />
            )}
            {cpuHistoryQuery.isSuccess && cpuChartData.length > 0 && (
              <TrendChart
                data={cpuChartData}
                xKey="time"
                series={CPU_SERIES}
                valueSuffix="%"
              />
            )}
          </div>
        </div>
      )}

      {databaseId && (
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">Anomalies in this window</p>
          {anomaliesQuery.isPending && <Loading label="Loading anomalies…" />}
          {anomaliesQuery.isSuccess && windowAnomalies.length === 0 && (
            <p className="text-sm text-slate-500 dark:text-slate-400">No anomalies detected in the selected time range.</p>
          )}
          {anomaliesQuery.isSuccess && windowAnomalies.length > 0 && (
            <ul className="flex flex-col gap-2">
              {windowAnomalies.map((a) => (
                <li key={a.id} className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2">
                    <Badge tone={SEVERITY_TONE[a.severity]}>{a.severity}</Badge>
                    <span className="font-mono text-xs text-slate-600 dark:text-slate-400">{a.metric_key}</span>
                  </span>
                  <span className="text-xs text-slate-400 dark:text-slate-500">{new Date(a.detected_at).toLocaleString()}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
