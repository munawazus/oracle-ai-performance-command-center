import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Badge, type Tone } from "../components/ui/Badge";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Loading } from "../components/ui/Loading";
import { Table, type Column } from "../components/ui/Table";
import { DatabaseSelector, useDatabases } from "../components/telemetry/DatabaseSelector";
import { apiFetch, errorMessage } from "../lib/api";
import type { Session, SessionsSnapshot } from "../types/api";

/** A single at-a-glance status, combining what today's separate Status/Blocked-by columns
 * require a DBA to cross-reference: an ACTIVE session with a real `blocking_session` (lock
 * contention - the one signal that actually needs attention) reads orange, not the same green
 * as a session that's merely WAITING on a resource like I/O with nothing blocking it (normal
 * operation, not a problem). "Blocked by SID X" stays as its own column for which session. */
export function sessionStatusTone(s: Session): { label: string; tone: Tone } {
  if (s.status !== "ACTIVE") return { label: "Inactive", tone: "neutral" };
  if (s.blocking_session) return { label: "Blocked", tone: "warn" };
  return { label: "Active", tone: "good" };
}

export function formatDurationSeconds(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

/** V$SESSION.LAST_CALL_ET - seconds since this session became ACTIVE (its current call) or
 * since it became INACTIVE, depending on `status`. Without this, a session that just started
 * waiting and one that's been stuck for two hours render identically - the one signal a DBA
 * actually looks for when triaging whether a wait is transient or a real problem. */
export function sessionDurationLabel(s: Session): string {
  if (s.last_call_et === null) return "—";
  const duration = formatDurationSeconds(s.last_call_et);
  return s.status === "ACTIVE" ? `Active for ${duration}` : `Idle for ${duration}`;
}

export function SessionsPage() {
  const databasesQuery = useDatabases();
  const [databaseId, setDatabaseId] = useState("");

  useEffect(() => {
    if (!databaseId && databasesQuery.data?.length) setDatabaseId(databasesQuery.data[0].id);
  }, [databaseId, databasesQuery.data]);

  const sessionsQuery = useQuery({
    queryKey: ["sessions", databaseId],
    queryFn: () => apiFetch<SessionsSnapshot>(`/api/databases/${databaseId}/sessions`),
    enabled: Boolean(databaseId),
    refetchInterval: 15_000,
  });

  const columns: Column<Session>[] = [
    { header: "SID", cell: (s) => s.sid },
    { header: "Username", cell: (s) => s.username ?? "—" },
    {
      header: "Status",
      cell: (s) => {
        const { label, tone } = sessionStatusTone(s);
        return <Badge tone={tone}>{label}</Badge>;
      },
    },
    { header: "Duration", cell: (s) => sessionDurationLabel(s) },
    {
      header: "State",
      cell: (s) => (
        <span title="V$SESSION.STATE - WAITING means blocked on Event right now; WAITED ... means it recently finished a wait and may genuinely be on CPU, even though Event/Wait class still show that last wait.">
          {s.state ?? "—"}
        </span>
      ),
    },
    { header: "SQL ID", cell: (s) => s.sql_id ?? "—" },
    { header: "Event", cell: (s) => s.event ?? "ON CPU" },
    { header: "Wait class", cell: (s) => s.wait_class ?? "—" },
    { header: "Blocked by", cell: (s) => (s.blocking_session ? <Badge tone="crit">SID {s.blocking_session}</Badge> : "—") },
    { header: "Machine / Program", cell: (s) => [s.machine, s.program].filter(Boolean).join(" / ") || "—" },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-end justify-between">
        <DatabaseSelector databaseId={databaseId} onChange={setDatabaseId} databases={databasesQuery.data ?? []} />
        {sessionsQuery.data?.available && (
          <p className="pb-2 text-sm text-slate-500 dark:text-slate-400">
            {sessionsQuery.data.session_count} session(s) as of {new Date(sessionsQuery.data.sampled_at!).toLocaleTimeString()}
          </p>
        )}
      </div>

      {!databaseId && <EmptyState title="Select a database to view its sessions" />}
      {databaseId && sessionsQuery.isPending && <Loading label="Loading sessions…" />}
      {databaseId && sessionsQuery.isError && (
        <ErrorState message={errorMessage(sessionsQuery.error)} onRetry={() => sessionsQuery.refetch()} />
      )}
      {databaseId && sessionsQuery.data && !sessionsQuery.data.available && (
        <EmptyState
          title="No session telemetry yet"
          description={sessionsQuery.data.reason ?? "The collector hasn't sampled this database yet."}
        />
      )}
      {databaseId && sessionsQuery.data?.available && sessionsQuery.data.sessions.length === 0 && (
        <EmptyState title="No active sessions in the latest sample" />
      )}
      {databaseId && sessionsQuery.data?.available && sessionsQuery.data.sessions.length > 0 && (
        <Table columns={columns} rows={sessionsQuery.data.sessions} rowKey={(s) => String(s.sid)} />
      )}
    </div>
  );
}
