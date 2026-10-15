import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Drawer } from "../components/ui/Drawer";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Input } from "../components/ui/Input";
import { Loading } from "../components/ui/Loading";
import { Modal } from "../components/ui/Modal";
import { Select } from "../components/ui/Select";
import { Table, type Column } from "../components/ui/Table";
import { Tabs } from "../components/ui/Tabs";
import { useToast } from "../components/ui/Toast";
import { apiFetch, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth-context";
import { CapabilityPanel } from "./databases/CapabilityPanel";
import { BulkAddModal } from "./databases/BulkAddModal";
import { GroupsPanel } from "./databases/GroupsPanel";
import type {
  CreateDatabaseInput,
  DatabaseGroup,
  MonitoredDatabase,
  UpdateDatabaseInput,
} from "../types/api";

const EDITION_OPTIONS = [
  { value: "UNKNOWN", label: "Unknown" },
  { value: "SE2", label: "Standard Edition 2" },
  { value: "EE", label: "Enterprise Edition" },
];

export function DatabasesPage() {
  const { hasPermission } = useAuth();
  const { show } = useToast();
  const queryClient = useQueryClient();

  const [pageTab, setPageTab] = useState<"databases" | "groups">("databases");
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [isBulkAddOpen, setIsBulkAddOpen] = useState(false);
  const [editing, setEditing] = useState<MonitoredDatabase | null>(null);
  const [viewing, setViewing] = useState<MonitoredDatabase | null>(null);
  const [pendingDelete, setPendingDelete] = useState<MonitoredDatabase | null>(null);
  const [drawerTab, setDrawerTab] = useState<"details" | "capabilities">("details");
  const [groupFilter, setGroupFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");

  const openDrawer = (database: MonitoredDatabase) => {
    setDrawerTab("details");
    setViewing(database);
  };

  // A one-shot "open this database's drawer" trigger (from search, e.g. /databases?id=...) -
  // fetched directly by id so it works regardless of the group/tag filter currently active,
  // then the param is stripped so it doesn't linger.
  const [searchParams, setSearchParams] = useSearchParams();
  const idParam = searchParams.get("id");
  const databaseByIdQuery = useQuery({
    queryKey: ["database", idParam],
    queryFn: () => apiFetch<MonitoredDatabase>(`/api/databases/${idParam}`),
    enabled: Boolean(idParam),
  });
  useEffect(() => {
    if (!databaseByIdQuery.data) return;
    openDrawer(databaseByIdQuery.data);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("id");
        return next;
      },
      { replace: true },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [databaseByIdQuery.data]);

  const groupsQuery = useQuery({
    queryKey: ["database-groups"],
    queryFn: () => apiFetch<DatabaseGroup[]>("/api/database-groups"),
  });

  const listParams = new URLSearchParams();
  if (groupFilter) listParams.set("group_id", groupFilter);
  if (tagFilter) listParams.set("tag", tagFilter);
  const query = useQuery({
    queryKey: ["databases", groupFilter, tagFilter],
    queryFn: () => apiFetch<MonitoredDatabase[]>(`/api/databases${listParams.toString() ? `?${listParams}` : ""}`),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["databases"] });

  const createMutation = useMutation({
    mutationFn: (input: CreateDatabaseInput) =>
      apiFetch<MonitoredDatabase>("/api/databases", { method: "POST", body: input }),
    onSuccess: () => {
      invalidate();
      setIsAddOpen(false);
      show("Database registered.", "good");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateDatabaseInput }) =>
      apiFetch<MonitoredDatabase>(`/api/databases/${id}`, { method: "PATCH", body: input }),
    onSuccess: () => {
      invalidate();
      setEditing(null);
      show("Database updated.", "good");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/api/databases/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      invalidate();
      setPendingDelete(null);
      setViewing(null);
      show("Database removed.", "good");
    },
  });

  const canAdd = hasPermission("database:add");
  const canEdit = hasPermission("database:edit");
  const canDelete = hasPermission("database:delete");

  const columns: Column<MonitoredDatabase>[] = [
    { header: "Name", cell: (row) => <span className="font-medium text-slate-900 dark:text-slate-100">{row.name}</span> },
    { header: "Host", cell: (row) => `${row.host}:${row.port}` },
    { header: "Service", cell: (row) => row.service_name },
    { header: "Edition", cell: (row) => <Badge tone="info">{row.edition_hint}</Badge> },
    {
      header: "Tags",
      cell: (row) =>
        row.tags.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {row.tags.map((t) => (
              <Badge key={t} tone="neutral">
                {t}
              </Badge>
            ))}
          </div>
        ) : (
          "—"
        ),
    },
    {
      header: "Status",
      cell: (row) => <Badge tone={row.is_active ? "good" : "neutral"}>{row.is_active ? "Active" : "Inactive"}</Badge>,
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <Tabs
        tabs={[
          { id: "databases", label: "Databases" },
          { id: "groups", label: "Groups" },
        ]}
        activeId={pageTab}
        onChange={(id) => setPageTab(id as "databases" | "groups")}
      />

      {pageTab === "groups" && <GroupsPanel canManage={canAdd && canEdit && canDelete} />}

      {pageTab === "databases" && (
        <>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-56">
                <Select
                  label="Filter by group"
                  options={[
                    { value: "", label: "All groups" },
                    ...(groupsQuery.data ?? []).map((g) => ({ value: g.id, label: g.name })),
                  ]}
                  value={groupFilter}
                  onChange={(e) => setGroupFilter(e.target.value)}
                />
              </div>
              <div className="w-48">
                <Input label="Filter by tag" placeholder="e.g. prod" value={tagFilter} onChange={(e) => setTagFilter(e.target.value)} />
              </div>
            </div>
            {canAdd && (
              <div className="flex gap-2">
                <Button variant="secondary" onClick={() => setIsBulkAddOpen(true)}>
                  Bulk Add
                </Button>
                <Button onClick={() => setIsAddOpen(true)}>Add Database</Button>
              </div>
            )}
          </div>

          {query.isPending && <Loading label="Loading databases…" />}
          {query.isError && <ErrorState message={errorMessage(query.error)} onRetry={() => query.refetch()} />}
          {query.isSuccess && query.data.length === 0 && (
            <EmptyState
              title={groupFilter || tagFilter ? "No databases match this filter" : "No databases registered"}
              description={
                groupFilter || tagFilter
                  ? "Try a different group or tag."
                  : "Register your first Oracle database to begin onboarding it."
              }
              action={!groupFilter && !tagFilter && canAdd ? <Button onClick={() => setIsAddOpen(true)}>Add Database</Button> : undefined}
            />
          )}
          {query.isSuccess && query.data.length > 0 && (
            <Table columns={columns} rows={query.data} rowKey={(row) => row.id} onRowClick={openDrawer} />
          )}
        </>
      )}

      {isAddOpen && (
        <DatabaseFormModal
          mode="create"
          isOpen={isAddOpen}
          onClose={() => setIsAddOpen(false)}
          isSubmitting={createMutation.isPending}
          error={createMutation.error ? errorMessage(createMutation.error) : null}
          onSubmit={(input) => createMutation.mutate(input as CreateDatabaseInput)}
        />
      )}

      {isBulkAddOpen && (
        <BulkAddModal
          isOpen={isBulkAddOpen}
          onClose={() => setIsBulkAddOpen(false)}
          onDone={() => {
            invalidate();
            setIsBulkAddOpen(false);
          }}
        />
      )}

      {editing && (
        <DatabaseFormModal
          mode="edit"
          isOpen={Boolean(editing)}
          database={editing}
          onClose={() => setEditing(null)}
          isSubmitting={updateMutation.isPending}
          error={updateMutation.error ? errorMessage(updateMutation.error) : null}
          onSubmit={(input) => updateMutation.mutate({ id: editing.id, input })}
        />
      )}

      {viewing && (
        <Drawer title={viewing.name} isOpen={Boolean(viewing)} onClose={() => setViewing(null)}>
          <div className="mb-4">
            <Tabs
              tabs={[
                { id: "details", label: "Details" },
                { id: "capabilities", label: "Capabilities" },
              ]}
              activeId={drawerTab}
              onChange={(id) => setDrawerTab(id as "details" | "capabilities")}
            />
          </div>

          {drawerTab === "details" && (
            <>
              <dl className="flex flex-col gap-3 text-sm">
                <Row label="Host" value={`${viewing.host}:${viewing.port}`} />
                <Row label="Service name" value={viewing.service_name} />
                <Row label="Edition" value={viewing.edition_hint} />
                <Row label="Status" value={viewing.is_active ? "Active" : "Inactive"} />
                <Row label="Registered" value={new Date(viewing.created_at).toLocaleString()} />
              </dl>
              <div className="mt-6 flex gap-2">
                {canEdit && (
                  <Button variant="secondary" size="sm" onClick={() => setEditing(viewing)}>
                    Edit
                  </Button>
                )}
                {canDelete && (
                  <Button variant="danger" size="sm" onClick={() => setPendingDelete(viewing)}>
                    Delete
                  </Button>
                )}
              </div>

              <div className="mt-6 border-t border-slate-200 pt-4 dark:border-slate-800">
                <GroupMembershipEditor databaseId={viewing.id} canEdit={canEdit} groups={groupsQuery.data ?? []} />
              </div>
            </>
          )}

          {drawerTab === "capabilities" && <CapabilityPanel databaseId={viewing.id} canEdit={canEdit} />}
        </Drawer>
      )}

      {pendingDelete && (
        <Modal
          title={`Remove ${pendingDelete.name}?`}
          isOpen={Boolean(pendingDelete)}
          onClose={() => setPendingDelete(null)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setPendingDelete(null)}>
                Cancel
              </Button>
              <Button
                variant="danger"
                isLoading={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate(pendingDelete.id)}
              >
                Remove
              </Button>
            </>
          }
        >
          <p className="text-sm text-slate-600 dark:text-slate-400">
            This permanently removes the registration and stored credential for {pendingDelete.name}. This cannot be
            undone.
          </p>
          {deleteMutation.isError && (
            <div className="mt-3">
              <Alert tone="crit">{errorMessage(deleteMutation.error)}</Alert>
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}

function GroupMembershipEditor({
  databaseId,
  canEdit,
  groups,
}: {
  databaseId: string;
  canEdit: boolean;
  groups: DatabaseGroup[];
}) {
  const { show } = useToast();
  const queryClient = useQueryClient();

  const membershipsQuery = useQuery({
    queryKey: ["database-groups-of", databaseId],
    queryFn: () => apiFetch<string[]>(`/api/databases/${databaseId}/groups`),
  });

  const setMutation = useMutation({
    mutationFn: (groupIds: string[]) =>
      apiFetch<string[]>(`/api/databases/${databaseId}/groups`, { method: "PUT", body: { group_ids: groupIds } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["database-groups-of", databaseId] });
      queryClient.invalidateQueries({ queryKey: ["database-groups"] });
      show("Group membership updated.", "good");
    },
  });

  const toggle = (groupId: string) => {
    const current = membershipsQuery.data ?? [];
    const next = current.includes(groupId) ? current.filter((id) => id !== groupId) : [...current, groupId];
    setMutation.mutate(next);
  };

  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Groups</h3>
      {membershipsQuery.isPending && <Loading label="Loading groups…" />}
      {membershipsQuery.isError && (
        <ErrorState message={errorMessage(membershipsQuery.error)} onRetry={() => membershipsQuery.refetch()} />
      )}
      {membershipsQuery.isSuccess && groups.length === 0 && (
        <p className="text-sm text-slate-500 dark:text-slate-400">No groups exist yet - create one from the Groups tab.</p>
      )}
      {membershipsQuery.isSuccess && groups.length > 0 && (
        <ul className="flex flex-col gap-1">
          {groups.map((g) => (
            <li key={g.id}>
              <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                <input
                  type="checkbox"
                  disabled={!canEdit || setMutation.isPending}
                  checked={membershipsQuery.data!.includes(g.id)}
                  onChange={() => toggle(g.id)}
                />
                {g.name}
              </label>
            </li>
          ))}
        </ul>
      )}
    </div>
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

interface DatabaseFormModalProps {
  mode: "create" | "edit";
  isOpen: boolean;
  database?: MonitoredDatabase;
  isSubmitting: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (input: CreateDatabaseInput | UpdateDatabaseInput) => void;
}

function DatabaseFormModal({ mode, isOpen, database, isSubmitting, error, onClose, onSubmit }: DatabaseFormModalProps) {
  const [name, setName] = useState(database?.name ?? "");
  const [host, setHost] = useState(database?.host ?? "");
  const [port, setPort] = useState(database ? String(database.port) : "1521");
  const [serviceName, setServiceName] = useState(database?.service_name ?? "");
  const [editionHint, setEditionHint] = useState(database?.edition_hint ?? "UNKNOWN");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [tagsText, setTagsText] = useState(database?.tags.join(", ") ?? "");

  const parseTags = () =>
    tagsText
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const base = {
      name,
      host,
      port: Number(port),
      service_name: serviceName,
      edition_hint: editionHint as CreateDatabaseInput["edition_hint"],
      tags: parseTags(),
    };
    if (mode === "create") {
      onSubmit({ ...base, username, password } satisfies CreateDatabaseInput);
    } else {
      const update: UpdateDatabaseInput = { ...base };
      if (username) update.username = username;
      if (password) update.password = password;
      onSubmit(update);
    }
  };

  return (
    <Modal
      title={mode === "create" ? "Add Database" : `Edit ${database?.name}`}
      isOpen={isOpen}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="database-form" isLoading={isSubmitting}>
            {mode === "create" ? "Register database" : "Save changes"}
          </Button>
        </>
      }
    >
      <form id="database-form" onSubmit={handleSubmit} className="flex flex-col gap-3" noValidate>
        {error && <Alert tone="crit">{error}</Alert>}
        <Input label="Name" required value={name} onChange={(e) => setName(e.target.value)} />
        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-2">
            <Input label="Host" required value={host} onChange={(e) => setHost(e.target.value)} />
          </div>
          <Input
            label="Port"
            type="number"
            min={1}
            max={65535}
            required
            value={port}
            onChange={(e) => setPort(e.target.value)}
          />
        </div>
        <Input label="Service name" required value={serviceName} onChange={(e) => setServiceName(e.target.value)} />
        <Select
          label="Edition"
          options={EDITION_OPTIONS}
          value={editionHint}
          onChange={(e) => setEditionHint(e.target.value as CreateDatabaseInput["edition_hint"])}
        />
        <Input
          label="Tags (comma-separated)"
          placeholder="prod, east-region"
          value={tagsText}
          onChange={(e) => setTagsText(e.target.value)}
          hint="Freeform fleet labels - used for filtering databases and incidents."
        />
        <Input
          label={mode === "create" ? "Monitoring account username" : "Monitoring account username (leave blank to keep)"}
          required={mode === "create"}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <Input
          label={mode === "create" ? "Password" : "Password (leave blank to keep current)"}
          type="password"
          autoComplete="new-password"
          required={mode === "create"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          hint="Stored encrypted. Never shown again after saving."
        />
      </form>
    </Modal>
  );
}
