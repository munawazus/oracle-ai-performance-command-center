import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { Badge, type Tone } from "../ui/Badge";
import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { Loading } from "../ui/Loading";
import { apiFetch, errorMessage } from "../../lib/api";
import { SEVERITY_TONE } from "../../lib/severity";
import type { IncidentSeverity, MetricCompareResponse } from "../../types/api";

/** Position (0-100) of `observed` along a track spanning ±clampSigma standard deviations
 * around `mean`, clamped at the edges. Null when there's no variance to position against yet
 * (stddev <= 0) - this is a purely descriptive read, never itself a claim of anomalousness;
 * that verdict only ever comes from the backend's own classifier (see compareStatus). */
export function computeDeviationPosition(
  observed: number,
  mean: number,
  stddev: number,
  clampSigma = 3,
): number | null {
  if (stddev <= 0) return null;
  const z = (observed - mean) / stddev;
  const clamped = Math.max(-clampSigma, Math.min(clampSigma, z));
  return ((clamped + clampSigma) / (clampSigma * 2)) * 100;
}

/** The backend's classify_severity is the only source of the anomalous/not-anomalous
 * verdict - this only picks the right honest label for whichever case it already reported. */
export function compareStatus(
  severity: string | null,
  sampleCount: number,
  minSamplesForAnomaly: number,
): { label: string; tone: Tone } {
  if (severity !== null && severity in SEVERITY_TONE) {
    return { label: severity, tone: SEVERITY_TONE[severity as IncidentSeverity] };
  }
  if (sampleCount >= minSamplesForAnomaly) {
    return { label: "Normal", tone: "good" };
  }
  return { label: "Building baseline", tone: "neutral" };
}

// Grafana-style: the panel's whole state reads at a glance from a colored accent (left
// border) and the value's own text color, not just a small corner badge - the badge stays too
// (color alone shouldn't be the only signal), but it's no longer the only thing that moves.
// `!` (important) is required here: index.css's own `*`/`:is(.dark *)` border-color reset
// has the same specificity as any single utility class and sits later in the compiled
// stylesheet, so it silently wins the left-side color over a plain `border-l-*` utility -
// confirmed live via computed styles, not assumed.
const TONE_BORDER_CLASS: Record<Tone, string> = {
  good: "!border-l-status-good",
  info: "!border-l-status-info",
  warn: "!border-l-status-warn",
  crit: "!border-l-status-crit",
  critical: "!border-l-red-600 dark:!border-l-red-500",
  neutral: "!border-l-slate-300 dark:!border-l-slate-700",
};

const TONE_VALUE_CLASS: Record<Tone, string> = {
  good: "text-status-good",
  info: "text-status-info",
  warn: "text-status-warn",
  crit: "text-status-crit",
  critical: "text-red-600 dark:text-red-500",
  neutral: "text-slate-800 dark:text-slate-100",
};

// A Grafana Bar Gauge fills a track up to the value in a single threshold color, rather than
// marking a bare dot on an unfilled line - reused here as a *diverging* fill (from the
// baseline mean outward, since a deviation can go either direction) so a bigger deviation
// reads as a visibly bigger, more saturated bar, not just a dot further along a thin track.
const TONE_FILL_CLASS: Record<Tone, string> = {
  good: "bg-status-good",
  info: "bg-status-info",
  warn: "bg-status-warn",
  crit: "bg-status-crit",
  critical: "bg-red-600 dark:bg-red-500",
  neutral: "bg-slate-400 dark:bg-slate-500",
};

export function CompareModePanel({
  databaseId,
  metricKey,
  valueSuffix = "",
  extraContext,
}: {
  databaseId: string;
  metricKey: string;
  /** Appended to the current value and both baseline numbers, e.g. "%". */
  valueSuffix?: string;
  /** An optional extra line of caller-supplied context for this specific metric (e.g. how
   * many sessions are behind a CPU reading) - kept generic here so this component stays
   * metric-agnostic; the caller decides what's relevant for the metric it's showing. */
  extraContext?: ReactNode;
}) {
  const compareQuery = useQuery({
    queryKey: ["metric-compare", databaseId, metricKey],
    queryFn: () =>
      apiFetch<MetricCompareResponse>(
        `/api/databases/${databaseId}/metrics/compare?metric_key=${encodeURIComponent(metricKey)}`,
      ),
    enabled: Boolean(databaseId && metricKey),
    refetchInterval: 15_000,
  });

  if (compareQuery.isPending) return <Loading label="Loading comparison…" />;
  if (compareQuery.isError) {
    return <ErrorState message={errorMessage(compareQuery.error)} onRetry={() => compareQuery.refetch()} />;
  }

  const data = compareQuery.data;
  if (!data.available) {
    return <EmptyState title="No current value for this metric" description={data.reason ?? undefined} />;
  }

  const hasBaseline = data.sample_count > 0 && data.baseline_mean !== null && data.baseline_stddev !== null;
  const status = compareStatus(data.severity, data.sample_count, data.min_samples_for_anomaly);
  const position = hasBaseline
    ? computeDeviationPosition(data.observed_value!, data.baseline_mean!, data.baseline_stddev!)
    : null;

  return (
    <div
      className={`rounded-lg border border-l-4 border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900 ${TONE_BORDER_CLASS[status.tone]}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-slate-500 dark:text-slate-400">{data.metric_label} vs. its baseline for this hour</p>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>

      <p className="mt-2 flex items-baseline gap-1">
        <span className={`text-4xl font-bold tabular-nums ${TONE_VALUE_CLASS[status.tone]}`}>
          {data.observed_value!.toFixed(1)}
        </span>
        {valueSuffix && <span className="text-lg font-medium text-slate-400 dark:text-slate-500">{valueSuffix}</span>}
      </p>

      {!hasBaseline && (
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Still building this hour's baseline (0 of {data.min_samples_for_anomaly} samples needed).
        </p>
      )}

      {hasBaseline && (
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Baseline {data.baseline_mean!.toFixed(1)}
          {valueSuffix} ± {data.baseline_stddev!.toFixed(1)}
          {valueSuffix} ({data.sample_count} of {data.min_samples_for_anomaly}+ samples)
        </p>
      )}

      {extraContext && (
        <p className="mt-3 border-t border-slate-100 pt-3 text-sm text-slate-600 dark:border-slate-800 dark:text-slate-300">
          {extraContext}
        </p>
      )}

      {hasBaseline && position !== null && (
        <div className="mt-4">
          <div className="relative h-3 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800" aria-hidden="true">
            <div
              className={`absolute top-0 h-full rounded-full transition-all ${TONE_FILL_CLASS[status.tone]}`}
              style={{ left: `${Math.min(50, position)}%`, width: `${Math.abs(position - 50)}%` }}
            />
            <div className="absolute top-0 left-1/2 h-full w-px bg-white/80 dark:bg-slate-950/60" />
          </div>
          <div className="mt-1 flex justify-between text-[10px] font-medium uppercase tracking-wide text-slate-400 dark:text-slate-600">
            <span>−3σ</span>
            <span>Baseline mean</span>
            <span>+3σ</span>
          </div>
        </div>
      )}
    </div>
  );
}
