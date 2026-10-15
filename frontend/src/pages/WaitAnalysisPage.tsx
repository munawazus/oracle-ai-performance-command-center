import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { TimeRangeControl, type TimeRange } from "../components/charts/TimeRangeControl";
import { TrendChart, type TrendChartSeries } from "../components/charts/TrendChart";
import { WaitClassSmallMultiples } from "../components/charts/WaitClassSmallMultiples";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Loading } from "../components/ui/Loading";
import { DatabaseSelector, useDatabases } from "../components/telemetry/DatabaseSelector";
import { WaitDetailPanel } from "../components/telemetry/WaitDetailPanel";
import { apiFetch, errorMessage } from "../lib/api";
import type { CapabilityProfile, CpuHistoryPoint, WaitClassHistoryPoint } from "../types/api";

// Oracle can report up to 10 real wait classes, but the validated CVD-safe palette only
// covers 8 hues reliably (dataviz skill: "a 9th series is never a generated hue - it folds
// into Other"). These 7 are the ones a DBA actually tunes against - I/O contention, lock
// contention, application waits, commit/log sync, network, config changes - so each keeps
// its own color; everything else sums into one combined series.
const CORE_WAIT_CLASSES = new Set([
  "Application",
  "Commit",
  "Concurrency",
  "Configuration",
  "Network",
  "System I/O",
  "User I/O",
]);
// Deliberately not "Other" - Oracle already reports a real "Other" wait class, and this
// series is a superset of it (also folds in Administrative and Scheduler), so reusing that
// exact name would misleadingly imply it's just the one real class.
const OTHER_CLASSES_KEY = "Other classes";

/** Shapes wait-class history points into chart rows + a series list, folding any class
 * outside CORE_WAIT_CLASSES into one combined OTHER_CLASSES_KEY series - see the comment
 * above for why. Only appears when there's a real, non-zero value to show. */
export function groupWaitClasses(
  points: WaitClassHistoryPoint[],
): { chartData: Record<string, unknown>[]; series: TrendChartSeries[] } {
  const classKeys = new Set<string>();
  const chartData = points.map((point) => {
    const row: Record<string, unknown> = { time: new Date(point.sampled_at).toLocaleString(), __rawTime: point.sampled_at };
    let otherTotal = 0;
    for (const wc of point.wait_classes) {
      if (CORE_WAIT_CLASSES.has(wc.wait_class)) {
        row[wc.wait_class] = wc.centisec_waited_per_sec;
        classKeys.add(wc.wait_class);
      } else {
        otherTotal += wc.centisec_waited_per_sec;
      }
    }
    if (otherTotal > 0) {
      row[OTHER_CLASSES_KEY] = otherTotal;
      classKeys.add(OTHER_CLASSES_KEY);
    }
    return row;
  });
  const series: TrendChartSeries[] = [...classKeys].sort().map((key) => ({ key, label: key }));
  return { chartData, series };
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

// Half the 15s collector tick - a CPU reading further than this from a wait round isn't
// really "the same round," so it's left unmerged rather than paired with the wrong point.
const AAS_MATCH_TOLERANCE_MS = 8_000;

/** Merges the CPU rate into each wait-history row by nearest sampled_at (/waits/history and
 * /cpu/history are two separate routes over the same underlying collector rounds, not
 * guaranteed to return identically-timestamped points) and scales every value - CPU and every
 * wait class alike - from raw centiseconds/sec to average active sessions: the same
 * divide-by-100 derivation F7.2 already established for "% of one CPU core" on Performance,
 * framed here as a session count to match Oracle ASH Analytics' own convention (both are
 * "how much of one session-second was spent in this state, per second of wall time").
 * CPU is placed first in the returned series list - Oracle's own ASH Analytics stacks CPU at
 * the chart's zero baseline, wait classes on top of it - the rest keep groupWaitClasses's
 * already-validated alphabetical order (reordering the wait classes themselves would need a
 * fresh validator pass, since a different adjacency is a different palette question). */
export function addCpuLayer(
  waitChartData: Record<string, unknown>[],
  waitSeries: TrendChartSeries[],
  cpuPoints: CpuHistoryPoint[],
): { chartData: Record<string, unknown>[]; series: TrendChartSeries[] } {
  const cpuByMs = cpuPoints.map((p) => ({ ms: new Date(p.sampled_at).getTime(), value: p.cpu_centisec_used_per_sec }));

  let hasCpu = false;
  const chartData = waitChartData.map((row) => {
    const scaled: Record<string, unknown> = { time: row.time, __rawTime: row.__rawTime };
    for (const [key, value] of Object.entries(row)) {
      if (key === "time" || key === "__rawTime") continue;
      if (typeof value === "number") scaled[key] = round2(value / 100);
    }

    const rowMs = new Date(String(row.__rawTime)).getTime();
    let best: { ms: number; value: number } | null = null;
    let bestDiff = Infinity;
    for (const p of cpuByMs) {
      const diff = Math.abs(p.ms - rowMs);
      if (diff < bestDiff) {
        bestDiff = diff;
        best = p;
      }
    }
    if (best && bestDiff <= AAS_MATCH_TOLERANCE_MS) {
      scaled["CPU"] = round2(best.value / 100);
      hasCpu = true;
    }
    return scaled;
  });

  const series = hasCpu ? [{ key: "CPU", label: "CPU" }, ...waitSeries] : waitSeries;
  return { chartData, series };
}

/** The most recent point's total activity and its single biggest contributor (any series in
 * the given list - CPU included, once merged in) - unlike Performance's Compare Mode, there's
 * no one stable "the" metric here to hold a baseline against (up to 9 series, any of which
 * can dominate), so this is a plain current-state summary, not a fabricated baseline
 * comparison. Null when there's no data at all; `topClass` is null (not an arbitrary pick)
 * when every series reads zero right now. */
export function currentWaitProfile(
  chartData: Record<string, unknown>[],
  series: TrendChartSeries[],
): { total: number; topClass: string | null; topValue: number } | null {
  if (chartData.length === 0 || series.length === 0) return null;
  const latest = chartData[chartData.length - 1];
  const values = series.map((s) => ({
    key: s.key,
    value: typeof latest[s.key] === "number" ? (latest[s.key] as number) : 0,
  }));
  const total = values.reduce((sum, v) => sum + v.value, 0);
  if (total <= 0) return { total: 0, topClass: null, topValue: 0 };
  const top = values.reduce((best, v) => (v.value > best.value ? v : best));
  return { total: round2(total), topClass: top.key, topValue: round2(top.value) };
}

export function WaitAnalysisPage() {
  const databasesQuery = useDatabases();
  const [databaseId, setDatabaseId] = useState("");
  const [range, setRange] = useState<TimeRange>({ hours: 1 });

  useEffect(() => {
    if (!databaseId && databasesQuery.data?.length) setDatabaseId(databasesQuery.data[0].id);
  }, [databaseId, databasesQuery.data]);

  const untilParam = range.until ? `&until=${encodeURIComponent(range.until)}` : "";
  const waitsQuery = useQuery({
    queryKey: ["waits-history", databaseId, range.hours, range.until],
    queryFn: () =>
      apiFetch<WaitClassHistoryPoint[]>(`/api/databases/${databaseId}/waits/history?hours=${range.hours}${untilParam}`),
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

  // Same query key CapabilityPanel already uses, so this shares cache with the Databases
  // page - no extra Oracle round-trip just to draw a reference line. Static instance
  // metadata (refreshed only when capabilities are re-detected), so no polling here either.
  const capabilitiesQuery = useQuery({
    queryKey: ["capabilities", databaseId],
    queryFn: () => apiFetch<CapabilityProfile>(`/api/databases/${databaseId}/capabilities`),
    enabled: Boolean(databaseId),
  });
  const cpuCoreReferenceLine = capabilitiesQuery.data?.cpu_count
    ? { value: capabilitiesQuery.data.cpu_count, label: "Max CPU cores" }
    : undefined;

  const { chartData, series } = useMemo(() => {
    const grouped = groupWaitClasses(waitsQuery.data ?? []);
    return addCpuLayer(grouped.chartData, grouped.series, cpuHistoryQuery.data ?? []);
  }, [waitsQuery.data, cpuHistoryQuery.data]);
  const waitProfile = useMemo(() => currentWaitProfile(chartData, series), [chartData, series]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <DatabaseSelector databaseId={databaseId} onChange={setDatabaseId} databases={databasesQuery.data ?? []} />
        <TimeRangeControl value={range} onChange={setRange} />
      </div>

      {!databaseId && <EmptyState title="Select a database to view its wait profile" />}
      {databaseId && waitsQuery.isPending && <Loading label="Loading wait profile…" />}
      {databaseId && waitsQuery.isError && (
        <ErrorState message={errorMessage(waitsQuery.error)} onRetry={() => waitsQuery.refetch()} />
      )}
      {databaseId && waitsQuery.isSuccess && chartData.length === 0 && (
        <EmptyState
          title="Not enough telemetry in this window"
          description="Rates need at least two collection rounds - try a wider range, or check back once the collector has run."
        />
      )}
      {databaseId && waitsQuery.isSuccess && chartData.length > 0 && waitProfile && (
        <div className="flex flex-col gap-4">
          <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">
              Average active sessions, by CPU and wait class. Derived from telemetry rates, not
              Oracle's own ASH sampling.
            </p>
            <TrendChart data={chartData} xKey="time" series={series} referenceLine={cpuCoreReferenceLine} />
          </div>
          <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">Per-class breakdown</p>
            <WaitClassSmallMultiples data={chartData} xKey="time" series={series} referenceLine={cpuCoreReferenceLine} />
          </div>
          <WaitDetailPanel databaseId={databaseId} waitProfile={waitProfile} />
        </div>
      )}
    </div>
  );
}
