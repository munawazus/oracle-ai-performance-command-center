import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { Alert } from "../components/ui/Alert";
import { Badge, type Tone } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Select } from "../components/ui/Select";
import { apiFetch, errorMessage } from "../lib/api";
import { SEVERITY_TONE } from "../lib/severity";
import { formatRelativeTime } from "../lib/time";
import type { CopilotAnswer, Incident, IncidentSeverity, MonitoredDatabase, ToolStatus } from "../types/api";

interface Exchange {
  question: string;
  databaseName: string;
  answer?: CopilotAnswer;
  error?: string;
}

/** A tool that ran and found real evidence must read distinctly from one that ran and
 * legitimately found nothing - a DBA should never have to guess which happened from an
 * identical neutral chip the way the old flat `tools_used` badges forced them to. */
function toolStatusTone(status: ToolStatus): { label: string; tone: Tone } {
  if (status.returned_evidence) return { label: status.tool_name, tone: "good" };
  return { label: `${status.tool_name} (no findings)`, tone: "neutral" };
}

function findingSeverityTone(severity: string): Tone {
  return SEVERITY_TONE[severity as IncidentSeverity] ?? "neutral";
}

export function AiCopilotPage() {
  const [searchParams] = useSearchParams();
  const [databaseId, setDatabaseId] = useState(() => searchParams.get("database_id") ?? "");
  const [incidentId] = useState(() => searchParams.get("incident_id") ?? "");
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<Exchange[]>([]);
  // Explicit toggle (never heuristic intent-guessing from the question text) - Activity tab
  // Phase 4b. Fleet mode ignores the database selector entirely, so it's disabled while on.
  const [fleetScope, setFleetScope] = useState(false);

  const databasesQuery = useQuery({
    queryKey: ["databases"],
    queryFn: () => apiFetch<MonitoredDatabase[]>("/api/databases"),
  });

  const incidentQuery = useQuery({
    queryKey: ["incident", incidentId],
    queryFn: () => apiFetch<Incident>(`/api/incidents/${incidentId}`),
    enabled: Boolean(incidentId),
  });

  const askMutation = useMutation({
    mutationFn: (input: { question: string; database_id?: string; incident_id?: string; fleet_scope?: boolean }) =>
      apiFetch<CopilotAnswer>("/api/ai/ask", { method: "POST", body: input }),
  });

  const databaseName = fleetScope
    ? "Whole fleet"
    : (databasesQuery.data?.find((d) => d.id === databaseId)?.name ?? "No database selected");

  // Once an incident is in context, its database is authoritative - locking the selector to it
  // (rather than leaving it independently editable) means the UI itself can never construct
  // the incident_id/database_id mismatch the backend now rejects with a 409.
  useEffect(() => {
    if (incidentQuery.data) setDatabaseId(incidentQuery.data.monitored_database_id);
  }, [incidentQuery.data]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!question.trim() || askMutation.isPending) return;
    const askedQuestion = question;
    setQuestion("");

    try {
      const answer = await askMutation.mutateAsync({
        question: askedQuestion,
        database_id: fleetScope ? undefined : databaseId || undefined,
        incident_id: fleetScope ? undefined : incidentId || undefined,
        fleet_scope: fleetScope || undefined,
      });
      setHistory((prev) => [...prev, { question: askedQuestion, databaseName, answer }]);
    } catch (err) {
      setHistory((prev) => [...prev, { question: askedQuestion, databaseName, error: errorMessage(err) }]);
    }
  };

  return (
    <div className="flex h-full flex-col gap-4">
      {incidentId && (
        <Alert tone="info">
          Investigating incident: <strong>{incidentQuery.data?.title ?? incidentId}</strong>
          {incidentQuery.data && (
            <>
              {" "}
              <Badge tone={SEVERITY_TONE[incidentQuery.data.severity]}>{incidentQuery.data.severity}</Badge>
            </>
          )}
        </Alert>
      )}
      <div className="flex items-end gap-3">
        <div className="w-64">
          <Select
            label="Database context"
            options={[
              { value: "", label: "General question" },
              ...(databasesQuery.data ?? []).map((d) => ({ value: d.id, label: d.name })),
            ]}
            value={databaseId}
            onChange={(e) => setDatabaseId(e.target.value)}
            disabled={Boolean(incidentId) || fleetScope}
          />
        </div>
        <label className="flex items-center gap-2 pb-2 text-sm text-slate-700 dark:text-slate-300">
          <input
            type="checkbox"
            checked={fleetScope}
            disabled={Boolean(incidentId)}
            onChange={(e) => setFleetScope(e.target.checked)}
          />
          Ask about the whole fleet
        </label>
        <p className="pb-2 text-xs text-slate-500 dark:text-slate-400">
          Answers are grounded only in this platform's own collected telemetry - never fabricated.
        </p>
      </div>

      <div className="flex-1 overflow-y-auto rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
        {history.length === 0 && (
          <EmptyState
            title={fleetScope ? "Ask about the whole fleet" : "Ask about a monitored database"}
            description={
              fleetScope
                ? "Try: \"Is anything wrong anywhere?\" or \"What's the worst thing happening right now?\""
                : databaseId
                  ? "Try: \"Is this database healthy right now?\" or \"What's blocking sessions?\""
                  : "Select a database above, then ask a question grounded in its collected telemetry."
            }
          />
        )}
        <div className="flex flex-col gap-4">
          {history.map((exchange, i) => (
            <div key={i} className="flex flex-col gap-2">
              <div className="self-end rounded-lg bg-brand-50 px-3 py-2 text-sm text-brand-900 dark:bg-brand-900/30 dark:text-brand-100">
                <p className="text-xs font-medium text-brand-600 dark:text-brand-400">{exchange.databaseName}</p>
                {exchange.question}
              </div>
              {exchange.error && (
                <div className="self-start max-w-2xl">
                  <ErrorState message={exchange.error} />
                </div>
              )}
              {exchange.answer && (
                <div className="max-w-2xl self-start rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-800 dark:border-slate-800 dark:bg-slate-800 dark:text-slate-200">
                  <p className="whitespace-pre-wrap">{exchange.answer.answer}</p>

                  <div className="mt-2 flex flex-wrap items-center gap-1">
                    {exchange.answer.model && <Badge tone="info">{exchange.answer.model}</Badge>}
                    {exchange.answer.evidence_coverage && (
                      <Badge tone="neutral">evidence: {exchange.answer.evidence_coverage}</Badge>
                    )}
                    {(exchange.answer.tool_statuses.length > 0
                      ? exchange.answer.tool_statuses
                      : exchange.answer.tools_used.map((tool_name) => ({
                          tool_name,
                          called: true,
                          returned_evidence: true,
                          reason: null,
                        }))
                    ).map((status) => {
                      const { label, tone } = toolStatusTone(status);
                      return (
                        <Badge key={status.tool_name} tone={tone}>
                          {label}
                        </Badge>
                      );
                    })}
                  </div>

                  {exchange.answer.findings.length > 0 && (
                    <div className="mt-2 flex flex-col gap-1">
                      {exchange.answer.findings.map((finding) => (
                        <details
                          key={finding.finding_id}
                          className="rounded-md border border-slate-200 bg-white p-2 text-xs dark:border-slate-700 dark:bg-slate-900"
                        >
                          <summary className="flex cursor-pointer items-center gap-2 font-medium text-slate-700 dark:text-slate-300">
                            <Badge tone={findingSeverityTone(finding.severity)}>{finding.severity}</Badge>
                            <span>{finding.summary}</span>
                            {finding.sampled_at && (
                              <span
                                className={
                                  finding.is_stale
                                    ? "ml-auto shrink-0 text-status-warn dark:text-amber-400"
                                    : "ml-auto shrink-0 text-slate-400 dark:text-slate-500"
                                }
                              >
                                {formatRelativeTime(finding.sampled_at)}
                                {finding.is_stale ? " · stale" : ""}
                              </span>
                            )}
                          </summary>
                          <div className="mt-1 flex flex-col gap-0.5 text-slate-600 dark:text-slate-400">
                            {Object.entries(finding.entities).map(([key, value]) => (
                              <span key={key}>
                                {key}: {String(value)}
                              </span>
                            ))}
                            <span className="text-slate-400 dark:text-slate-500">
                              from: {finding.category} ({finding.finding_id})
                            </span>
                          </div>
                        </details>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask a question about this database…"
          disabled={askMutation.isPending}
          className="flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:border-brand-400 dark:focus:ring-brand-400"
        />
        <Button type="submit" isLoading={askMutation.isPending} disabled={!question.trim() || askMutation.isPending}>
          Ask
        </Button>
      </form>
    </div>
  );
}
