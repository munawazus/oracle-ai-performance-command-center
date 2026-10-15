import { useMemo } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useTheme } from "../../lib/theme-context";
import { assignSeriesColors, bucketToPeaks, type TrendChartSeries } from "./TrendChart";

export interface WaitClassSmallMultiplesProps {
  data: Record<string, unknown>[];
  xKey: string;
  series: TrendChartSeries[];
  height?: number;
  valueSuffix?: string;
  maxPoints?: number;
  /** Applies only to the tile whose series key is literally "CPU" - same shape as
   * TrendChart's own referenceLine prop (Oracle ASH's "Max CPU cores" convention). */
  referenceLine?: { value: number; label: string };
}

/** One small chart per class, replacing the single combined stacked chart. Deliberately NOT a
 * shared Y-axis across tiles: each tile auto-scales to its own data, so a naturally small-
 * magnitude class (e.g. Commit) still shows its own real spike clearly, instead of being
 * flattened under whatever class happens to run largest - matching this layout's actual job
 * ("does any one class have an unusual pattern"), not a magnitude-comparison chart. */
export function WaitClassSmallMultiples({
  data,
  xKey,
  series,
  height = 140,
  valueSuffix = "",
  maxPoints = 400,
  referenceLine,
}: WaitClassSmallMultiplesProps) {
  const { resolvedIsDark: isDarkMode } = useTheme();
  const seriesKeys = useMemo(() => series.map((s) => s.key), [series]);
  const colors = useMemo(() => assignSeriesColors(seriesKeys, isDarkMode), [seriesKeys, isDarkMode]);
  const chartData = useMemo(
    () => bucketToPeaks(data, xKey, seriesKeys, maxPoints),
    [data, xKey, seriesKeys, maxPoints],
  );

  const gridColor = isDarkMode ? "#334155" : "#e2e8f0";
  const tickColor = isDarkMode ? "#94a3b8" : "#64748b";
  const refColor = isDarkMode ? "#f87171" : "#dc2626";

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
      {series.map((s) => {
        const tileReferenceLine = s.key === "CPU" ? referenceLine : undefined;
        return (
          <div
            key={s.key}
            className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900"
          >
            <p className="mb-1 text-xs font-medium text-slate-600 dark:text-slate-300">{s.label}</p>
            <ResponsiveContainer width="100%" height={height}>
              <AreaChart data={chartData}>
                <CartesianGrid vertical={false} stroke={gridColor} />
                <XAxis dataKey={xKey} tick={{ fontSize: 9, fill: tickColor }} minTickGap={40} />
                <YAxis
                  tick={{ fontSize: 9, fill: tickColor }}
                  width={30}
                  domain={
                    tileReferenceLine
                      ? [0, (dataMax: number) => Math.max(dataMax, tileReferenceLine.value * 1.05)]
                      : undefined
                  }
                />
                <Tooltip formatter={(value) => [`${value}${valueSuffix}`, s.label]} />
                <Area
                  type="monotone"
                  dataKey={s.key}
                  stroke={colors[s.key]}
                  strokeWidth={1.5}
                  fill={colors[s.key]}
                  fillOpacity={0.15}
                  dot={false}
                />
                {tileReferenceLine && (
                  <ReferenceLine
                    y={tileReferenceLine.value}
                    stroke={refColor}
                    strokeDasharray="4 4"
                    strokeWidth={1.5}
                    label={{
                      value: tileReferenceLine.label,
                      position: "insideTopRight",
                      fill: refColor,
                      fontSize: 9,
                    }}
                  />
                )}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        );
      })}
    </div>
  );
}
