import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { Loading } from "../ui/Loading";
import { apiFetch, errorMessage } from "../../lib/api";
import { useInvestigation } from "../../lib/investigation-context";
import type { Incident } from "../../types/api";

export function InvestigationDrawer() {
  const { isOpen, close, currentDatabaseId, currentDatabaseName } = useInvestigation();
  const navigate = useNavigate();

  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [isOpen, close]);

  const incidentsQuery = useQuery({
    queryKey: ["incidents", "investigation", currentDatabaseId],
    queryFn: () => apiFetch<Incident[]>(`/api/incidents?monitored_database_id=${currentDatabaseId}`),
    enabled: isOpen && Boolean(currentDatabaseId),
    refetchInterval: isOpen ? 15_000 : false,
  });

  const openIncidents = (incidentsQuery.data ?? [])
    .filter((i) => i.status !== "RESOLVED")
    .sort((a, b) => new Date(b.last_evidence_at).getTime() - new Date(a.last_evidence_at).getTime());
  const newestOpenIncident = openIncidents[0] ?? null;

  return (
    <div
      data-testid="investigation-drawer"
      role="complementary"
      aria-label="Investigation"
      aria-hidden={!isOpen}
      className={`fixed right-0 top-14 bottom-0 z-40 w-[360px] overflow-y-auto border-l border-slate-200 bg-white p-6 shadow-xl transition-transform dark:border-slate-800 dark:bg-slate-900 ${
        isOpen ? "translate-x-0 pointer-events-auto" : "translate-x-full pointer-events-none"
      }`}
    >
      <div className="mb-4 flex items-start justify-between gap-4">
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Investigation</h2>
        <button
          type="button"
          onClick={close}
          aria-label="Close panel"
          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-300"
        >
          ✕
        </button>
      </div>

      {!currentDatabaseId && (
        <EmptyState
          title="No database selected yet"
          description="Select a database on any page to get started."
        />
      )}

      {currentDatabaseId && (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Current database: <span className="font-medium text-slate-800 dark:text-slate-200">{currentDatabaseName}</span>
          </p>

          {incidentsQuery.isPending && <Loading label="Loading incidents…" />}
          {incidentsQuery.isError && (
            <ErrorState message={errorMessage(incidentsQuery.error)} onRetry={() => incidentsQuery.refetch()} />
          )}
          {incidentsQuery.isSuccess && (
            <p className="text-sm text-slate-500 dark:text-slate-400">
              {openIncidents.length} open incident{openIncidents.length === 1 ? "" : "s"} for this database.
            </p>
          )}

          <div className="flex flex-col gap-2">
            <Button
              variant="secondary"
              disabled={!newestOpenIncident}
              title={newestOpenIncident ? undefined : "No open incidents for this database"}
              onClick={() => newestOpenIncident && navigate(`/incidents?incident_id=${newestOpenIncident.id}`)}
            >
              Open Incident
            </Button>
            <Button
              variant="secondary"
              onClick={() =>
                navigate(
                  newestOpenIncident
                    ? `/ai-copilot?database_id=${currentDatabaseId}&incident_id=${newestOpenIncident.id}`
                    : `/ai-copilot?database_id=${currentDatabaseId}`,
                )
              }
            >
              {newestOpenIncident ? "Ask AI Copilot about this incident" : "Ask AI Copilot"}
            </Button>
            <Button variant="secondary" onClick={() => navigate(`/reports?database_id=${currentDatabaseId}`)}>
              Generate Report
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
