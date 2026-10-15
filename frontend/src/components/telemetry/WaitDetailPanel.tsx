import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { Loading } from "../ui/Loading";
import { Table, type Column } from "../ui/Table";
import { apiFetch, errorMessage } from "../../lib/api";
import type { Session, SessionsSnapshot } from "../../types/api";

export interface WaitProfileSummary {
  total: number;
  topClass: string | null;
  topValue: number;
}

const columns: Column<Session>[] = [
  { header: "SID", cell: (s) => s.sid },
  { header: "Username", cell: (s) => s.username ?? "—" },
  { header: "SQL ID", cell: (s) => s.sql_id ?? "—" },
  { header: "Wait class", cell: (s) => s.wait_class ?? "—" },
  { header: "Event", cell: (s) => s.event ?? "ON CPU" },
  { header: "Machine / Program", cell: (s) => [s.machine, s.program].filter(Boolean).join(" / ") || "—" },
];

/** Real sessions producing right-now activity - which SQL ID and which wait event, per
 * session, in the current sample (real Oracle OEM shows this as its own drill-down; this is
 * the honest version of it available from this app's telemetry - a live snapshot, not a
 * historical per-SQL time attribution, since there's no ASH-style sampling to compute that
 * from - see docs/QA_LIVE_FINDINGS.md Finding 8). Same query key SessionsPage/PerformancePage
 * already use, so this shares cache rather than triggering an extra fetch. */
export function WaitDetailPanel({ databaseId, waitProfile }: { databaseId: string; waitProfile: WaitProfileSummary | null }) {
  const sessionsQuery = useQuery({
    queryKey: ["sessions", databaseId],
    queryFn: () => apiFetch<SessionsSnapshot>(`/api/databases/${databaseId}/sessions`),
    enabled: Boolean(databaseId),
    refetchInterval: 15_000,
  });

  // ACTIVE sessions only - an INACTIVE session isn't producing any wait right now by
  // definition. Real non-idle waits surface first; idle-wait ACTIVE sessions (e.g. a client
  // just hasn't sent its next call yet) still show, just sorted after, so nothing real is
  // hidden - only reordered by relevance.
  const activeSessions = useMemo(() => {
    if (!sessionsQuery.data?.available) return [];
    return sessionsQuery.data.sessions
      .filter((s) => s.status === "ACTIVE")
      .slice()
      .sort((a, b) => Number(a.wait_class === "Idle") - Number(b.wait_class === "Idle"));
  }, [sessionsQuery.data]);

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm text-slate-500 dark:text-slate-400">What's producing wait right now</p>
        <p className="text-xs text-slate-400 dark:text-slate-500">
          {waitProfile?.topClass
            ? `Total ${waitProfile.total.toFixed(2)} AAS · dominated by ${waitProfile.topClass} (${waitProfile.topValue.toFixed(2)})`
            : "No activity right now"}
        </p>
      </div>

      {sessionsQuery.isPending && <Loading label="Loading sessions…" />}
      {sessionsQuery.isError && (
        <ErrorState message={errorMessage(sessionsQuery.error)} onRetry={() => sessionsQuery.refetch()} />
      )}
      {sessionsQuery.data && !sessionsQuery.data.available && (
        <EmptyState title="No session telemetry yet" description={sessionsQuery.data.reason ?? undefined} />
      )}
      {sessionsQuery.data?.available && activeSessions.length === 0 && (
        <p className="text-sm text-slate-500 dark:text-slate-400">No active sessions in the latest sample.</p>
      )}
      {sessionsQuery.data?.available && activeSessions.length > 0 && (
        <Table columns={columns} rows={activeSessions} rowKey={(s) => String(s.sid)} />
      )}
    </div>
  );
}
