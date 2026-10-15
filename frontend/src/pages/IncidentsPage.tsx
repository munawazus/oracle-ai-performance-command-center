import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Drawer } from "../components/ui/Drawer";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Loading } from "../components/ui/Loading";
import { Select } from "../components/ui/Select";
import { Table, type Column } from "../components/ui/Table";
import { useToast } from "../components/ui/Toast";
import { apiFetch, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth-context";
import { SEVERITY_TONE } from "../lib/severity";
import { formatRelativeTime } from "../lib/time";
import type {
  AssignableUser,
  DatabaseGroup,
  Incident,
  IncidentEvent,
  IncidentStatus,
  IntegrationDelivery,
  IntegrationDeliveryStatus,
  MonitoredDatabase,
  NotificationEvent,
  NotificationStatus,
  RcaResult,
} from "../types/api";

const CONFIDENCE_TONE = { low: "warn", medium: "info", high: "good" } as const;

const NOTIFICATION_STATUS_TONE: Record<NotificationStatus, "good" | "warn" | "crit" | "neutral"> = {
  SENT: "good",
  PENDING: "neutral",
  RETRYING: "warn",
  FAILED: "crit",
};

const DELIVERY_STATUS_TONE: Record<IntegrationDeliveryStatus, "good" | "warn" | "crit" | "neutral"> = {
  SENT: "good",
  PENDING: "neutral",
  RETRYING: "warn",
  FAILED: "crit",
};

const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "OPEN", label: "Open" },
  { value: "ACKNOWLEDGED", label: "Acknowledged" },
  { value: "RESOLVED", label: "Resolved" },
];

const STATUS_TONE: Record<IncidentStatus, "crit" | "warn" | "good"> = {
  OPEN: "crit",
  ACKNOWLEDGED: "warn",
  RESOLVED: "good",
};

export function IncidentsPage() {
  const { hasPermission } = useAuth();
  const canManage = hasPermission("incident:manage");
  const canUseAi = hasPermission("ai:query");
  const [statusFilter, setStatusFilter] = useState("");
  const [viewing, setViewing] = useState<Incident | null>(null);

  // Arriving from the Fleet Overview's "N open incident(s)" drill-down link
  // (/incidents?database_id=...) pre-scopes this already-global view to one database.
  const [searchParams, setSearchParams] = useSearchParams();
  const databaseFilter = searchParams.get("database_id") ?? "";
  const [groupFilter, setGroupFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");

  const setDatabaseFilter = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set("database_id", value);
    else next.delete("database_id");
    setSearchParams(next, { replace: true });
  };

  // A one-shot "open this incident's drawer" trigger (from search or the notifications bell,
  // e.g. /incidents?incident_id=...) - fetched directly by id so it works regardless of
  // whatever status/database/group/tag filters happen to be active, then the param is
  // stripped so it doesn't linger as a sticky filter or refight a later row click.
  const incidentIdParam = searchParams.get("incident_id");
  const incidentByIdQuery = useQuery({
    queryKey: ["incident", incidentIdParam],
    queryFn: () => apiFetch<Incident>(`/api/incidents/${incidentIdParam}`),
    enabled: Boolean(incidentIdParam),
  });
  useEffect(() => {
    if (!incidentByIdQuery.data) return;
    setViewing(incidentByIdQuery.data);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("incident_id");
        return next;
      },
      { replace: true },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incidentByIdQuery.data]);

  const filterParams = new URLSearchParams();
  if (statusFilter) filterParams.set("status", statusFilter);
  if (databaseFilter) filterParams.set("monitored_database_id", databaseFilter);
  else {
    if (groupFilter) filterParams.set("group_id", groupFilter);
    if (tagFilter) filterParams.set("tag", tagFilter);
  }

  const incidentsQuery = useQuery({
    queryKey: ["incidents", statusFilter, databaseFilter, groupFilter, tagFilter],
    queryFn: () => apiFetch<Incident[]>(`/api/incidents${filterParams.toString() ? `?${filterParams}` : ""}`),
    refetchInterval: 20_000,
  });

  const databasesQuery = useQuery({
    queryKey: ["databases"],
    queryFn: () => apiFetch<MonitoredDatabase[]>("/api/databases"),
  });

  const groupsQuery = useQuery({
    queryKey: ["database-groups"],
    queryFn: () => apiFetch<DatabaseGroup[]>("/api/database-groups"),
  });

  const databaseName = (id: string) => databasesQuery.data?.find((d) => d.id === id)?.name ?? id;

  const columns: Column<Incident>[] = [
    {
      header: "Severity",
      cell: (row) => <Badge tone={SEVERITY_TONE[row.severity]}>{row.severity}</Badge>,
    },
    { header: "Title", cell: (row) => <span className="font-medium text-slate-900 dark:text-slate-100">{row.title}</span> },
    { header: "Database", cell: (row) => databaseName(row.monitored_database_id) },
    { header: "Status", cell: (row) => <Badge tone={STATUS_TONE[row.status]}>{row.status}</Badge> },
    { header: "Opened", cell: (row) => formatRelativeTime(row.opened_at) },
    { header: "Last evidence", cell: (row) => formatRelativeTime(row.last_evidence_at) },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <p className="max-w-md text-sm text-slate-500 dark:text-slate-400">
          Correlated from anomaly detection - repeated symptoms of the same event attach to one
          incident instead of creating duplicates. This view spans the whole fleet by default.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-44">
            <Select
              label="Database"
              options={[
                { value: "", label: "All databases" },
                ...(databasesQuery.data ?? []).map((d) => ({ value: d.id, label: d.name })),
              ]}
              value={databaseFilter}
              onChange={(e) => setDatabaseFilter(e.target.value)}
            />
          </div>
          <div className="w-44">
            <Select
              label="Group"
              options={[
                { value: "", label: "All groups" },
                ...(groupsQuery.data ?? []).map((g) => ({ value: g.id, label: g.name })),
              ]}
              value={groupFilter}
              disabled={Boolean(databaseFilter)}
              onChange={(e) => setGroupFilter(e.target.value)}
            />
          </div>
          <div className="w-36">
            <Select
              label="Tag"
              options={[
                { value: "", label: "All tags" },
                ...Array.from(new Set((databasesQuery.data ?? []).flatMap((d) => d.tags))).map((t) => ({ value: t, label: t })),
              ]}
              value={tagFilter}
              disabled={Boolean(databaseFilter)}
              onChange={(e) => setTagFilter(e.target.value)}
            />
          </div>
          <div className="w-40">
            <Select
              label="Status"
              options={STATUS_OPTIONS}
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            />
          </div>
        </div>
      </div>

      {incidentsQuery.isPending && <Loading label="Loading incidents…" />}
      {incidentsQuery.isError && (
        <ErrorState message={errorMessage(incidentsQuery.error)} onRetry={() => incidentsQuery.refetch()} />
      )}
      {incidentsQuery.isSuccess && incidentsQuery.data.length === 0 && (
        <EmptyState
          title="No incidents"
          description="Nothing has been correlated into an incident yet. This fills in once anomaly detection has enough history to flag something."
        />
      )}
      {incidentsQuery.isSuccess && incidentsQuery.data.length > 0 && (
        <Table columns={columns} rows={incidentsQuery.data} rowKey={(row) => row.id} onRowClick={setViewing} />
      )}

      {viewing && (
        <IncidentDrawer
          incident={viewing}
          databaseName={databaseName(viewing.monitored_database_id)}
          canManage={canManage}
          canUseAi={canUseAi}
          onClose={() => setViewing(null)}
          onUpdated={(updated) => setViewing(updated)}
        />
      )}
    </div>
  );
}

function IncidentDrawer({
  incident,
  databaseName,
  canManage,
  canUseAi,
  onClose,
  onUpdated,
}: {
  incident: Incident;
  databaseName: string;
  canManage: boolean;
  canUseAi: boolean;
  onClose: () => void;
  onUpdated: (incident: Incident) => void;
}) {
  const { show } = useToast();
  const queryClient = useQueryClient();
  const [resolutionNote, setResolutionNote] = useState("");
  const [assigneeId, setAssigneeId] = useState("");

  const timelineQuery = useQuery({
    queryKey: ["incident-timeline", incident.id],
    queryFn: () => apiFetch<IncidentEvent[]>(`/api/incidents/${incident.id}/timeline`),
  });

  const notificationsQuery = useQuery({
    queryKey: ["incident-notifications", incident.id],
    queryFn: () => apiFetch<NotificationEvent[]>(`/api/notifications/events?incident_id=${incident.id}`),
  });

  const deliveriesQuery = useQuery({
    queryKey: ["incident-integration-deliveries", incident.id],
    queryFn: () => apiFetch<IntegrationDelivery[]>(`/api/integrations/deliveries?incident_id=${incident.id}`),
  });

  const assignableUsersQuery = useQuery({
    queryKey: ["assignable-users"],
    queryFn: () => apiFetch<AssignableUser[]>("/api/incidents/assignable-users"),
    enabled: canManage,
  });

  const rcaMutation = useMutation({
    mutationFn: () => apiFetch<RcaResult>(`/api/incidents/${incident.id}/rca`, { method: "POST" }),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["incidents"] });
    queryClient.invalidateQueries({ queryKey: ["incident-timeline", incident.id] });
  };

  const acknowledgeMutation = useMutation({
    mutationFn: () => apiFetch<Incident>(`/api/incidents/${incident.id}/acknowledge`, { method: "POST" }),
    onSuccess: (updated) => {
      invalidate();
      onUpdated(updated);
      show("Incident acknowledged.", "good");
    },
  });

  const assignMutation = useMutation({
    mutationFn: () =>
      apiFetch<Incident>(`/api/incidents/${incident.id}/assign`, {
        method: "POST",
        body: { assignee_user_id: assigneeId },
      }),
    onSuccess: (updated) => {
      invalidate();
      onUpdated(updated);
      show("Incident assigned.", "good");
    },
  });

  const resolveMutation = useMutation({
    mutationFn: () =>
      apiFetch<Incident>(`/api/incidents/${incident.id}/resolve`, {
        method: "POST",
        body: { note: resolutionNote || undefined },
      }),
    onSuccess: (updated) => {
      invalidate();
      onUpdated(updated);
      show("Incident resolved.", "good");
    },
  });

  const handleAssign = (e: FormEvent) => {
    e.preventDefault();
    if (assigneeId) assignMutation.mutate();
  };

  const handleResolve = (e: FormEvent) => {
    e.preventDefault();
    resolveMutation.mutate();
  };

  return (
    <Drawer title={incident.title} isOpen onClose={onClose}>
      <dl className="flex flex-col gap-3 text-sm">
        <Row label="Database" value={databaseName} />
        <Row label="Severity" value={incident.severity} />
        <Row label="Status" value={incident.status} />
        <Row label="Opened" value={new Date(incident.opened_at).toLocaleString()} />
        <Row label="Last evidence" value={new Date(incident.last_evidence_at).toLocaleString()} />
        {incident.resolved_at && (
          <Row label="Resolved" value={new Date(incident.resolved_at).toLocaleString()} />
        )}
        {incident.resolution_note && <Row label="Resolution note" value={incident.resolution_note} />}
      </dl>

      <h3 className="mt-6 mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Timeline</h3>
      {timelineQuery.isPending && <Loading label="Loading timeline…" />}
      {timelineQuery.isError && (
        <ErrorState message={errorMessage(timelineQuery.error)} onRetry={() => timelineQuery.refetch()} />
      )}
      {timelineQuery.isSuccess && (
        <ol className="flex flex-col gap-2 border-l-2 border-slate-200 pl-4 dark:border-slate-800">
          {timelineQuery.data.map((event) => (
            <li key={event.id} className="text-xs">
              <p className="font-medium text-slate-700 dark:text-slate-300">{event.event_type}</p>
              <p className="text-slate-400 dark:text-slate-500">{new Date(event.occurred_at).toLocaleString()}</p>
            </li>
          ))}
        </ol>
      )}

      <h3 className="mt-6 mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
        Email notifications
      </h3>
      {notificationsQuery.isPending && <Loading label="Loading notification status…" />}
      {notificationsQuery.isError && (
        <ErrorState message={errorMessage(notificationsQuery.error)} onRetry={() => notificationsQuery.refetch()} />
      )}
      {notificationsQuery.isSuccess && notificationsQuery.data.length === 0 && (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          No HIGH/CRITICAL threshold reached for this incident yet - nothing queued.
        </p>
      )}
      {notificationsQuery.isSuccess && notificationsQuery.data.length > 0 && (
        <ul className="flex flex-col gap-2">
          {notificationsQuery.data.map((n) => (
            <li key={n.id} className="rounded-md border border-slate-200 p-2 text-xs dark:border-slate-800">
              <div className="flex items-center justify-between">
                <Badge tone={NOTIFICATION_STATUS_TONE[n.status]}>{n.status}</Badge>
                <span className="text-slate-400 dark:text-slate-500">{n.severity} threshold</span>
              </div>
              <p className="mt-1 text-slate-600 dark:text-slate-400">{n.recipients.join(", ")}</p>
              {n.status === "FAILED" && n.last_error_category && (
                <p className="mt-1 text-status-crit dark:text-red-400">
                  Delivery failed ({n.last_error_category}) after {n.attempt_count} attempt(s).
                </p>
              )}
              {n.status === "RETRYING" && (
                <p className="mt-1 text-status-warn dark:text-amber-400">Retrying - {n.attempt_count} attempt(s) so far.</p>
              )}
              {n.sent_at && <p className="mt-1 text-slate-400 dark:text-slate-500">Sent {new Date(n.sent_at).toLocaleString()}</p>}
            </li>
          ))}
        </ul>
      )}

      <h3 className="mt-6 mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Integration deliveries</h3>
      {deliveriesQuery.isPending && <Loading label="Loading delivery status…" />}
      {deliveriesQuery.isError && (
        <ErrorState message={errorMessage(deliveriesQuery.error)} onRetry={() => deliveriesQuery.refetch()} />
      )}
      {deliveriesQuery.isSuccess && deliveriesQuery.data.length === 0 && (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          No HIGH/CRITICAL threshold reached, or no integrations configured - nothing queued.
        </p>
      )}
      {deliveriesQuery.isSuccess && deliveriesQuery.data.length > 0 && (
        <ul className="flex flex-col gap-2">
          {deliveriesQuery.data.map((d) => (
            <li key={d.id} className="rounded-md border border-slate-200 p-2 text-xs dark:border-slate-800">
              <div className="flex items-center justify-between">
                <Badge tone={DELIVERY_STATUS_TONE[d.status]}>{d.status}</Badge>
                <span className="text-slate-400 dark:text-slate-500">{d.event_type}</span>
              </div>
              {d.status === "FAILED" && d.last_error_category && (
                <p className="mt-1 text-status-crit dark:text-red-400">
                  Delivery failed ({d.last_error_category}) after {d.attempt_count} attempt(s).
                </p>
              )}
              {d.status === "RETRYING" && (
                <p className="mt-1 text-status-warn dark:text-amber-400">Retrying - {d.attempt_count} attempt(s) so far.</p>
              )}
              {d.sent_at && <p className="mt-1 text-slate-400 dark:text-slate-500">Sent {new Date(d.sent_at).toLocaleString()}</p>}
            </li>
          ))}
        </ul>
      )}

      {canUseAi && (
        <div className="mt-6 border-t border-slate-200 pt-4 dark:border-slate-800">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">AI investigation</h3>
            <Button
              variant="secondary"
              size="sm"
              isLoading={rcaMutation.isPending}
              onClick={() => rcaMutation.mutate()}
            >
              {rcaMutation.data ? "Re-investigate" : "Investigate with AI"}
            </Button>
          </div>
          {rcaMutation.isError && <Alert tone="crit">{errorMessage(rcaMutation.error)}</Alert>}
          {rcaMutation.data && <RcaPanel result={rcaMutation.data} />}
        </div>
      )}

      {canManage && incident.status !== "RESOLVED" && (
        <div className="mt-6 flex flex-col gap-4 border-t border-slate-200 pt-4 dark:border-slate-800">
          {incident.status === "OPEN" && (
            <Button
              variant="secondary"
              size="sm"
              isLoading={acknowledgeMutation.isPending}
              onClick={() => acknowledgeMutation.mutate()}
            >
              Acknowledge
            </Button>
          )}
          {acknowledgeMutation.isError && <Alert tone="crit">{errorMessage(acknowledgeMutation.error)}</Alert>}

          <form onSubmit={handleAssign} className="flex items-end gap-2">
            <div className="flex-1">
              <Select
                label="Assign to"
                options={[
                  { value: "", label: assignableUsersQuery.isPending ? "Loading…" : "Select a user" },
                  ...(assignableUsersQuery.data ?? []).map((u) => ({
                    value: u.id,
                    label: `${u.display_name} (${u.email})`,
                  })),
                ]}
                value={assigneeId}
                onChange={(e) => setAssigneeId(e.target.value)}
              />
            </div>
            <Button type="submit" variant="secondary" size="sm" isLoading={assignMutation.isPending} disabled={!assigneeId}>
              Assign
            </Button>
          </form>
          {assignMutation.isError && <Alert tone="crit">{errorMessage(assignMutation.error)}</Alert>}

          <form onSubmit={handleResolve} className="flex flex-col gap-2">
            <label className="text-sm font-medium text-slate-700 dark:text-slate-300" htmlFor="resolution-note">
              Resolution note (optional)
            </label>
            <textarea
              id="resolution-note"
              className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:border-brand-400 dark:focus:ring-brand-400"
              rows={3}
              value={resolutionNote}
              onChange={(e) => setResolutionNote(e.target.value)}
            />
            <Button type="submit" variant="primary" size="sm" isLoading={resolveMutation.isPending}>
              Resolve
            </Button>
            {resolveMutation.isError && <Alert tone="crit">{errorMessage(resolveMutation.error)}</Alert>}
          </form>
        </div>
      )}
    </Drawer>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-400 dark:text-slate-500">{label}</dt>
      <dd className="text-slate-800 dark:text-slate-200">{value}</dd>
    </div>
  );
}

function RcaPanel({ result }: { result: RcaResult }) {
  return (
    <div className="flex flex-col gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm dark:border-slate-800 dark:bg-slate-800">
      <div className="flex items-center justify-between">
        <p className="font-medium text-slate-800 dark:text-slate-200">
          {result.probable_root_cause ?? "No probable cause could be determined from available evidence"}
        </p>
        {result.confidence && <Badge tone={CONFIDENCE_TONE[result.confidence]}>{result.confidence} confidence</Badge>}
      </div>
      {result.evidence_summary && <p className="text-slate-600 dark:text-slate-400">{result.evidence_summary}</p>}

      <RcaList label="Secondary contributors" items={result.secondary_contributors} />
      <RcaList label="Alternative hypotheses" items={result.alternative_hypotheses} />
      <RcaList label="Recommended investigation" items={result.recommended_investigation} />
      <RcaList label="Recommended action" items={result.recommended_action} />
      {result.risk && <Row label="Risk" value={result.risk} />}

      {result.data_completeness_note && (
        <div className="rounded border border-status-warn/30 bg-amber-50 px-2 py-1.5 text-xs text-status-warn dark:border-amber-800/40 dark:bg-amber-950/40 dark:text-amber-400">
          {result.data_completeness_note}
        </div>
      )}
      {result.model && <p className="text-xs text-slate-400 dark:text-slate-500">Generated by {result.model}</p>}
    </div>
  );
}

function RcaList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-slate-400 dark:text-slate-500">{label}</p>
      <ul className="list-inside list-disc text-slate-700 dark:text-slate-300">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
