import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Badge } from "../components/ui/Badge";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Loading } from "../components/ui/Loading";
import { Select } from "../components/ui/Select";
import { apiFetch, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth-context";
import { formatRelativeTime } from "../lib/time";
import type { CollectorStatus, DatabaseGroup, Incident, MonitoredDatabase } from "../types/api";

export function OverviewPage() {
  const { hasPermission } = useAuth();
  const canView = hasPermission("database:view");
  const canViewIncidents = hasPermission("incident:view");
  const [groupFilter, setGroupFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");

  const groupsQuery = useQuery({
    queryKey: ["database-groups"],
    queryFn: () => apiFetch<DatabaseGroup[]>("/api/database-groups"),
    enabled: canView,
  });

  const params = new URLSearchParams();
  if (groupFilter) params.set("group_id", groupFilter);
  if (tagFilter) params.set("tag", tagFilter);
  const query = useQuery({
    queryKey: ["databases", groupFilter, tagFilter],
    queryFn: () => apiFetch<MonitoredDatabase[]>(`/api/databases${params.toString() ? `?${params}` : ""}`),
    enabled: canView,
  });

  const incidentsQuery = useQuery({
    queryKey: ["incidents", "fleet-overview", groupFilter, tagFilter],
    queryFn: () => apiFetch<Incident[]>(`/api/incidents?status=OPEN${groupFilter ? `&group_id=${groupFilter}` : ""}${tagFilter ? `&tag=${tagFilter}` : ""}`),
    enabled: canView && canViewIncidents,
    refetchInterval: 20_000,
  });

  if (!canView) {
    return (
      <EmptyState
        title="Nothing to show yet"
        description="Your role doesn't include database:view, so there's no registered-database summary to display here."
      />
    );
  }

  if (query.isPending) return <Loading label="Loading registered databases…" />;
  if (query.isError) return <ErrorState message={errorMessage(query.error)} onRetry={() => query.refetch()} />;

  const databases = query.data;
  const openIncidentCounts = new Map<string, number>();
  for (const incident of incidentsQuery.data ?? []) {
    openIncidentCounts.set(incident.monitored_database_id, (openIncidentCounts.get(incident.monitored_database_id) ?? 0) + 1);
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          {databases.length} database{databases.length === 1 ? "" : "s"}
          {groupFilter || tagFilter ? " matching this filter." : " registered."}
        </p>
        <div className="flex gap-3">
          <div className="w-48">
            <Select
              label="Group"
              options={[{ value: "", label: "All groups" }, ...(groupsQuery.data ?? []).map((g) => ({ value: g.id, label: g.name }))]}
              value={groupFilter}
              onChange={(e) => setGroupFilter(e.target.value)}
            />
          </div>
          <div className="w-40">
            <Select
              label="Tag"
              options={[
                { value: "", label: "All tags" },
                ...Array.from(new Set(databases.flatMap((d) => d.tags))).map((t) => ({ value: t, label: t })),
              ]}
              value={tagFilter}
              onChange={(e) => setTagFilter(e.target.value)}
            />
          </div>
        </div>
      </div>

      {databases.length === 0 && (
        <EmptyState
          title={groupFilter || tagFilter ? "No databases match this filter" : "No databases registered yet"}
          description={
            groupFilter || tagFilter
              ? "Try a different group or tag."
              : "Register an Oracle database to start onboarding it. Once collection begins, its status shows up here."
          }
          action={
            !groupFilter && !tagFilter && hasPermission("database:add") ? (
              <Link to="/databases" className="text-sm font-medium text-brand-600 hover:underline dark:text-brand-400">
                Go to Databases →
              </Link>
            ) : undefined
          }
        />
      )}

      {databases.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {databases.map((db) => (
            <DatabaseOverviewCard
              key={db.id}
              database={db}
              openIncidentCount={canViewIncidents ? (openIncidentCounts.get(db.id) ?? 0) : null}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function DatabaseOverviewCard({
  database,
  openIncidentCount,
}: {
  database: MonitoredDatabase;
  openIncidentCount: number | null;
}) {
  const statusQuery = useQuery({
    queryKey: ["collector-status", database.id],
    queryFn: () => apiFetch<CollectorStatus>(`/api/databases/${database.id}/collector-status`),
    refetchInterval: 15_000,
  });

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-2">
        <p className="font-semibold text-slate-900 dark:text-slate-100">{database.name}</p>
        <Badge tone={database.is_active ? "good" : "neutral"}>{database.is_active ? "Active" : "Inactive"}</Badge>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        {database.host}:{database.port}/{database.service_name}
      </p>
      {database.tags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {database.tags.map((t) => (
            <Badge key={t} tone="neutral">
              {t}
            </Badge>
          ))}
        </div>
      )}
      <div className="mt-3 flex items-center justify-between">
        <Badge tone="info">{database.edition_hint}</Badge>
        <CollectorStatusBadge status={statusQuery.data} isLoading={statusQuery.isPending} />
      </div>
      {openIncidentCount !== null && (
        <Link
          to={`/incidents?database_id=${database.id}`}
          className="mt-3 block text-xs font-medium text-brand-600 hover:underline dark:text-brand-400"
        >
          {openIncidentCount > 0 ? `${openIncidentCount} open incident(s) →` : "No open incidents →"}
        </Link>
      )}
    </div>
  );
}

function CollectorStatusBadge({ status, isLoading }: { status?: CollectorStatus; isLoading: boolean }) {
  if (isLoading || !status) return <Badge tone="neutral">Checking…</Badge>;

  if (status.last_run_at === null) {
    return <Badge tone="neutral">Not yet collecting</Badge>;
  }
  if (status.consecutive_failures > 0) {
    return <Badge tone="crit">Collection failing ({status.consecutive_failures})</Badge>;
  }
  return (
    <Badge tone="good">
      Collecting - last {status.last_success_at ? formatRelativeTime(status.last_success_at) : "unknown"}
    </Badge>
  );
}
