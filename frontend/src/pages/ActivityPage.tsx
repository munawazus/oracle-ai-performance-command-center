import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { TimeRangeControl, type TimeRange } from "../components/charts/TimeRangeControl";
import { Alert } from "../components/ui/Alert";
import { Badge, type Tone } from "../components/ui/Badge";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Loading } from "../components/ui/Loading";
import { Table, type Column } from "../components/ui/Table";
import { apiFetch, errorMessage } from "../lib/api";
import { SEVERITY_TONE } from "../lib/severity";
import { formatRelativeTime } from "../lib/time";
import type { ActivityEvent, ActivityEventCategory, CapacityTrend, FleetOverallStatus, FleetVitalsRow } from "../types/api";

// Phase 1 of the Activity tab (Master Plan follow-up): the fleet-wide "what's happening right
// now" pulse strip - one row per active monitored database, reusing the exact same deterministic
// health scoring already proven for the single-database Health report
// (backend/app/reports/health_check.py's run_health_checks/summarize_health).

const STATUS_TONE: Record<FleetOverallStatus, Tone> = {
  PASS: "good",
  WARN: "warn",
  FAIL: "crit",
  UNAVAILABLE: "neutral",
};

const STATUS_LABEL: Record<FleetOverallStatus, string> = {
  PASS: "Healthy",
  WARN: "Needs attention",
  FAIL: "Failing",
  UNAVAILABLE: "Not enough data",
};

const CAPACITY_TREND_TONE: Record<CapacityTrend, Tone> = {
  stable: "good",
  warning: "warn",
  critical: "crit",
};

// Phase 2: the unified, fleet-wide event timeline - merges anomalies, SQL regressions,
// capacity alerts, and incident lifecycle events (backend/app/reports/activity_feed.py) into
// one time-sorted list. Deliberately does not imply regressions/capacity alerts now escalate
// to Incidents - that stays exactly as decoupled as it was before this tab existed.

const CATEGORY_LABEL: Record<ActivityEventCategory, string> = {
  anomaly: "Anomaly",
  sql_regression: "SQL regression",
  capacity_alert: "Capacity alert",
  incident_opened: "Incident opened",
  incident_resolved: "Incident resolved",
  storm: "Storm",
};

// Every other category shares one neutral tone (severity already carries its own badge) - a
// storm is the one category that should visually pop on its own, since it's a cross-database
// correlation signal, not just a louder version of a single-database event.
const CATEGORY_TONE: Partial<Record<ActivityEventCategory, Tone>> = { storm: "crit" };

/** Where a timeline row's summary should deep-link to - the same "database_id"/"incident_id"
 * URL-param pattern IncidentsPage/InvestigationDrawer already use elsewhere. A storm spans
 * multiple databases, so it never gets a single link - see the expandable disclosure instead. */
function activityEventLink(e: ActivityEvent): string {
  switch (e.category) {
    case "sql_regression":
      return `/sql-intelligence?database_id=${e.database_id}`;
    case "capacity_alert":
      return `/capacity?database_id=${e.database_id}`;
    case "incident_opened":
    case "incident_resolved":
      return `/incidents?incident_id=${e.entities.incident_id}`;
    case "anomaly":
      return e.entities.incident_id
        ? `/incidents?incident_id=${e.entities.incident_id}`
        : `/performance?database_id=${e.database_id}`;
    case "storm":
      return "";
  }
}

export function ActivityPage() {
  const vitalsQuery = useQuery({
    queryKey: ["activity-fleet-pulse"],
    queryFn: () => apiFetch<FleetVitalsRow[]>("/api/activity/fleet-pulse"),
    refetchInterval: 15_000,
  });

  const [range, setRange] = useState<TimeRange>({ hours: 1 });
  const feedQuery = useQuery({
    queryKey: ["activity-feed", range.hours, range.until],
    queryFn: () => {
      const untilDate = range.until ? new Date(range.until) : new Date();
      const sinceDate = new Date(untilDate.getTime() - range.hours * 3_600_000);
      const untilParam = range.until ? `&until=${encodeURIComponent(range.until)}` : "";
      return apiFetch<ActivityEvent[]>(
        `/api/activity/feed?since=${encodeURIComponent(sinceDate.toISOString())}${untilParam}&limit=200`,
      );
    },
    refetchInterval: range.until ? false : 15_000,
  });

  const columns: Column<FleetVitalsRow>[] = [
    {
      header: "Database",
      cell: (r) => (
        <Link
          to={`/performance?database_id=${r.database_id}`}
          className="font-medium text-slate-900 hover:underline dark:text-slate-100"
        >
          {r.database_name}
        </Link>
      ),
    },
    {
      header: "Status",
      cell: (r) => <Badge tone={STATUS_TONE[r.overall_status]}>{STATUS_LABEL[r.overall_status]}</Badge>,
    },
    {
      header: "Open incidents",
      cell: (r) =>
        r.open_incident_count > 0 ? (
          <Link
            to={`/incidents?database_id=${r.database_id}`}
            className="font-medium text-brand-600 hover:underline dark:text-brand-400"
          >
            {r.open_incident_count}
          </Link>
        ) : (
          <span className="text-slate-400 dark:text-slate-500">0</span>
        ),
    },
    {
      header: "Blocking",
      cell: (r) =>
        r.blocking_present ? (
          <Link to={`/blocking-locks?database_id=${r.database_id}`} className="hover:underline">
            <Badge tone="crit">Present</Badge>
          </Link>
        ) : (
          <Badge tone="good">None</Badge>
        ),
    },
    {
      header: "Capacity",
      cell: (r) => (
        <Link to={`/capacity?database_id=${r.database_id}`} className="hover:underline">
          <Badge tone={CAPACITY_TREND_TONE[r.capacity_trend]}>{r.capacity_trend}</Badge>
        </Link>
      ),
    },
  ];

  const eventColumns: Column<ActivityEvent>[] = [
    {
      header: "When",
      cell: (e) => (
        <span title={new Date(e.occurred_at).toLocaleString()}>{formatRelativeTime(e.occurred_at)}</span>
      ),
    },
    {
      header: "Database",
      cell: (e) =>
        e.category === "storm" ? (
          <span className="text-slate-500 dark:text-slate-400">{e.database_name}</span>
        ) : (
          <Link
            to={`/performance?database_id=${e.database_id}`}
            className="font-medium text-slate-900 hover:underline dark:text-slate-100"
          >
            {e.database_name}
          </Link>
        ),
    },
    {
      header: "Category",
      cell: (e) => <Badge tone={CATEGORY_TONE[e.category] ?? "neutral"}>{CATEGORY_LABEL[e.category]}</Badge>,
    },
    { header: "Severity", cell: (e) => <Badge tone={SEVERITY_TONE[e.severity]}>{e.severity}</Badge> },
    {
      header: "Summary",
      cell: (e) =>
        e.category === "storm" ? (
          <details>
            <summary className="cursor-pointer hover:underline">{e.summary}</summary>
            <ul className="mt-1 flex flex-col gap-0.5 pl-4 text-xs text-slate-500 dark:text-slate-400">
              {(e.entities.database_ids as string[]).map((id, i) => (
                <li key={id}>
                  <Link to={`/performance?database_id=${id}`} className="hover:underline">
                    {(e.entities.database_names as string[])[i]}
                  </Link>
                </li>
              ))}
            </ul>
          </details>
        ) : (
          <Link to={activityEventLink(e)} className="hover:underline">
            {e.summary}
          </Link>
        ),
    },
  ];

  const failingCount = vitalsQuery.data?.filter((r) => r.overall_status === "FAIL").length ?? 0;

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">
        A live, fleet-wide pulse across every active monitored database - refreshes automatically.
      </p>

      {vitalsQuery.isPending && <Loading label="Loading fleet activity…" />}
      {vitalsQuery.isError && (
        <ErrorState message={errorMessage(vitalsQuery.error)} onRetry={() => vitalsQuery.refetch()} />
      )}
      {vitalsQuery.isSuccess && vitalsQuery.data.length === 0 && (
        <EmptyState title="No active databases yet" description="Register a database to see it here." />
      )}
      {vitalsQuery.isSuccess && vitalsQuery.data.length > 0 && (
        <>
          {failingCount > 0 && (
            <Alert tone="crit">
              {failingCount} of {vitalsQuery.data.length} database(s) are failing a health check right now.
            </Alert>
          )}
          <Table columns={columns} rows={vitalsQuery.data} rowKey={(r) => r.database_id} />
        </>
      )}

      <div className="mt-2 flex flex-col gap-4 border-t border-slate-200 pt-4 dark:border-slate-800">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-200">Timeline</h2>
            <p className="text-xs text-slate-400 dark:text-slate-500">
              Anomalies, SQL regressions, capacity alerts, and incident lifecycle events across
              every active database, merged into one feed.
            </p>
          </div>
          <TimeRangeControl value={range} onChange={setRange} />
        </div>

        {feedQuery.isPending && <Loading label="Loading activity…" />}
        {feedQuery.isError && (
          <ErrorState message={errorMessage(feedQuery.error)} onRetry={() => feedQuery.refetch()} />
        )}
        {feedQuery.isSuccess && feedQuery.data.length === 0 && (
          <EmptyState
            title="No activity in this window"
            description="Try a wider time range, or check back once more telemetry has been collected."
          />
        )}
        {feedQuery.isSuccess && feedQuery.data.length > 0 && (
          <Table columns={eventColumns} rows={feedQuery.data} rowKey={(e) => e.event_id} />
        )}
      </div>
    </div>
  );
}
