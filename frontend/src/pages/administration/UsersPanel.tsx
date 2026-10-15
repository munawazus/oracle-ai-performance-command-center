import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { Alert } from "../../components/ui/Alert";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { Input } from "../../components/ui/Input";
import { Loading } from "../../components/ui/Loading";
import { Modal } from "../../components/ui/Modal";
import { Table, type Column } from "../../components/ui/Table";
import { useToast } from "../../components/ui/Toast";
import { apiFetch, errorMessage } from "../../lib/api";
import { ROLE_NAMES, type AdminUser, type CreateUserInput, type RoleName } from "../../types/api";

export function UsersPanel() {
  const { show } = useToast();
  const queryClient = useQueryClient();
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [editingRolesFor, setEditingRolesFor] = useState<AdminUser | null>(null);

  const query = useQuery({ queryKey: ["admin-users"], queryFn: () => apiFetch<AdminUser[]>("/api/admin/users") });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin-users"] });

  const createMutation = useMutation({
    mutationFn: (input: CreateUserInput) => apiFetch<AdminUser>("/api/admin/users", { method: "POST", body: input }),
    onSuccess: () => {
      invalidate();
      setIsCreateOpen(false);
      show("User created.", "good");
    },
  });

  const statusMutation = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      apiFetch<AdminUser>(`/api/admin/users/${id}/status`, { method: "PATCH", body: { is_active } }),
    onSuccess: () => {
      invalidate();
      show("User status updated.", "good");
    },
  });

  const rolesMutation = useMutation({
    mutationFn: ({ id, roles }: { id: string; roles: RoleName[] }) =>
      apiFetch<AdminUser>(`/api/admin/users/${id}/roles`, { method: "PATCH", body: { roles } }),
    onSuccess: () => {
      invalidate();
      setEditingRolesFor(null);
      show("Roles updated.", "good");
    },
  });

  const columns: Column<AdminUser>[] = [
    {
      header: "User",
      cell: (row) => (
        <div>
          <p className="font-medium text-slate-900 dark:text-slate-100">{row.display_name}</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">{row.email}</p>
        </div>
      ),
    },
    {
      header: "Roles",
      cell: (row) => (
        <div className="flex flex-wrap gap-1">
          {row.roles.map((r) => (
            <Badge key={r} tone="info">
              {r}
            </Badge>
          ))}
        </div>
      ),
    },
    {
      header: "Status",
      cell: (row) => <Badge tone={row.is_active ? "good" : "neutral"}>{row.is_active ? "Active" : "Disabled"}</Badge>,
    },
    {
      header: "Actions",
      cell: (row) => (
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={() => setEditingRolesFor(row)}>
            Edit roles
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => statusMutation.mutate({ id: row.id, is_active: !row.is_active })}
          >
            {row.is_active ? "Disable" : "Enable"}
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-400">Accounts and role assignments.</p>
        <Button onClick={() => setIsCreateOpen(true)}>Create User</Button>
      </div>

      {query.isPending && <Loading label="Loading users…" />}
      {query.isError && <ErrorState message={errorMessage(query.error)} onRetry={() => query.refetch()} />}
      {query.isSuccess && query.data.length === 0 && <EmptyState title="No users yet" />}
      {query.isSuccess && query.data.length > 0 && (
        <Table columns={columns} rows={query.data} rowKey={(row) => row.id} />
      )}

      {isCreateOpen && (
        <CreateUserModal
          isOpen={isCreateOpen}
          onClose={() => setIsCreateOpen(false)}
          isSubmitting={createMutation.isPending}
          error={createMutation.error ? errorMessage(createMutation.error) : null}
          onSubmit={(input) => createMutation.mutate(input)}
        />
      )}

      {editingRolesFor && (
        <EditRolesModal
          user={editingRolesFor}
          isOpen={Boolean(editingRolesFor)}
          onClose={() => setEditingRolesFor(null)}
          isSubmitting={rolesMutation.isPending}
          error={rolesMutation.error ? errorMessage(rolesMutation.error) : null}
          onSubmit={(roles) => rolesMutation.mutate({ id: editingRolesFor.id, roles })}
        />
      )}
    </div>
  );
}

function RoleCheckboxes({ selected, onToggle }: { selected: RoleName[]; onToggle: (role: RoleName) => void }) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="text-sm font-medium text-slate-700 dark:text-slate-300">Roles</legend>
      {ROLE_NAMES.map((role) => (
        <label key={role} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
          <input type="checkbox" checked={selected.includes(role)} onChange={() => onToggle(role)} />
          {role}
        </label>
      ))}
    </fieldset>
  );
}

function CreateUserModal({
  isOpen,
  onClose,
  isSubmitting,
  error,
  onSubmit,
}: {
  isOpen: boolean;
  onClose: () => void;
  isSubmitting: boolean;
  error: string | null;
  onSubmit: (input: CreateUserInput) => void;
}) {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [roles, setRoles] = useState<RoleName[]>([]);

  const toggleRole = (role: RoleName) =>
    setRoles((prev) => (prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role]));

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit({ email, display_name: displayName, password, roles });
  };

  return (
    <Modal
      title="Create User"
      isOpen={isOpen}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="create-user-form" isLoading={isSubmitting}>
            Create user
          </Button>
        </>
      }
    >
      <form id="create-user-form" onSubmit={handleSubmit} className="flex flex-col gap-3" noValidate>
        {error && <Alert tone="crit">{error}</Alert>}
        <Input label="Email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <Input
          label="Display name"
          required
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
        <Input
          label="Password"
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          hint="At least 12 characters."
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <RoleCheckboxes selected={roles} onToggle={toggleRole} />
      </form>
    </Modal>
  );
}

function EditRolesModal({
  user,
  isOpen,
  onClose,
  isSubmitting,
  error,
  onSubmit,
}: {
  user: AdminUser;
  isOpen: boolean;
  onClose: () => void;
  isSubmitting: boolean;
  error: string | null;
  onSubmit: (roles: RoleName[]) => void;
}) {
  const [roles, setRoles] = useState<RoleName[]>(user.roles as RoleName[]);
  const toggleRole = (role: RoleName) =>
    setRoles((prev) => (prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role]));

  return (
    <Modal
      title={`Edit roles - ${user.display_name}`}
      isOpen={isOpen}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button isLoading={isSubmitting} onClick={() => onSubmit(roles)}>
            Save
          </Button>
        </>
      }
    >
      {error && (
        <div className="mb-3">
          <Alert tone="crit">{error}</Alert>
        </div>
      )}
      <RoleCheckboxes selected={roles} onToggle={toggleRole} />
    </Modal>
  );
}
