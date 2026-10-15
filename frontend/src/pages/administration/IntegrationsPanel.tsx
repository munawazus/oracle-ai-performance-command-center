import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { Alert } from "../../components/ui/Alert";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { Input } from "../../components/ui/Input";
import { Loading } from "../../components/ui/Loading";
import { Modal } from "../../components/ui/Modal";
import { Select } from "../../components/ui/Select";
import { Table, type Column } from "../../components/ui/Table";
import { useToast } from "../../components/ui/Toast";
import { apiFetch, errorMessage } from "../../lib/api";
import type {
  CreateIntegrationInput,
  Integration,
  IntegrationDelivery,
  IntegrationType,
  TestIntegrationResult,
  UpdateIntegrationInput,
} from "../../types/api";

const TYPE_OPTIONS: { value: IntegrationType; label: string }[] = [
  { value: "WEBHOOK", label: "Generic webhook" },
  { value: "SLACK", label: "Slack" },
  { value: "TEAMS", label: "Microsoft Teams" },
];

const ALL_SEVERITIES = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"];

const DELIVERY_STATUS_TONE: Record<string, "good" | "warn" | "crit" | "neutral"> = {
  SENT: "good",
  PENDING: "neutral",
  RETRYING: "warn",
  FAILED: "crit",
};

export function IntegrationsPanel() {
  const { show } = useToast();
  const queryClient = useQueryClient();
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [editing, setEditing] = useState<Integration | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Integration | null>(null);
  const [viewingDeliveries, setViewingDeliveries] = useState<Integration | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["integrations"],
    queryFn: () => apiFetch<Integration[]>("/api/integrations"),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["integrations"] });

  const createMutation = useMutation({
    mutationFn: (input: CreateIntegrationInput) => apiFetch<Integration>("/api/integrations", { method: "POST", body: input }),
    onSuccess: () => {
      invalidate();
      setIsAddOpen(false);
      show("Integration created.", "good");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateIntegrationInput }) =>
      apiFetch<Integration>(`/api/integrations/${id}`, { method: "PATCH", body: input }),
    onSuccess: () => {
      invalidate();
      setEditing(null);
      show("Integration updated.", "good");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/api/integrations/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      invalidate();
      setPendingDelete(null);
      show("Integration deleted.", "good");
    },
  });

  const handleTest = async (id: string) => {
    setTestingId(id);
    try {
      const result = await apiFetch<TestIntegrationResult>(`/api/integrations/${id}/test`, { method: "POST" });
      show(result.ok ? "Test delivery succeeded." : `Test delivery failed: ${result.message}`, result.ok ? "good" : "crit");
    } catch (err) {
      show(errorMessage(err), "crit");
    } finally {
      setTestingId(null);
    }
  };

  const columns: Column<Integration>[] = [
    { header: "Name", cell: (i) => <span className="font-medium text-slate-900 dark:text-slate-100">{i.name}</span> },
    { header: "Type", cell: (i) => TYPE_OPTIONS.find((o) => o.value === i.type)?.label ?? i.type },
    { header: "Target", cell: (i) => <span className="font-mono text-xs">{i.target_url}</span> },
    {
      header: "Fires on",
      cell: (i) => (
        <div className="flex flex-wrap gap-1">
          {i.trigger_severities.map((s) => (
            <Badge key={s} tone="neutral">
              {s}
            </Badge>
          ))}
        </div>
      ),
    },
    { header: "Status", cell: (i) => <Badge tone={i.enabled ? "good" : "neutral"}>{i.enabled ? "Enabled" : "Disabled"}</Badge> },
    {
      header: "",
      cell: (i) => (
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" isLoading={testingId === i.id} onClick={() => handleTest(i.id)}>
            Test
          </Button>
          <Button variant="secondary" size="sm" onClick={() => setViewingDeliveries(i)}>
            History
          </Button>
          <Button variant="secondary" size="sm" onClick={() => setEditing(i)}>
            Edit
          </Button>
          <Button variant="danger" size="sm" onClick={() => setPendingDelete(i)}>
            Delete
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="max-w-xl text-sm text-slate-500 dark:text-slate-400">
          HIGH and CRITICAL incidents fire every enabled integration whose trigger severities match - once per
          incident per severity level reached, same dedup rule as email.
        </p>
        <Button onClick={() => setIsAddOpen(true)}>New Integration</Button>
      </div>

      {query.isPending && <Loading label="Loading integrations…" />}
      {query.isError && <ErrorState message={errorMessage(query.error)} onRetry={() => query.refetch()} />}
      {query.isSuccess && query.data.length === 0 && (
        <EmptyState
          title="No integrations configured"
          description="Add a webhook, Slack, or Teams target to deliver HIGH/CRITICAL incidents outside email."
          action={<Button onClick={() => setIsAddOpen(true)}>New Integration</Button>}
        />
      )}
      {query.isSuccess && query.data.length > 0 && <Table columns={columns} rows={query.data} rowKey={(i) => i.id} />}

      {isAddOpen && (
        <IntegrationFormModal
          mode="create"
          isOpen={isAddOpen}
          onClose={() => setIsAddOpen(false)}
          isSubmitting={createMutation.isPending}
          error={createMutation.error ? errorMessage(createMutation.error) : null}
          onSubmit={(input) => createMutation.mutate(input as CreateIntegrationInput)}
        />
      )}

      {editing && (
        <IntegrationFormModal
          mode="edit"
          isOpen={Boolean(editing)}
          integration={editing}
          onClose={() => setEditing(null)}
          isSubmitting={updateMutation.isPending}
          error={updateMutation.error ? errorMessage(updateMutation.error) : null}
          onSubmit={(input) => updateMutation.mutate({ id: editing.id, input })}
        />
      )}

      {pendingDelete && (
        <Modal
          title={`Delete ${pendingDelete.name}?`}
          isOpen={Boolean(pendingDelete)}
          onClose={() => setPendingDelete(null)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setPendingDelete(null)}>
                Cancel
              </Button>
              <Button variant="danger" isLoading={deleteMutation.isPending} onClick={() => deleteMutation.mutate(pendingDelete.id)}>
                Delete
              </Button>
            </>
          }
        >
          <p className="text-sm text-slate-600 dark:text-slate-400">This stops future deliveries to this target. It cannot be undone.</p>
          {deleteMutation.isError && (
            <div className="mt-3">
              <Alert tone="crit">{errorMessage(deleteMutation.error)}</Alert>
            </div>
          )}
        </Modal>
      )}

      {viewingDeliveries && (
        <DeliveryHistoryDrawer integration={viewingDeliveries} onClose={() => setViewingDeliveries(null)} />
      )}
    </div>
  );
}

function DeliveryHistoryDrawer({ integration, onClose }: { integration: Integration; onClose: () => void }) {
  const deliveriesQuery = useQuery({
    queryKey: ["integration-deliveries", integration.id],
    queryFn: () => apiFetch<IntegrationDelivery[]>(`/api/integrations/deliveries?integration_id=${integration.id}`),
  });

  return (
    <Drawer title={`${integration.name} - delivery history`} isOpen onClose={onClose}>
      {deliveriesQuery.isPending && <Loading label="Loading delivery history…" />}
      {deliveriesQuery.isError && (
        <ErrorState message={errorMessage(deliveriesQuery.error)} onRetry={() => deliveriesQuery.refetch()} />
      )}
      {deliveriesQuery.isSuccess && deliveriesQuery.data.length === 0 && (
        <EmptyState title="No deliveries yet" description="Nothing has triggered this integration yet." />
      )}
      {deliveriesQuery.isSuccess && deliveriesQuery.data.length > 0 && (
        <ul className="flex flex-col gap-2">
          {deliveriesQuery.data.map((d) => (
            <li key={d.id} className="rounded-md border border-slate-200 p-2 text-xs dark:border-slate-800">
              <div className="flex items-center justify-between">
                <Badge tone={DELIVERY_STATUS_TONE[d.status] ?? "neutral"}>{d.status}</Badge>
                <span className="text-slate-400 dark:text-slate-500">{d.event_type}</span>
              </div>
              <p className="mt-1 text-slate-600 dark:text-slate-400">Incident {d.incident_id}</p>
              {d.status === "FAILED" && d.last_error_category && (
                <p className="mt-1 text-status-crit dark:text-red-400">
                  Failed ({d.last_error_category}) after {d.attempt_count} attempt(s).
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
    </Drawer>
  );
}

function IntegrationFormModal({
  mode,
  isOpen,
  integration,
  isSubmitting,
  error,
  onClose,
  onSubmit,
}: {
  mode: "create" | "edit";
  isOpen: boolean;
  integration?: Integration;
  isSubmitting: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (input: CreateIntegrationInput | UpdateIntegrationInput) => void;
}) {
  const [type, setType] = useState<IntegrationType>(integration?.type ?? "WEBHOOK");
  const [name, setName] = useState(integration?.name ?? "");
  const [targetUrl, setTargetUrl] = useState(integration?.target_url ?? "");
  const [secret, setSecret] = useState("");
  const [enabled, setEnabled] = useState(integration?.enabled ?? true);
  const [severities, setSeverities] = useState<string[]>(integration?.trigger_severities ?? ["HIGH", "CRITICAL"]);

  const toggleSeverity = (s: string) => {
    setSeverities((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (mode === "create") {
      const input: CreateIntegrationInput = { type, name, target_url: targetUrl, enabled, trigger_severities: severities };
      if (secret) input.secret = secret;
      onSubmit(input);
    } else {
      const input: UpdateIntegrationInput = { name, target_url: targetUrl, enabled, trigger_severities: severities };
      if (secret) input.secret = secret;
      onSubmit(input);
    }
  };

  return (
    <Modal
      title={mode === "create" ? "New Integration" : `Edit ${integration?.name}`}
      isOpen={isOpen}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="integration-form" isLoading={isSubmitting}>
            {mode === "create" ? "Create integration" : "Save changes"}
          </Button>
        </>
      }
    >
      <form id="integration-form" onSubmit={handleSubmit} className="flex flex-col gap-3" noValidate>
        {error && <Alert tone="crit">{error}</Alert>}
        {mode === "create" && (
          <Select label="Type" options={TYPE_OPTIONS} value={type} onChange={(e) => setType(e.target.value as IntegrationType)} />
        )}
        <Input label="Name" required value={name} onChange={(e) => setName(e.target.value)} />
        <Input
          label="Target URL"
          required
          value={targetUrl}
          onChange={(e) => setTargetUrl(e.target.value)}
          hint="Must be http:// or https://"
        />
        <Input
          label={integration?.has_secret ? "Signing secret (leave blank to keep current)" : "Signing secret (optional)"}
          type="password"
          autoComplete="new-password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          hint="Sent as an X-Signature-256 HMAC header - only meaningful for a Generic webhook receiver you control."
        />
        <div>
          <p className="mb-1 text-sm font-medium text-slate-700 dark:text-slate-300">Fires on</p>
          <div className="flex flex-wrap gap-3">
            {ALL_SEVERITIES.map((s) => (
              <label key={s} className="flex items-center gap-1.5 text-sm text-slate-700 dark:text-slate-300">
                <input type="checkbox" checked={severities.includes(s)} onChange={() => toggleSeverity(s)} />
                {s}
              </label>
            ))}
          </div>
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled
        </label>
      </form>
    </Modal>
  );
}
