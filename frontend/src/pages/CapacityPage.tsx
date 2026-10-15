import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Drawer } from "../components/ui/Drawer";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Input } from "../components/ui/Input";
import { Loading } from "../components/ui/Loading";
import { Table, type Column } from "../components/ui/Table";
import { DatabaseSelector, useDatabases } from "../components/telemetry/DatabaseSelector";
import { apiFetch, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth-context";
import { formatBytes } from "../lib/bytes";
import { SEVERITY_TONE } from "../lib/severity";
import { formatRelativeTime } from "../lib/time";
import type {
  CapacityAlert,
  CapacityOverviewResponse,
  CapacityThresholds,
  TablespaceDetailResponse,
  TablespaceUsageRow,
} from "../types/api";

const CONTENTS_LABEL: Record<string, string> = {
  PERMANENT: "Permanent",
  UNDO: "Undo",
  TEMPORARY: "Temp",
};

function pctTone(pct: number, warnPct: number, criticalPct: number): "good" | "warn" | "crit" {
  if (pct >= criticalPct) return "crit";
  if (pct >= warnPct) return "warn";
  return "good";
}

function formatDays(days: number | null): string {
  if (days === null) return "—";
  if (days < 1) return "< 1 day";
  return `${Math.round(days)} day(s)`;
}

/** The percentage that actually drives this tablespace's severity (matching
 * capacity_alerts.py's own preference): % of its real max size when one is known, since %
 * of the current allocated size alone cries wolf for an autoextensible tablespace with real
 * headroom - falling back to % of allocated only when there's no known max size to compare
 * against at all. */
function primaryPct(row: TablespaceUsageRow): { value: number; basis: "max size" | "allocated" } {
  return row.pct_of_max !== null ? { value: row.pct_of_max, basis: "max size" } : { value: row.pct_used, basis: "allocated" };
}

/** What actually made this alert fire, at the value it fired at - so a tablespace that's since
 * recovered (e.g. now reading 58.8%/green while an alert from hours ago is still open, since
 * alerts only resolve after 3 hours of not re-triggering, not the instant the reading improves)
 * doesn't read as a flat contradiction between the row and its own alert badge. Names the basis
 * (% of max size vs. % of allocated) for a PCT_USED alert whenever it's known - severity can be
 * driven by either now, so a bare number alone is ambiguous; omitted only for an alert that
 * predates the basis being tracked at all, never guessed. */
export function alertContextLine(alert: CapacityAlert): string {
  const detail =
    alert.alert_kind === "PCT_USED"
      ? alert.pct_used !== null
        ? `${alert.pct_used.toFixed(1)}% used${alert.pct_basis ? ` of ${alert.pct_basis}` : ""} at detection`
        : null
      : alert.projected_days_to_full !== null
        ? `${formatDays(alert.projected_days_to_full)} at detection`
        : null;
  return detail ? `${formatRelativeTime(alert.last_detected_at)} · ${detail}` : formatRelativeTime(alert.last_detected_at);
}

export function CapacityPage() {
  const { hasPermission } = useAuth();
  const databasesQuery = useDatabases();
  const [databaseId, setDatabaseId] = useState("");
  const [selectedTablespace, setSelectedTablespace] = useState<string | null>(null);
  const [thresholdsOpen, setThresholdsOpen] = useState(false);

  useEffect(() => {
    if (!databaseId && databasesQuery.data?.length) setDatabaseId(databasesQuery.data[0].id);
  }, [databaseId, databasesQuery.data]);

  // The capacity collector runs on its own, much slower interval (1h by default) - polling
  // faster than the backing data changes would just be wasted requests.
  const capacityQuery = useQuery({
    queryKey: ["capacity", databaseId],
    queryFn: () => apiFetch<CapacityOverviewResponse>(`/api/databases/${databaseId}/capacity`),
    enabled: Boolean(databaseId),
    refetchInterval: 600_000,
  });

  const alertsQuery = useQuery({
    queryKey: ["capacity-alerts", databaseId],
    queryFn: () => apiFetch<CapacityAlert[]>(`/api/databases/${databaseId}/capacity-alerts?status=OPEN`),
    enabled: Boolean(databaseId),
    refetchInterval: 600_000,
  });

  // Same query key CapacityThresholdsDrawer uses (shared cache) - hoisted here too so the
  // table's own badge tone can react to a real per-database override instead of a hardcoded
  // 85/95, matching what actually drives the severity these rows' Alert column shows.
  const thresholdsQuery = useQuery({
    queryKey: ["capacity-thresholds", databaseId],
    queryFn: () => apiFetch<CapacityThresholds>(`/api/databases/${databaseId}/capacity-thresholds`),
    enabled: Boolean(databaseId),
  });
  const effectiveWarnPct = thresholdsQuery.data?.warn_pct_used ?? thresholdsQuery.data?.default_warn_pct_used ?? 85;
  const effectiveCriticalPct =
    thresholdsQuery.data?.critical_pct_used ?? thresholdsQuery.data?.default_critical_pct_used ?? 95;

  const sortedRows = [...(capacityQuery.data?.rows ?? [])].sort(
    (a, b) => primaryPct(b).value - primaryPct(a).value,
  );

  // TablespaceUsageRow only carries the open alert's kind/severity (enough for the badge) -
  // the full alert (needed for "when/at what value") already lives in alertsQuery, fetched via
  // the same query this page's summary banner already uses, not a new request.
  const alertsByKey = new Map((alertsQuery.data ?? []).map((a) => [`${a.tablespace_name}:${a.alert_kind}`, a]));

  const columns: Column<TablespaceUsageRow>[] = [
    {
      header: "Tablespace",
      cell: (r) => (
        <div>
          <p className="font-medium text-slate-800 dark:text-slate-200">{r.tablespace_name}</p>
          <p className="text-xs text-slate-400 dark:text-slate-500">{CONTENTS_LABEL[r.contents] ?? r.contents}</p>
        </div>
      ),
    },
    {
      header: "% used",
      cell: (r) => {
        const primary = primaryPct(r);
        return (
          <div className="flex flex-col gap-1">
            <Badge tone={pctTone(primary.value, effectiveWarnPct, effectiveCriticalPct)}>
              {primary.value.toFixed(1)}% used of {primary.basis}
            </Badge>
            <span className="text-xs text-slate-400 dark:text-slate-500">
              {primary.basis === "max size"
                ? `${r.pct_used.toFixed(1)}% used of allocated`
                : "no known max size"}
            </span>
          </div>
        );
      },
    },
    { header: "Used", cell: (r) => formatBytes(r.used_bytes) },
    { header: "Max size", cell: (r) => (r.max_bytes !== null ? formatBytes(r.max_bytes) : "no known max size") },
    {
      header: "Growth / day",
      cell: (r) => (r.growth_available && r.growth_bytes_per_day !== null ? formatBytes(r.growth_bytes_per_day) : "—"),
    },
    {
      header: "Days to full",
      cell: (r) =>
        r.growth_available
          ? r.projected_days_to_full !== null
            ? formatDays(r.projected_days_to_full)
            : r.projection_reason ?? "—"
          : r.growth_reason ?? "not enough history",
    },
    {
      header: "Alert",
      cell: (r) => {
        if (!r.open_alert_severity) return "—";
        const alert = alertsByKey.get(`${r.tablespace_name}:${r.open_alert_kind}`);
        return (
          <div className="flex flex-col gap-1">
            <Badge tone={SEVERITY_TONE[r.open_alert_severity]}>{r.open_alert_kind}</Badge>
            {alert && <span className="text-xs text-slate-400 dark:text-slate-500">{alertContextLine(alert)}</span>}
          </div>
        );
      },
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <DatabaseSelector databaseId={databaseId} onChange={setDatabaseId} databases={databasesQuery.data ?? []} />
        {databaseId && (
          <Button variant="secondary" size="sm" onClick={() => setThresholdsOpen(true)}>
            Alert thresholds
          </Button>
        )}
      </div>

      {!databaseId && <EmptyState title="Select a database to view its capacity" />}

      {databaseId && alertsQuery.isSuccess && alertsQuery.data.length > 0 && (
        <Alert tone="crit">
          {alertsQuery.data.length} open capacity alert(s) -{" "}
          {alertsQuery.data
            .slice(0, 3)
            .map((a) => a.tablespace_name)
            .join(", ")}
          {alertsQuery.data.length > 3 ? ", …" : ""}
        </Alert>
      )}

      {databaseId && capacityQuery.isPending && <Loading label="Loading capacity…" />}
      {databaseId && capacityQuery.isError && (
        <ErrorState message={errorMessage(capacityQuery.error)} onRetry={() => capacityQuery.refetch()} />
      )}
      {databaseId && capacityQuery.data && !capacityQuery.data.available && (
        <EmptyState
          title="Not enough capacity telemetry yet"
          description={capacityQuery.data.reason ?? "The capacity collector hasn't sampled this database yet."}
        />
      )}
      {databaseId && capacityQuery.data?.available && sortedRows.length === 0 && (
        <EmptyState title="No tablespaces reported" />
      )}
      {databaseId && capacityQuery.data?.available && sortedRows.length > 0 && (
        <>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            As of {new Date(capacityQuery.data.sampled_at!).toLocaleString()}.
          </p>
          <Table
            columns={columns}
            rows={sortedRows}
            rowKey={(r) => r.tablespace_name}
            onRowClick={(r) => setSelectedTablespace(r.tablespace_name)}
          />
        </>
      )}

      {databaseId && selectedTablespace && (
        <TablespaceDetailDrawer
          databaseId={databaseId}
          tablespaceName={selectedTablespace}
          onClose={() => setSelectedTablespace(null)}
        />
      )}

      {databaseId && thresholdsOpen && (
        <CapacityThresholdsDrawer
          databaseId={databaseId}
          canEdit={hasPermission("database:edit")}
          onClose={() => setThresholdsOpen(false)}
        />
      )}
    </div>
  );
}

type ThresholdField = "warn_pct_used" | "critical_pct_used" | "warn_days_to_full" | "critical_days_to_full";

const THRESHOLD_FIELDS: { key: ThresholdField; label: string; defaultKey: keyof CapacityThresholds; suffix: string }[] = [
  { key: "warn_pct_used", label: "Warn at % used", defaultKey: "default_warn_pct_used", suffix: "%" },
  { key: "critical_pct_used", label: "Critical at % used", defaultKey: "default_critical_pct_used", suffix: "%" },
  { key: "warn_days_to_full", label: "Warn at days to full", defaultKey: "default_warn_days_to_full", suffix: "day(s)" },
  {
    key: "critical_days_to_full",
    label: "Critical at days to full",
    defaultKey: "default_critical_days_to_full",
    suffix: "day(s)",
  },
];

function CapacityThresholdsDrawer({
  databaseId,
  canEdit,
  onClose,
}: {
  databaseId: string;
  canEdit: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["capacity-thresholds", databaseId],
    queryFn: () => apiFetch<CapacityThresholds>(`/api/databases/${databaseId}/capacity-thresholds`),
  });

  const [draft, setDraft] = useState<Record<ThresholdField, string> | null>(null);

  useEffect(() => {
    if (!query.data) return;
    setDraft({
      warn_pct_used: query.data.warn_pct_used?.toString() ?? "",
      critical_pct_used: query.data.critical_pct_used?.toString() ?? "",
      warn_days_to_full: query.data.warn_days_to_full?.toString() ?? "",
      critical_days_to_full: query.data.critical_days_to_full?.toString() ?? "",
    });
  }, [query.data]);

  const saveMutation = useMutation({
    mutationFn: (payload: Record<ThresholdField, number | null>) =>
      apiFetch<CapacityThresholds>(`/api/databases/${databaseId}/capacity-thresholds`, { method: "PUT", body: payload }),
    onSuccess: (data) => {
      queryClient.setQueryData(["capacity-thresholds", databaseId], data);
      queryClient.invalidateQueries({ queryKey: ["capacity-alerts", databaseId] });
      queryClient.invalidateQueries({ queryKey: ["capacity", databaseId] });
    },
  });

  const save = (overrides?: Partial<Record<ThresholdField, string>>) => {
    if (!draft) return;
    const merged = { ...draft, ...overrides };
    const payload = Object.fromEntries(
      THRESHOLD_FIELDS.map(({ key }) => [key, merged[key].trim() === "" ? null : Number(merged[key])]),
    ) as Record<ThresholdField, number | null>;
    saveMutation.mutate(payload);
    if (overrides) setDraft(merged as Record<ThresholdField, string>);
  };

  return (
    <Drawer title="Alert thresholds" isOpen onClose={onClose}>
      {query.isPending && <Loading label="Loading thresholds…" />}
      {query.isError && <ErrorState message={errorMessage(query.error)} onRetry={() => query.refetch()} />}
      {query.data && draft && (
        <div className="flex flex-col gap-4 text-sm">
          <p className="text-slate-500 dark:text-slate-400">
            {canEdit
              ? "Overrides this database's fleet-wide default thresholds. Leave a field blank to use the default."
              : "Read-only - you don't have permission to edit this database."}
          </p>
          {saveMutation.isError && <Alert tone="crit">{errorMessage(saveMutation.error)}</Alert>}
          {THRESHOLD_FIELDS.map(({ key, label, defaultKey, suffix }) => {
            const override = query.data![key];
            const effective = override ?? query.data![defaultKey];
            const unit = suffix === "%" ? "%" : ` ${suffix}`;
            return (
              <Input
                key={key}
                label={label}
                type="number"
                inputMode="decimal"
                value={draft[key]}
                disabled={!canEdit}
                placeholder={`Default: ${query.data![defaultKey]}${unit}`}
                hint={`Currently applied: ${effective}${unit}${override === null ? " (default)" : " (override)"}`}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              />
            );
          })}
          {canEdit && (
            <div className="flex gap-2">
              <Button size="sm" isLoading={saveMutation.isPending} onClick={() => save()}>
                Save
              </Button>
              <Button
                variant="secondary"
                size="sm"
                isLoading={saveMutation.isPending}
                onClick={() =>
                  save({
                    warn_pct_used: "",
                    critical_pct_used: "",
                    warn_days_to_full: "",
                    critical_days_to_full: "",
                  })
                }
              >
                Reset to defaults
              </Button>
            </div>
          )}
        </div>
      )}
    </Drawer>
  );
}

function TablespaceDetailDrawer({
  databaseId,
  tablespaceName,
  onClose,
}: {
  databaseId: string;
  tablespaceName: string;
  onClose: () => void;
}) {
  const detailQuery = useQuery({
    queryKey: ["capacity-detail", databaseId, tablespaceName],
    queryFn: () =>
      apiFetch<TablespaceDetailResponse>(`/api/databases/${databaseId}/capacity/${tablespaceName}`),
  });

  return (
    <Drawer title={tablespaceName} isOpen onClose={onClose}>
      {detailQuery.isPending && <Loading label="Loading tablespace detail…" />}
      {detailQuery.isError && (
        <ErrorState message={errorMessage(detailQuery.error)} onRetry={() => detailQuery.refetch()} />
      )}
      {detailQuery.isSuccess && !detailQuery.data.available && (
        <EmptyState title="No capacity telemetry for this tablespace" description={detailQuery.data.reason ?? undefined} />
      )}
      {detailQuery.isSuccess && detailQuery.data.available && detailQuery.data.usage && (
        <div className="flex flex-col gap-6 text-sm">
          <dl className="flex flex-col gap-3">
            <Row label="Contents" value={CONTENTS_LABEL[detailQuery.data.contents ?? ""] ?? detailQuery.data.contents ?? "—"} />
            {(() => {
              const primary = primaryPct(detailQuery.data.usage);
              return (
                <>
                  <Row label="% used" value={`${primary.value.toFixed(1)}% used of ${primary.basis}`} />
                  <Row
                    label={primary.basis === "max size" ? "% of allocated" : "% of max size"}
                    value={
                      primary.basis === "max size"
                        ? `${detailQuery.data.usage.pct_used.toFixed(1)}% used`
                        : "no known max size"
                    }
                  />
                </>
              );
            })()}
            <Row label="Used" value={formatBytes(detailQuery.data.usage.used_bytes)} />
            <Row
              label="Max size"
              value={detailQuery.data.usage.max_bytes !== null ? formatBytes(detailQuery.data.usage.max_bytes) : "no known max size"}
            />
            <Row
              label="Growth"
              value={
                detailQuery.data.usage.growth_available
                  ? detailQuery.data.usage.growth_bytes_per_day !== null
                    ? `${formatBytes(detailQuery.data.usage.growth_bytes_per_day)}/day`
                    : "flat or shrinking"
                  : detailQuery.data.usage.growth_reason ?? "not enough history"
              }
            />
            <Row
              label="Projected time to full"
              value={
                detailQuery.data.usage.projected_days_to_full !== null
                  ? formatDays(detailQuery.data.usage.projected_days_to_full)
                  : detailQuery.data.usage.projection_reason ?? "—"
              }
            />
          </dl>

          {detailQuery.data.open_alerts.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Open alerts</h3>
              <ul className="flex flex-col gap-2">
                {detailQuery.data.open_alerts.map((a) => (
                  <li key={a.id} className="rounded-md border border-slate-200 p-2 text-xs dark:border-slate-800">
                    <div className="mb-1 flex items-center gap-2">
                      <Badge tone={SEVERITY_TONE[a.severity]}>{a.severity}</Badge>
                      <span className="text-slate-500 dark:text-slate-400">{a.alert_kind}</span>
                      <span className="text-slate-400 dark:text-slate-500">
                        · last confirmed {formatRelativeTime(a.last_detected_at)}
                      </span>
                    </div>
                    <p className="text-slate-700 dark:text-slate-300">{a.detection_note}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">History</h3>
            {detailQuery.data.history.length === 0 ? (
              <p className="text-xs text-slate-500 dark:text-slate-400">No collection rounds in the recent window.</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {[...detailQuery.data.history].reverse().map((h, i) => (
                  <li key={i} className="rounded-md border border-slate-200 p-2 text-xs dark:border-slate-800">
                    {new Date(h.sampled_at).toLocaleString()} - {formatBytes(h.used_bytes)} used of{" "}
                    {formatBytes(h.allocated_bytes)}
                  </li>
                ))}
              </ul>
            )}
          </div>
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
