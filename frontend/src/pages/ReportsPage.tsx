import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
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
import { DatabaseSelector, useDatabases } from "../components/telemetry/DatabaseSelector";
import { apiFetch, downloadFile, errorMessage } from "../lib/api";
import type { GenerateReportInput, Incident, ReportDetail, ReportSummary, ReportType } from "../types/api";

const REPORT_TYPE_OPTIONS: { value: ReportType; label: string }[] = [
  { value: "DAILY_DBA", label: "Daily DBA report" },
  { value: "INCIDENT", label: "Incident report" },
  { value: "PERFORMANCE", label: "Performance report" },
  { value: "SQL_REGRESSION", label: "SQL regression report" },
  { value: "CAPACITY", label: "Capacity report" },
  { value: "HEALTH", label: "Health report" },
];

const REPORT_TYPE_LABEL: Record<ReportType, string> = Object.fromEntries(
  REPORT_TYPE_OPTIONS.map((o) => [o.value, o.label]),
) as Record<ReportType, string>;

const DATABASE_SCOPED_TYPES: ReportType[] = ["DAILY_DBA", "PERFORMANCE", "SQL_REGRESSION", "CAPACITY", "HEALTH"];
const PERIOD_TYPES: ReportType[] = ["DAILY_DBA", "PERFORMANCE"];

export function ReportsPage() {
  const { show } = useToast();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();

  const [typeFilter, setTypeFilter] = useState<string>("");
  const [databaseFilter, setDatabaseFilter] = useState<string>("");
  const [viewing, setViewing] = useState<ReportSummary | null>(null);

  const [reportType, setReportType] = useState<ReportType>("DAILY_DBA");
  const [databaseId, setDatabaseId] = useState(() => searchParams.get("database_id") ?? "");
  const [incidentId, setIncidentId] = useState("");
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [includeRca, setIncludeRca] = useState(false);

  const databasesQuery = useDatabases();
  const incidentsQuery = useQuery({
    queryKey: ["incidents-for-reports"],
    queryFn: () => apiFetch<Incident[]>("/api/incidents"),
    enabled: reportType === "INCIDENT",
  });

  const queryString = new URLSearchParams({
    ...(typeFilter ? { report_type: typeFilter } : {}),
    ...(databaseFilter ? { database_id: databaseFilter } : {}),
  }).toString();

  const listQuery = useQuery({
    queryKey: ["reports", typeFilter, databaseFilter],
    queryFn: () => apiFetch<ReportSummary[]>(`/api/reports${queryString ? `?${queryString}` : ""}`),
  });

  const generateMutation = useMutation({
    mutationFn: (input: GenerateReportInput) => apiFetch<ReportDetail>("/api/reports", { method: "POST", body: input }),
    onSuccess: (report) => {
      queryClient.invalidateQueries({ queryKey: ["reports"] });
      show(`${REPORT_TYPE_LABEL[report.report_type]} generated.`, "good");
      setViewing(report);
    },
  });

  const databaseName = (id: string | null) => databasesQuery.data?.find((d) => d.id === id)?.name ?? id ?? "—";

  const handleGenerate = (e: FormEvent) => {
    e.preventDefault();
    const input: GenerateReportInput = { report_type: reportType };
    if (DATABASE_SCOPED_TYPES.includes(reportType)) input.database_id = databaseId;
    if (reportType === "INCIDENT") {
      input.incident_id = incidentId;
      input.include_rca = includeRca;
    }
    if (PERIOD_TYPES.includes(reportType)) {
      if (periodStart) input.period_start = new Date(periodStart).toISOString();
      if (periodEnd) input.period_end = new Date(periodEnd).toISOString();
    }
    generateMutation.mutate(input);
  };

  const columns: Column<ReportSummary>[] = [
    { header: "Type", cell: (r) => REPORT_TYPE_LABEL[r.report_type] },
    { header: "Scope", cell: (r) => r.incident_title_snapshot ?? r.database_name_snapshot ?? "—" },
    {
      header: "Data",
      cell: (r) => <Badge tone={r.data_available ? "good" : "warn"}>{r.data_available ? "Available" : "Unavailable"}</Badge>,
    },
    { header: "Generated", cell: (r) => new Date(r.generated_at).toLocaleString() },
    { header: "Requested by", cell: (r) => r.requested_by_email },
  ];

  const canGenerate =
    reportType === "INCIDENT"
      ? Boolean(incidentId)
      : DATABASE_SCOPED_TYPES.includes(reportType)
        ? Boolean(databaseId)
        : true;

  return (
    <div className="flex flex-col gap-6">
      <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <h2 className="mb-3 text-sm font-semibold text-slate-700 dark:text-slate-300">Generate a report</h2>
        <form onSubmit={handleGenerate} className="flex flex-wrap items-end gap-3">
          <div className="w-56">
            <Select
              label="Report type"
              options={REPORT_TYPE_OPTIONS}
              value={reportType}
              onChange={(e) => setReportType(e.target.value as ReportType)}
            />
          </div>

          {DATABASE_SCOPED_TYPES.includes(reportType) && (
            <div className="w-56">
              <DatabaseSelector databaseId={databaseId} onChange={setDatabaseId} databases={databasesQuery.data ?? []} />
            </div>
          )}

          {reportType === "INCIDENT" && (
            <div className="w-64">
              <Select
                label="Incident"
                options={[
                  { value: "", label: incidentsQuery.isPending ? "Loading…" : "Select an incident" },
                  ...(incidentsQuery.data ?? []).map((i) => ({ value: i.id, label: `${i.severity} - ${i.title}` })),
                ]}
                value={incidentId}
                onChange={(e) => setIncidentId(e.target.value)}
              />
            </div>
          )}

          {PERIOD_TYPES.includes(reportType) && (
            <>
              <div>
                <label className="text-sm font-medium text-slate-700 dark:text-slate-300" htmlFor="period-start">
                  Period start (optional)
                </label>
                <input
                  id="period-start"
                  type="datetime-local"
                  className="block rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:border-brand-400 dark:focus:ring-brand-400"
                  value={periodStart}
                  onChange={(e) => setPeriodStart(e.target.value)}
                />
              </div>
              <div>
                <label className="text-sm font-medium text-slate-700 dark:text-slate-300" htmlFor="period-end">
                  Period end (optional)
                </label>
                <input
                  id="period-end"
                  type="datetime-local"
                  className="block rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:border-brand-400 dark:focus:ring-brand-400"
                  value={periodEnd}
                  onChange={(e) => setPeriodEnd(e.target.value)}
                />
              </div>
              <p className="max-w-xs pb-2 text-xs text-slate-400 dark:text-slate-500">Defaults to the last 24 hours. Maximum span: 30 days.</p>
            </>
          )}

          {reportType === "INCIDENT" && (
            <label className="flex items-center gap-2 pb-2 text-sm text-slate-700 dark:text-slate-300">
              <input type="checkbox" checked={includeRca} onChange={(e) => setIncludeRca(e.target.checked)} />
              Include AI root-cause analysis
            </label>
          )}

          <Button type="submit" isLoading={generateMutation.isPending} disabled={!canGenerate}>
            Generate
          </Button>
        </form>
        {generateMutation.isError && (
          <div className="mt-3">
            <Alert tone="crit">{errorMessage(generateMutation.error)}</Alert>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-4">
        <div className="flex items-end gap-3">
          <div className="w-56">
            <Select
              label="Filter by type"
              options={[{ value: "", label: "All types" }, ...REPORT_TYPE_OPTIONS]}
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
            />
          </div>
          <div className="w-56">
            <Select
              label="Filter by database"
              options={[
                { value: "", label: "All databases" },
                ...(databasesQuery.data ?? []).map((d) => ({ value: d.id, label: d.name })),
              ]}
              value={databaseFilter}
              onChange={(e) => setDatabaseFilter(e.target.value)}
            />
          </div>
        </div>

        {listQuery.isPending && <Loading label="Loading reports…" />}
        {listQuery.isError && <ErrorState message={errorMessage(listQuery.error)} onRetry={() => listQuery.refetch()} />}
        {listQuery.isSuccess && listQuery.data.length === 0 && (
          <EmptyState title="No reports yet" description="Generate one above - every report is a real, persisted artifact, not a preview." />
        )}
        {listQuery.isSuccess && listQuery.data.length > 0 && (
          <Table columns={columns} rows={listQuery.data} rowKey={(r) => r.id} onRowClick={setViewing} />
        )}
      </div>

      {viewing && (
        <ReportDrawer
          reportId={viewing.id}
          onClose={() => setViewing(null)}
          databaseName={databaseName}
        />
      )}
    </div>
  );
}

function ReportDrawer({
  reportId,
  onClose,
  databaseName,
}: {
  reportId: string;
  onClose: () => void;
  databaseName: (id: string | null) => string;
}) {
  const { show } = useToast();
  const [downloading, setDownloading] = useState<"csv" | "pdf" | null>(null);

  const reportQuery = useQuery({
    queryKey: ["report", reportId],
    queryFn: () => apiFetch<ReportDetail>(`/api/reports/${reportId}`),
  });

  const handleDownload = async (format: "csv" | "pdf") => {
    setDownloading(format);
    try {
      await downloadFile(`/api/reports/${reportId}/export?format=${format}`, `report-${reportId}.${format}`);
      show(`Downloaded as ${format.toUpperCase()}.`, "good");
    } catch (err) {
      show(errorMessage(err), "crit");
    } finally {
      setDownloading(null);
    }
  };

  return (
    <Drawer title={reportQuery.data ? REPORT_TYPE_LABEL[reportQuery.data.report_type] : "Report"} isOpen onClose={onClose}>
      {reportQuery.isPending && <Loading label="Loading report…" />}
      {reportQuery.isError && <ErrorState message={errorMessage(reportQuery.error)} onRetry={() => reportQuery.refetch()} />}
      {reportQuery.isSuccess && (
        <div className="flex flex-col gap-4 text-sm">
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" isLoading={downloading === "csv"} onClick={() => handleDownload("csv")}>
              Download CSV
            </Button>
            <Button variant="secondary" size="sm" isLoading={downloading === "pdf"} onClick={() => handleDownload("pdf")}>
              Download PDF
            </Button>
          </div>

          <dl className="flex flex-col gap-2">
            <Row label="Database" value={databaseName(reportQuery.data.monitored_database_id)} />
            {reportQuery.data.incident_title_snapshot && (
              <Row label="Incident" value={reportQuery.data.incident_title_snapshot} />
            )}
            <Row
              label="Period"
              value={
                reportQuery.data.period_start
                  ? `${new Date(reportQuery.data.period_start).toLocaleString()} to ${new Date(reportQuery.data.period_end!).toLocaleString()}`
                  : "—"
              }
            />
            <Row label="Generated" value={`${new Date(reportQuery.data.generated_at).toLocaleString()} (${reportQuery.data.timezone})`} />
            <Row label="Requested by" value={reportQuery.data.requested_by_email} />
          </dl>

          {!reportQuery.data.data_available && (
            <Alert tone="warn">This report type is not available yet - see the note below.</Alert>
          )}
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Data completeness</p>
            <p className="text-slate-700 dark:text-slate-300">{reportQuery.data.data_completeness_note}</p>
          </div>
          {reportQuery.data.limitations.length > 0 && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Limitations</p>
              <ul className="list-inside list-disc text-slate-600 dark:text-slate-400">
                {reportQuery.data.limitations.map((lim, i) => (
                  <li key={i}>{lim}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="border-t border-slate-200 pt-3 dark:border-slate-800">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Content</p>
            <ContentSections content={reportQuery.data.content} />
          </div>
        </div>
      )}
    </Drawer>
  );
}

function ContentSections({ content }: { content: Record<string, unknown> }) {
  const entries = Object.entries(content);
  if (entries.length === 0) return <p className="text-slate-500 dark:text-slate-400">No content sections.</p>;
  return (
    <div className="flex flex-col gap-3">
      {entries.map(([key, value]) => (
        <details key={key} className="rounded-md border border-slate-200 bg-slate-50 p-2 dark:border-slate-800 dark:bg-slate-800" open>
          <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-slate-600 dark:text-slate-400">
            {key.replaceAll("_", " ")}
          </summary>
          <div className="mt-2">
            <ContentValue value={value} />
          </div>
        </details>
      ))}
    </div>
  );
}

function humanizeKey(key: string): string {
  return key.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isScalar(value: unknown): boolean {
  return value === null || typeof value !== "object";
}

/** Every report type's `content` is a free-form nested structure (see backend/app/reports/
 * builders.py) - rather than hand-writing a bespoke view per report type per field, this
 * recognizes the handful of shapes that actually recur (an "unavailable" stub, a health-check
 * checklist, an array of same-shaped records, a flat key/value object) and renders each one
 * as real UI. Anything genuinely irregular still falls back to formatted JSON rather than
 * hiding it, but that's now the exception, not the default for every single field. */
function ContentValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="text-slate-400 dark:text-slate-500">—</span>;
  }

  if (isPlainObject(value) && "available" in value && value.available === false) {
    return (
      <p className="rounded border border-dashed border-slate-300 bg-white px-3 py-2 text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
        Not available — {typeof value.reason === "string" ? value.reason : "no data collected for this section."}
      </p>
    );
  }

  if (isPlainObject(value) && Array.isArray((value as { checks?: unknown }).checks)) {
    const checks = (value as { checks: Record<string, unknown>[] }).checks;
    const summary = (value as { summary?: Record<string, unknown> }).summary;
    return (
      <div className="flex flex-col gap-2">
        {summary && (
          <div className="flex flex-wrap gap-2 text-xs text-slate-600 dark:text-slate-400">
            {Object.entries(summary).map(([k, v]) => (
              <span key={k} className="rounded bg-white px-2 py-1 shadow-sm dark:bg-slate-800">
                {humanizeKey(k)}: <strong>{String(v)}</strong>
              </span>
            ))}
          </div>
        )}
        <Table
          columns={[
            { header: "Category", cell: (r) => humanizeKey(String(r.category ?? "")) },
            {
              header: "Status",
              cell: (r) => (
                <Badge tone={r.status === "PASS" ? "good" : r.status === "FAIL" ? "crit" : "neutral"}>
                  {String(r.status ?? "")}
                </Badge>
              ),
            },
            {
              header: "Detail",
              cell: (r) => <span className="text-slate-600 dark:text-slate-400">{String(r.detail ?? "")}</span>,
            },
          ]}
          rows={checks}
          rowKey={(r) => String(r.category)}
        />
      </div>
    );
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-slate-400 dark:text-slate-500">None</span>;
    if (value.every(isScalar)) {
      return (
        <ul className="list-inside list-disc text-sm text-slate-700 dark:text-slate-300">
          {value.map((v, i) => (
            <li key={i}>{String(v)}</li>
          ))}
        </ul>
      );
    }
    if (value.every(isPlainObject)) {
      const rows = value as Record<string, unknown>[];
      const columns = Array.from(new Set(rows.flatMap((r) => Object.keys(r))));
      // A record with a nested object/array field (e.g. an RCA result embedded in an
      // incident's timeline) can't be rendered as one flat table cell - fall back to JSON for
      // just that column set rather than the whole report.
      if (columns.length <= 8 && rows.every((r) => Object.values(r).every(isScalar))) {
        return (
          <Table
            columns={columns.map((c) => ({
              header: humanizeKey(c),
              cell: (r: Record<string, unknown>) => (r[c] === null || r[c] === undefined ? "—" : String(r[c])),
            }))}
            rows={rows}
            rowKey={(r) => JSON.stringify(r)}
          />
        );
      }
    }
    return <RawJson value={value} />;
  }

  if (isPlainObject(value)) {
    const entries = Object.entries(value);
    if (entries.every(([, v]) => isScalar(v))) {
      return (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          {entries.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-slate-500 dark:text-slate-400">{humanizeKey(k)}</dt>
              <dd className="text-slate-800 dark:text-slate-200">{v === null || v === undefined ? "—" : String(v)}</dd>
            </div>
          ))}
        </dl>
      );
    }
    return (
      <div className="flex flex-col gap-2">
        {entries.map(([k, v]) => (
          <div key={k}>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400 dark:text-slate-500">{humanizeKey(k)}</p>
            <ContentValue value={v} />
          </div>
        ))}
      </div>
    );
  }

  return <span className="text-slate-800 dark:text-slate-200">{String(value)}</span>;
}

function RawJson({ value }: { value: unknown }) {
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-white p-2 text-xs text-slate-700 shadow-sm dark:bg-slate-800 dark:text-slate-300">
      {JSON.stringify(value, null, 2)}
    </pre>
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
