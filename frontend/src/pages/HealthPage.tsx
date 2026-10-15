import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Loading } from "../components/ui/Loading";
import { Table, type Column } from "../components/ui/Table";
import { apiFetch, errorMessage } from "../lib/api";
import { formatRelativeTime } from "../lib/time";
import type { CapabilityProfile, CollectorStatus, Incident, MonitoredDatabase } from "../types/api";

export interface HealthRow {
  database: MonitoredDatabase;
  status: CollectorStatus;
  capabilities: CapabilityProfile;
  incidents: Incident[];
}

// Matches humanize_reachability_category's wording (backend/app/services/incident_service.py)
// - same real OracleConnectError.category values, same short-title convention, kept in sync
// deliberately even though the two languages can't share the literal mapping.
const ERROR_CATEGORY_LABEL: Record<string, string> = {
  unreachable: "Unreachable",
  authentication_failed: "Authentication failing",
  unknown_error: "Connection failing",
};

export function humanizeErrorCategory(category: string | null): string {
  if (category === null) return "Unknown reason";
  return ERROR_CATEGORY_LABEL[category] ?? "Connection failing";
}

// Capability facts (version, edition, RAC, CPU core count) genuinely don't change often - a
// database doesn't get re-architected weekly - so this is long enough not to nag about a
// database probed a few days ago, short enough to catch one nobody's re-checked in months.
const CAPABILITY_STALE_AFTER_DAYS = 30;

export function isCapabilityStale(lastProbedAt: string): boolean {
  const ageDays = (Date.now() - new Date(lastProbedAt).getTime()) / (1000 * 60 * 60 * 24);
  return ageDays > CAPABILITY_STALE_AFTER_DAYS;
}

/** The real reachability/anomaly incident a failing collector row would have opened, if any -
 * a database can accumulate several over time, so this is the one to link to, not the whole list. */
export function newestOpenIncident(incidents: Incident[]): Incident | null {
  const open = incidents.filter((i) => i.status !== "RESOLVED");
  if (open.length === 0) return null;
  return open.reduce((newest, i) => (new Date(i.last_evidence_at) > new Date(newest.last_evidence_at) ? i : newest));
}

/** `edition_hint` (DBA-entered at registration) and `detected_edition` (read straight off the
 * live V$VERSION banner) share the same "SE2" | "EE" | "UNKNOWN" domain
 * (backend/app/oracle/capability_probe.py) - a real disagreement only when both sides actually
 * assert something, so an unset hint or an as-yet-unprobed database is never flagged as a mismatch. */
export function editionMismatch(editionHint: string, detectedEdition: string | null): boolean {
  if (detectedEdition === null || detectedEdition === "UNKNOWN" || editionHint === "UNKNOWN") return false;
  return editionHint !== detectedEdition;
}

/** A single database is exactly one of these three - never double-counted, matching the same
 * three branches the Collector column itself renders. */
export function summarizeFleetHealth(rows: HealthRow[]): { healthy: number; failing: number; notYetCollecting: number } {
  const counts = { healthy: 0, failing: 0, notYetCollecting: 0 };
  for (const r of rows) {
    if (r.status.last_run_at === null) counts.notYetCollecting++;
    else if (r.status.consecutive_failures > 0) counts.failing++;
    else counts.healthy++;
  }
  return counts;
}

export function HealthPage() {
  const databasesQuery = useQuery({
    queryKey: ["databases"],
    queryFn: () => apiFetch<MonitoredDatabase[]>("/api/databases"),
  });

  const healthQuery = useQuery({
    queryKey: ["fleet-health", databasesQuery.data?.map((d) => d.id)],
    enabled: Boolean(databasesQuery.data),
    queryFn: async (): Promise<HealthRow[]> => {
      const databases = databasesQuery.data ?? [];
      return Promise.all(
        databases.map(async (database) => {
          const [status, capabilities] = await Promise.all([
            apiFetch<CollectorStatus>(`/api/databases/${database.id}/collector-status`),
            apiFetch<CapabilityProfile>(`/api/databases/${database.id}/capabilities`),
          ]);
          const incidents =
            status.consecutive_failures > 0
              ? await apiFetch<Incident[]>(`/api/incidents?monitored_database_id=${database.id}`)
              : [];
          return { database, status, capabilities, incidents };
        }),
      );
    },
  });

  const columns: Column<HealthRow>[] = [
    {
      header: "Database",
      cell: (r) => <span className="font-medium text-slate-900 dark:text-slate-100">{r.database.name}</span>,
    },
    { header: "Edition", cell: (r) => <Badge tone="info">{r.database.edition_hint}</Badge> },
    {
      header: "Collector",
      cell: (r) => {
        if (r.status.last_run_at === null) return <Badge tone="neutral">Not yet collecting</Badge>;
        if (r.status.consecutive_failures > 0) {
          const incident = newestOpenIncident(r.incidents);
          return (
            <div className="flex flex-col gap-1">
              <Badge tone="crit">Failing ({r.status.consecutive_failures})</Badge>
              <span className="text-xs text-slate-400 dark:text-slate-500">
                {humanizeErrorCategory(r.status.last_error_category)}
                {r.status.last_failure_at && ` · since ${formatRelativeTime(r.status.last_failure_at)}`}
              </span>
              {incident && (
                <Link
                  to={`/incidents?incident_id=${incident.id}`}
                  className="text-xs font-medium text-brand-600 hover:underline dark:text-brand-400"
                >
                  View incident
                </Link>
              )}
            </div>
          );
        }
        return <Badge tone="good">Healthy - {formatRelativeTime(r.status.last_success_at!)}</Badge>;
      },
    },
    {
      header: "Capabilities probed",
      cell: (r) => {
        if (!r.capabilities.last_probed_at) return <Badge tone="neutral">Never probed</Badge>;
        const stale = isCapabilityStale(r.capabilities.last_probed_at);
        return (
          <div className="flex flex-col gap-1">
            <Badge tone={stale ? "warn" : "good"}>{formatRelativeTime(r.capabilities.last_probed_at)}</Badge>
            {stale && (
              <span className="text-xs text-slate-400 dark:text-slate-500">Consider re-probing</span>
            )}
          </div>
        );
      },
    },
    {
      header: "Detected edition",
      cell: (r) => {
        const detected = r.capabilities.detected_edition;
        if (detected === null) return "Unknown";
        if (!editionMismatch(r.database.edition_hint, detected)) return detected;
        return (
          <div className="flex flex-col gap-1">
            <Badge tone="warn">{detected}</Badge>
            <span className="text-xs text-slate-400 dark:text-slate-500">
              Doesn't match registered edition ({r.database.edition_hint})
            </span>
          </div>
        );
      },
    },
    { header: "RAC", cell: (r) => (r.capabilities.is_rac === null ? "Unknown" : r.capabilities.is_rac ? "Yes" : "No") },
  ];

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">
        Fleet-wide collector and capability-probe status across every registered database.
      </p>

      {(databasesQuery.isPending || (databasesQuery.isSuccess && healthQuery.isPending)) && <Loading label="Loading fleet health…" />}
      {databasesQuery.isError && <ErrorState message={errorMessage(databasesQuery.error)} onRetry={() => databasesQuery.refetch()} />}
      {healthQuery.isError && <ErrorState message={errorMessage(healthQuery.error)} onRetry={() => healthQuery.refetch()} />}
      {databasesQuery.isSuccess && databasesQuery.data.length === 0 && <EmptyState title="No databases registered yet" />}
      {healthQuery.isSuccess && healthQuery.data.length > 0 && (
        <>
          {(() => {
            const counts = summarizeFleetHealth(healthQuery.data);
            const failingRows = healthQuery.data.filter(
              (r) => r.status.last_run_at !== null && r.status.consecutive_failures > 0,
            );
            return (
              <>
                {failingRows.length > 0 && (
                  <Alert tone="crit">
                    {failingRows.length} of {healthQuery.data.length} database(s) failing to collect -{" "}
                    {failingRows
                      .slice(0, 3)
                      .map((r) => r.database.name)
                      .join(", ")}
                    {failingRows.length > 3 ? ", …" : ""}
                  </Alert>
                )}
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  {healthQuery.data.length} database{healthQuery.data.length === 1 ? "" : "s"} registered -{" "}
                  {counts.healthy} healthy
                  {counts.failing > 0 ? `, ${counts.failing} failing` : ""}
                  {counts.notYetCollecting > 0 ? `, ${counts.notYetCollecting} not yet collecting` : ""}.
                </p>
              </>
            );
          })()}
          <Table columns={columns} rows={healthQuery.data} rowKey={(r) => r.database.id} />
        </>
      )}
    </div>
  );
}
