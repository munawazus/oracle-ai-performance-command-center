import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { Alert } from "../../components/ui/Alert";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { Input } from "../../components/ui/Input";
import { Loading } from "../../components/ui/Loading";
import { Modal } from "../../components/ui/Modal";
import { Table, type Column } from "../../components/ui/Table";
import { useToast } from "../../components/ui/Toast";
import { apiFetch, errorMessage } from "../../lib/api";
import type { CreateDatabaseGroupInput, DatabaseGroup, UpdateDatabaseGroupInput } from "../../types/api";

export function GroupsPanel({ canManage }: { canManage: boolean }) {
  const { show } = useToast();
  const queryClient = useQueryClient();
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [editing, setEditing] = useState<DatabaseGroup | null>(null);
  const [pendingDelete, setPendingDelete] = useState<DatabaseGroup | null>(null);

  const query = useQuery({
    queryKey: ["database-groups"],
    queryFn: () => apiFetch<DatabaseGroup[]>("/api/database-groups"),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["database-groups"] });

  const createMutation = useMutation({
    mutationFn: (input: CreateDatabaseGroupInput) =>
      apiFetch<DatabaseGroup>("/api/database-groups", { method: "POST", body: input }),
    onSuccess: () => {
      invalidate();
      setIsAddOpen(false);
      show("Group created.", "good");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateDatabaseGroupInput }) =>
      apiFetch<DatabaseGroup>(`/api/database-groups/${id}`, { method: "PATCH", body: input }),
    onSuccess: () => {
      invalidate();
      setEditing(null);
      show("Group updated.", "good");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/api/database-groups/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      invalidate();
      setPendingDelete(null);
      show("Group deleted.", "good");
    },
  });

  const columns: Column<DatabaseGroup>[] = [
    { header: "Name", cell: (g) => <span className="font-medium text-slate-900 dark:text-slate-100">{g.name}</span> },
    { header: "Description", cell: (g) => g.description || "—" },
    { header: "Databases", cell: (g) => String(g.database_count) },
    ...(canManage
      ? [
          {
            header: "",
            cell: (g: DatabaseGroup) => (
              <div className="flex gap-2">
                <Button variant="secondary" size="sm" onClick={() => setEditing(g)}>
                  Edit
                </Button>
                <Button variant="danger" size="sm" onClick={() => setPendingDelete(g)}>
                  Delete
                </Button>
              </div>
            ),
          },
        ]
      : []),
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Named collections of databases (Master Plan Phase 22) - used to filter both the Databases list and the
          global Incidents view.
        </p>
        {canManage && <Button onClick={() => setIsAddOpen(true)}>New Group</Button>}
      </div>

      {query.isPending && <Loading label="Loading groups…" />}
      {query.isError && <ErrorState message={errorMessage(query.error)} onRetry={() => query.refetch()} />}
      {query.isSuccess && query.data.length === 0 && (
        <EmptyState
          title="No groups yet"
          description="Create a group to organize databases for fleet-wide filtering."
          action={canManage ? <Button onClick={() => setIsAddOpen(true)}>New Group</Button> : undefined}
        />
      )}
      {query.isSuccess && query.data.length > 0 && <Table columns={columns} rows={query.data} rowKey={(g) => g.id} />}

      {isAddOpen && (
        <GroupFormModal
          mode="create"
          isOpen={isAddOpen}
          onClose={() => setIsAddOpen(false)}
          isSubmitting={createMutation.isPending}
          error={createMutation.error ? errorMessage(createMutation.error) : null}
          onSubmit={(input) => createMutation.mutate(input as CreateDatabaseGroupInput)}
        />
      )}

      {editing && (
        <GroupFormModal
          mode="edit"
          isOpen={Boolean(editing)}
          group={editing}
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
          <p className="text-sm text-slate-600 dark:text-slate-400">
            This removes the group and its membership links. Databases themselves are not affected.
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

function GroupFormModal({
  mode,
  isOpen,
  group,
  isSubmitting,
  error,
  onClose,
  onSubmit,
}: {
  mode: "create" | "edit";
  isOpen: boolean;
  group?: DatabaseGroup;
  isSubmitting: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (input: CreateDatabaseGroupInput | UpdateDatabaseGroupInput) => void;
}) {
  const [name, setName] = useState(group?.name ?? "");
  const [description, setDescription] = useState(group?.description ?? "");

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit({ name, description });
  };

  return (
    <Modal
      title={mode === "create" ? "New Group" : `Edit ${group?.name}`}
      isOpen={isOpen}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="group-form" isLoading={isSubmitting}>
            {mode === "create" ? "Create group" : "Save changes"}
          </Button>
        </>
      }
    >
      <form id="group-form" onSubmit={handleSubmit} className="flex flex-col gap-3" noValidate>
        {error && <Alert tone="crit">{error}</Alert>}
        <Input label="Name" required value={name} onChange={(e) => setName(e.target.value)} />
        <Input label="Description" value={description} onChange={(e) => setDescription(e.target.value)} />
      </form>
    </Modal>
  );
}
