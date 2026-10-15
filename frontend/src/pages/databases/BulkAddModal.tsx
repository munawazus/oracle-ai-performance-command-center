import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { Alert } from "../../components/ui/Alert";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Modal } from "../../components/ui/Modal";
import { apiFetch, errorMessage } from "../../lib/api";
import type { BulkCreateDatabasesResponse, CreateDatabaseInput } from "../../types/api";

const EXAMPLE = `[
  {
    "name": "PROD02",
    "host": "prod02.internal",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "change-me",
    "tags": ["prod"]
  }
]`;

export function BulkAddModal({ isOpen, onClose, onDone }: { isOpen: boolean; onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  const bulkMutation = useMutation({
    mutationFn: (databases: CreateDatabaseInput[]) =>
      apiFetch<BulkCreateDatabasesResponse>("/api/databases/bulk", { method: "POST", body: { databases } }),
  });

  const handleSubmit = () => {
    setParseError(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      setParseError("Not valid JSON.");
      return;
    }
    if (!Array.isArray(parsed) || parsed.length === 0) {
      setParseError("Must be a non-empty JSON array of database objects.");
      return;
    }
    bulkMutation.mutate(parsed as CreateDatabaseInput[]);
  };

  return (
    <Modal
      title="Bulk Add Databases"
      isOpen={isOpen}
      onClose={onClose}
      footer={
        !bulkMutation.data ? (
          <>
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button isLoading={bulkMutation.isPending} onClick={handleSubmit} disabled={!text.trim()}>
              Submit
            </Button>
          </>
        ) : (
          <Button onClick={onDone}>Done</Button>
        )
      }
    >
      {!bulkMutation.data && (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-slate-600 dark:text-slate-400">
            Paste a JSON array of databases to register (up to 100). One row's failure - e.g. a duplicate name - never
            blocks the rest of the batch; each row is reported individually below.
          </p>
          <textarea
            className="h-56 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-xs text-slate-900 shadow-sm outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:border-brand-400 dark:focus:ring-brand-400"
            placeholder={EXAMPLE}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          {parseError && <Alert tone="crit">{parseError}</Alert>}
          {bulkMutation.isError && <Alert tone="crit">{errorMessage(bulkMutation.error)}</Alert>}
        </div>
      )}

      {bulkMutation.data && (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-slate-700 dark:text-slate-300">
            {bulkMutation.data.created_count} created, {bulkMutation.data.failed_count} failed.
          </p>
          <ul className="flex max-h-64 flex-col gap-2 overflow-y-auto">
            {bulkMutation.data.results.map((r) => (
              <li
                key={r.index}
                className="flex items-center justify-between rounded-md border border-slate-200 p-2 text-sm dark:border-slate-800"
              >
                <span>{r.database?.name ?? `Row ${r.index + 1}`}</span>
                {r.status === "created" ? (
                  <Badge tone="good">Created</Badge>
                ) : (
                  <span className="flex items-center gap-2">
                    <Badge tone="crit">Failed</Badge>
                    <span className="text-xs text-status-crit dark:text-red-400">{r.error}</span>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Modal>
  );
}
