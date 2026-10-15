import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { TimeRangeControl, type TimeRange } from "../components/charts/TimeRangeControl";
import { Badge } from "../components/ui/Badge";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Loading } from "../components/ui/Loading";
import { Table, type Column } from "../components/ui/Table";
import { DatabaseSelector, useDatabases } from "../components/telemetry/DatabaseSelector";
import { apiFetch, errorMessage } from "../lib/api";
import { formatRelativeTime } from "../lib/time";
import type { ConfigChangeEvent, ConfigurationSummaryResponse, ParameterChangeItem } from "../types/api";

const CHANGE_TYPE_LABEL: Record<string, string> = {
  newly_set: "Newly set",
  changed: "Changed",
  reverted_to_default: "Reverted to default",
};

const CHANGE_TYPE_TONE: Record<string, "info" | "warn" | "neutral"> = {
  newly_set: "info",
  changed: "warn",
  reverted_to_default: "neutral",
};

/** A quick-glance breakdown by change type, e.g. "2 changed, 1 newly set" - only categories
 * that actually occurred, in the order a DBA is likeliest to care about (a real value change
 * first, since that's the one most likely to be the cause of something; a reversion last,
 * since that's usually a cleanup rather than a new risk). */
export function summarizeChangeCounts(changes: ParameterChangeItem[]): string {
  const counts: Record<string, number> = {};
  for (const c of changes) counts[c.change_type] = (counts[c.change_type] ?? 0) + 1;
  return [
    counts.changed ? `${counts.changed} changed` : null,
    counts.newly_set ? `${counts.newly_set} newly set` : null,
    counts.reverted_to_default ? `${counts.reverted_to_default} reverted to default` : null,
  ]
    .filter((part): part is string => part !== null)
    .join(", ");
}

export function ChangeIntelligencePage() {
  const databasesQuery = useDatabases();
  const [databaseId, setDatabaseId] = useState("");

  useEffect(() => {
    if (!databaseId && databasesQuery.data?.length) setDatabaseId(databasesQuery.data[0].id);
  }, [databaseId, databasesQuery.data]);

  // The Change Intelligence collector runs on its own, much slower interval (1h by default) -
  // polling faster than the backing data changes would just be wasted requests.
  const configQuery = useQuery({
    queryKey: ["configuration", databaseId],
    queryFn: () => apiFetch<ConfigurationSummaryResponse>(`/api/databases/${databaseId}/configuration`),
    enabled: Boolean(databaseId),
    refetchInterval: 600_000,
  });

  // A separate window from configQuery, defaulting wider (24h vs. the app's usual 1h default)
  // - config changes are infrequent, so a 1h default would almost always show nothing here
  // even when there's real history a few hours back.
  const [historyRange, setHistoryRange] = useState<TimeRange>({ hours: 24 });
  const untilParam = historyRange.until ? `&until=${encodeURIComponent(historyRange.until)}` : "";
  const historyQuery = useQuery({
    queryKey: ["configuration-history", databaseId, historyRange.hours, historyRange.until],
    queryFn: () =>
      apiFetch<ConfigChangeEvent[]>(
        `/api/databases/${databaseId}/configuration/history?hours=${historyRange.hours}${untilParam}`,
      ),
    enabled: Boolean(databaseId),
    refetchInterval: historyRange.until ? false : 600_000,
  });

  const columns: Column<ParameterChangeItem>[] = [
    {
      header: "Parameter",
      cell: (c) => <span className="font-medium text-slate-800 dark:text-slate-200">{c.parameter_name}</span>,
    },
    {
      header: "Change",
      cell: (c) => <Badge tone={CHANGE_TYPE_TONE[c.change_type] ?? "neutral"}>{CHANGE_TYPE_LABEL[c.change_type] ?? c.change_type}</Badge>,
    },
    { header: "Previous value", cell: (c) => c.previous_value ?? "(default)" },
    { header: "Current value", cell: (c) => c.current_value ?? "(default)" },
  ];

  const historyColumns: Column<ConfigChangeEvent>[] = [
    {
      header: "When",
      cell: (e) => (
        <span title={new Date(e.detected_at).toLocaleString()}>{formatRelativeTime(e.detected_at)}</span>
      ),
    },
    {
      header: "Parameter",
      cell: (e) => <span className="font-medium text-slate-800 dark:text-slate-200">{e.parameter_name}</span>,
    },
    {
      header: "Change",
      cell: (e) => <Badge tone={CHANGE_TYPE_TONE[e.change_type] ?? "neutral"}>{CHANGE_TYPE_LABEL[e.change_type] ?? e.change_type}</Badge>,
    },
    { header: "Previous value", cell: (e) => e.previous_value ?? "(default)" },
    { header: "Current value", cell: (e) => e.current_value ?? "(default)" },
  ];

  return (
    <div className="flex flex-col gap-4">
      <DatabaseSelector databaseId={databaseId} onChange={setDatabaseId} databases={databasesQuery.data ?? []} />

      {!databaseId && <EmptyState title="Select a database to view its configuration changes" />}

      {databaseId && configQuery.isPending && <Loading label="Loading configuration changes…" />}
      {databaseId && configQuery.isError && (
        <ErrorState message={errorMessage(configQuery.error)} onRetry={() => configQuery.refetch()} />
      )}
      {databaseId && configQuery.data && !configQuery.data.available && (
        <EmptyState
          title="Not enough configuration telemetry yet"
          description={configQuery.data.reason ?? "The configuration collector hasn't sampled this database twice yet."}
        />
      )}
      {databaseId && configQuery.data?.available && configQuery.data.changes.length === 0 && (
        <EmptyState
          title="No configuration changes detected"
          description={`As of ${new Date(configQuery.data.to_sampled_at!).toLocaleString()} (${formatRelativeTime(configQuery.data.to_sampled_at!)}).`}
        />
      )}
      {databaseId && configQuery.data?.available && configQuery.data.changes.length > 0 && (
        <>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {configQuery.data.changes.length} change{configQuery.data.changes.length === 1 ? "" : "s"} (
            {summarizeChangeCounts(configQuery.data.changes)}) since{" "}
            {new Date(configQuery.data.from_sampled_at!).toLocaleString()}, as of{" "}
            {new Date(configQuery.data.to_sampled_at!).toLocaleString()} (
            {formatRelativeTime(configQuery.data.to_sampled_at!)}).
          </p>
          <p className="text-xs text-slate-400 dark:text-slate-500">
            Reflects the value currently in effect only - not whether it would persist across
            an instance restart.
          </p>
          <Table columns={columns} rows={configQuery.data.changes} rowKey={(c) => c.parameter_name} />
        </>
      )}

      {databaseId && (
        <div className="mt-2 flex flex-col gap-4 border-t border-slate-200 pt-4 dark:border-slate-800">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-200">History</h2>
              <p className="text-xs text-slate-400 dark:text-slate-500">
                Every change detected in the selected window, not just the latest round - a
                change that happened and reverted in between still shows as two events.
              </p>
            </div>
            <TimeRangeControl value={historyRange} onChange={setHistoryRange} />
          </div>

          {historyQuery.isPending && <Loading label="Loading history…" />}
          {historyQuery.isError && (
            <ErrorState message={errorMessage(historyQuery.error)} onRetry={() => historyQuery.refetch()} />
          )}
          {historyQuery.isSuccess && historyQuery.data.length === 0 && (
            <EmptyState
              title="No configuration changes in this window"
              description="Try a wider time range, or check back once the collector has run again."
            />
          )}
          {historyQuery.isSuccess && historyQuery.data.length > 0 && (
            <Table
              columns={historyColumns}
              rows={historyQuery.data}
              rowKey={(e) => `${e.parameter_name}-${e.detected_at}`}
            />
          )}
        </div>
      )}
    </div>
  );
}
