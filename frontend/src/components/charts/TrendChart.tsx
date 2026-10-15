import { useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useTheme } from "../../lib/theme-context";

export interface TrendChartSeries {
  key: string;
  label: string;
}

export interface TrendChartProps {
  data: Record<string, unknown>[];
  xKey: string;
  series: TrendChartSeries[];
  height?: number;
  yAllowDecimals?: boolean;
  /** Appended to the tooltip's value, e.g. " cs/s". */
  valueSuffix?: string;
  /** Points beyond this are bucketed to their per-bucket max (see bucketToPeaks) rather than
   * rendered one-for-one, so a brief real spike stays visible at any zoom level instead of
   * being one lucky/unlucky sample among hundreds. Default suits this app's typical chart
   * width; override only if a caller renders unusually wide/narrow. */
  maxPoints?: number;
  /** A single flat, constant-value horizontal line - e.g. Oracle ASH Analytics' own "Max CPU
   * cores" convention (sessions queue for CPU once Average Active Sessions on CPU exceeds
   * this count). Not the removed per-timestamp annotation mechanism: this is one fixed value
   * for the whole chart, never matched against individual data points. */
  referenceLine?: { value: number; label: string };
}

/** When a series has far more points than the chart can usefully show, groups consecutive
 * points into buckets and keeps each bucket's MAX (never an average, which would smooth a
 * real spike away) - the same reason real monitoring tools default to max-aggregation for
 * zoomed-out threshold-style views. A bucket's x-label is its last point's, so the label
 * still reads as a real moment in time. Returns `data` unchanged when it already fits. */
export function bucketToPeaks(
  data: Record<string, unknown>[],
  xKey: string,
  seriesKeys: string[],
  maxPoints: number,
): Record<string, unknown>[] {
  if (data.length <= maxPoints) return data;

  const bucketSize = Math.ceil(data.length / maxPoints);
  const buckets: Record<string, unknown>[] = [];
  for (let i = 0; i < data.length; i += bucketSize) {
    const chunk = data.slice(i, i + bucketSize);
    // Spread the chunk's last row first so any extra field a caller carries survives bucketing
    // unchanged - only xKey and the series values are recomputed.
    const bucket: Record<string, unknown> = { ...chunk[chunk.length - 1], [xKey]: chunk[chunk.length - 1][xKey] };
    for (const key of seriesKeys) {
      let max: number | null = null;
      for (const row of chunk) {
        const value = row[key];
        if (typeof value === "number" && (max === null || value > max)) max = value;
      }
      bucket[key] = max;
    }
    buckets.push(bucket);
  }
  return buckets;
}

// A categorical palette deliberately distinct from the red/amber/green already used for
// severity elsewhere in the app, so a series color (e.g. one wait class) is never mistaken
// for a severity signal. Run through the dataviz skill's actual validator
// (scripts/validate_palette.js), not eyeballed - this exact order clears every hard gate in
// both light and dark mode (worst adjacent CVD deltaE 15.9 light / 8.2 dark, both above the
// 8.0 target; worst normal-vision deltaE 19.6 light / 15.3 dark, both above the 15.0 floor).
// Replaces the previous hand-picked set, which included "lime" - too close to this app's own
// status.good green (docs/CHART_QUALITY_PLAN.md).
const PALETTE: { light: string; dark: string }[] = [
  { light: "#e87ba4", dark: "#d55181" }, // magenta
  { light: "#eda100", dark: "#c98500" }, // yellow
  { light: "#0d9488", dark: "#0d9488" }, // teal
  { light: "#4a3aa7", dark: "#9085e9" }, // violet
  { light: "#0891b2", dark: "#0891b2" }, // cyan
  { light: "#eb6834", dark: "#d95926" }, // orange
  { light: "#2a78d6", dark: "#3987e5" }, // blue
  { light: "#db2777", dark: "#ec4899" }, // pink
];

// A reserved, non-categorical color for a literal "CPU" series (Wait Analysis's Oracle-ASH-
// style stack) - CPU isn't a wait class competing for a categorical slot, so it never cycles
// through PALETTE the way a 9th wait class would (the dataviz skill's own rule: "a 9th series
// is never a generated hue"). Validated against the existing 8-slot PALETTE in this exact
// adjacency (CPU stacked directly below the alphabetically-first class, "Application") -
// clears every hard gate in both modes (light: CVD deltaE 14.5, normal-vision 19.6; dark: CVD
// 8.2, normal-vision 15.3 - both the worst pair *overall*, not involving CPU at all, meaning
// CPU's own adjacency is comfortably inside the floor).
const CPU_COLOR = { light: "#1a7a4c", dark: "#15803d" };

/** Assigns colors by each key's position in *sorted* order, not by data value/rank, so a
 * series keeps the same color across refetches even as rates change and reorder. The literal
 * key "CPU" is special-cased to its own reserved color (see CPU_COLOR) rather than competing
 * for a categorical slot - every other key is unaffected. */
export function assignSeriesColors(keys: string[], isDarkMode: boolean): Record<string, string> {
  const categoricalKeys = keys.filter((k) => k !== "CPU").sort();
  const colors: Record<string, string> = {};
  if (keys.includes("CPU")) colors["CPU"] = isDarkMode ? CPU_COLOR.dark : CPU_COLOR.light;
  categoricalKeys.forEach((key, i) => {
    const pair = PALETTE[i % PALETTE.length];
    colors[key] = isDarkMode ? pair.dark : pair.light;
  });
  return colors;
}

export function toggleHiddenKey(hidden: ReadonlySet<string>, key: string): Set<string> {
  const next = new Set(hidden);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}

/** One shared time-range chart for both Performance (a single active-session-count series)
 * and Wait Analysis (one series per real wait class) - "stack of one" for a single series is
 * an honest area, not a fabricated stack, so both callers get the identical visual treatment
 * without implying a decomposition the data doesn't support. */
export function TrendChart({
  data,
  xKey,
  series,
  height = 280,
  yAllowDecimals = false,
  valueSuffix = "",
  maxPoints = 400,
  referenceLine,
}: TrendChartProps) {
  const { resolvedIsDark: isDarkMode } = useTheme();
  const [hiddenKeys, setHiddenKeys] = useState<ReadonlySet<string>>(new Set());
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const colors = useMemo(
    () => assignSeriesColors(series.map((s) => s.key), isDarkMode),
    [series, isDarkMode],
  );
  const seriesKeys = useMemo(() => series.map((s) => s.key), [series]);
  const chartData = useMemo(
    () => bucketToPeaks(data, xKey, seriesKeys, maxPoints),
    [data, xKey, seriesKeys, maxPoints],
  );
  const isBucketed = chartData.length < data.length;

  const gridColor = isDarkMode ? "#334155" : "#e2e8f0";
  const tickColor = isDarkMode ? "#94a3b8" : "#64748b";

  // The chart's own rightmost point - what a side legend showing "current value" should
  // reflect, consistent with where the drawn line itself ends (the bucketed peak of the last
  // interval when zoomed out, not necessarily the single latest raw sample).
  const latestRow = chartData.length > 0 ? chartData[chartData.length - 1] : null;
  const valueOf = (key: string): number | null =>
    latestRow && typeof latestRow[key] === "number" ? (latestRow[key] as number) : null;
  // Sorted by current value, not stack/alphabetical order - a side table exists specifically
  // to answer "what's dominant right now" at a glance (the real Grafana Oracle dashboards'
  // own legend-table convention), which a fixed order doesn't serve.
  const sortedSeries = [...series].sort((a, b) => (valueOf(b.key) ?? 0) - (valueOf(a.key) ?? 0));

  return (
    <>
      <div className="flex flex-col gap-3 sm:flex-row">
        <div className="min-w-0 flex-1">
          <ResponsiveContainer width="100%" height={height}>
            <AreaChart data={chartData}>
              <CartesianGrid vertical={false} stroke={gridColor} />
              <XAxis dataKey={xKey} tick={{ fontSize: 11, fill: tickColor }} minTickGap={30} />
              <YAxis
                tick={{ fontSize: 11, fill: tickColor }}
                allowDecimals={yAllowDecimals}
                // Recharts clips a ReferenceLine outside the auto-scaled data domain by default -
                // real current activity can sit far below a real CPU core count (e.g. 0.1 AAS vs.
                // 22 cores), so the domain must explicitly extend to cover it, not just the data.
                domain={
                  referenceLine ? [0, (dataMax: number) => Math.max(dataMax, referenceLine.value * 1.05)] : undefined
                }
              />
              <Tooltip formatter={(value, name) => [`${value}${valueSuffix}`, String(name)]} />
              {series.map((s) => {
                const isDimmed = hoveredKey !== null && hoveredKey !== s.key;
                return (
                  <Area
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    name={s.label}
                    stackId="1"
                    stroke={colors[s.key]}
                    strokeWidth={hoveredKey === s.key ? 3.5 : 2}
                    strokeOpacity={isDimmed ? 0.3 : 1}
                    fill={colors[s.key]}
                    fillOpacity={isDimmed ? 0.04 : 0.12}
                    hide={hiddenKeys.has(s.key)}
                    dot={false}
                  />
                );
              })}
              {referenceLine && (
                <ReferenceLine
                  y={referenceLine.value}
                  stroke={isDarkMode ? "#f87171" : "#dc2626"}
                  strokeDasharray="4 4"
                  strokeWidth={1.5}
                  label={{
                    value: referenceLine.label,
                    position: "insideTopRight",
                    fill: isDarkMode ? "#f87171" : "#dc2626",
                    fontSize: 11,
                  }}
                />
              )}
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {series.length > 1 && (
          <div className="flex flex-col gap-0.5 sm:w-48 sm:shrink-0">
            <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
              By current value
            </p>
            {sortedSeries.map((s) => {
              const value = valueOf(s.key);
              const isHidden = hiddenKeys.has(s.key);
              const isHovered = hoveredKey === s.key;
              return (
                <button
                  key={s.key}
                  type="button"
                  onClick={() => setHiddenKeys((prev) => toggleHiddenKey(prev, s.key))}
                  onMouseEnter={() => setHoveredKey(s.key)}
                  onMouseLeave={() => setHoveredKey(null)}
                  className={`flex items-center justify-between gap-2 rounded px-2 py-1 text-left text-xs transition-colors ${
                    isHovered ? "bg-slate-100 dark:bg-slate-800" : ""
                  }`}
                  style={{ opacity: isHidden ? 0.4 : 1 }}
                >
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ backgroundColor: colors[s.key] }}
                      aria-hidden="true"
                    />
                    <span className={`truncate text-slate-600 dark:text-slate-300 ${isHovered ? "font-semibold" : ""}`}>
                      {s.label}
                    </span>
                  </span>
                  <span className="shrink-0 tabular-nums text-slate-500 dark:text-slate-400">
                    {value === null ? "—" : `${value.toFixed(2)}${valueSuffix}`}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>
      {isBucketed && (
        <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
          Showing peak values per interval ({data.length.toLocaleString()} samples grouped into{" "}
          {chartData.length.toLocaleString()}) so brief spikes stay visible at this zoom level.
        </p>
      )}
    </>
  );
}
