import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { Input } from "../../components/ui/Input";
import { Loading } from "../../components/ui/Loading";
import { Table, type Column } from "../../components/ui/Table";
import { apiFetch, errorMessage } from "../../lib/api";
import type { AuditEvent } from "../../types/api";

const PAGE_SIZE = 50;

export function AuditPanel() {
  const [actionPrefix, setActionPrefix] = useState("");
  const [offset, setOffset] = useState(0);

  const query = useQuery({
    queryKey: ["audit-events", actionPrefix, offset],
    queryFn: () => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
      if (actionPrefix) params.set("action_prefix", actionPrefix);
      return apiFetch<AuditEvent[]>(`/api/audit?${params.toString()}`);
    },
  });

  const columns: Column<AuditEvent>[] = [
    { header: "When", cell: (row) => new Date(row.occurred_at).toLocaleString() },
    { header: "Action", cell: (row) => <span className="font-mono text-xs">{row.action}</span> },
    { header: "Actor", cell: (row) => row.actor_email ?? "—" },
    { header: "Target", cell: (row) => (row.target_type ? `${row.target_type}:${row.target_id}` : "—") },
    {
      header: "Result",
      cell: (row) => <Badge tone={row.success ? "good" : "crit"}>{row.success ? "Success" : "Failure"}</Badge>,
    },
    { header: "IP", cell: (row) => row.ip_address ?? "—" },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="max-w-xs">
        <Input
          label="Filter by action prefix"
          placeholder="e.g. auth.login"
          value={actionPrefix}
          onChange={(e) => {
            setOffset(0);
            setActionPrefix(e.target.value);
          }}
        />
      </div>

      {query.isPending && <Loading label="Loading audit events…" />}
      {query.isError && <ErrorState message={errorMessage(query.error)} onRetry={() => query.refetch()} />}
      {query.isSuccess && query.data.length === 0 && <EmptyState title="No matching audit events" />}
      {query.isSuccess && query.data.length > 0 && (
        <>
          <Table columns={columns} rows={query.data} rowKey={(row) => row.id} />
          <div className="flex items-center justify-between">
            <Button
              variant="secondary"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            >
              Previous
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={query.data.length < PAGE_SIZE}
              onClick={() => setOffset((o) => o + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
