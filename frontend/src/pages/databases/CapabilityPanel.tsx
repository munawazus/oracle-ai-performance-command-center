import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Alert } from "../../components/ui/Alert";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ErrorState } from "../../components/ui/ErrorState";
import { Loading } from "../../components/ui/Loading";
import { apiFetch, errorMessage } from "../../lib/api";
import type { CapabilityProfile, TestConnectionResult } from "../../types/api";

export function CapabilityPanel({ databaseId, canEdit }: { databaseId: string; canEdit: boolean }) {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ["capabilities", databaseId],
    queryFn: () => apiFetch<CapabilityProfile>(`/api/databases/${databaseId}/capabilities`),
  });

  const testConnectionMutation = useMutation({
    mutationFn: () => apiFetch<TestConnectionResult>(`/api/databases/${databaseId}/test-connection`, { method: "POST" }),
  });

  const detectMutation = useMutation({
    mutationFn: () => apiFetch<CapabilityProfile>(`/api/databases/${databaseId}/detect-capabilities`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["capabilities", databaseId] }),
  });

  if (query.isPending) return <Loading label="Loading capability profile…" />;
  if (query.isError) return <ErrorState message={errorMessage(query.error)} onRetry={() => query.refetch()} />;

  const profile = query.data;
  const hasBeenProbed = profile.last_probed_at !== null;

  return (
    <div className="flex flex-col gap-4">
      {canEdit && (
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            size="sm"
            isLoading={testConnectionMutation.isPending}
            onClick={() => testConnectionMutation.mutate()}
          >
            Test Connection
          </Button>
          <Button variant="secondary" size="sm" isLoading={detectMutation.isPending} onClick={() => detectMutation.mutate()}>
            Detect Capabilities
          </Button>
        </div>
      )}

      {testConnectionMutation.data && (
        <Alert tone={testConnectionMutation.data.ok ? "good" : "crit"}>{testConnectionMutation.data.message}</Alert>
      )}
      {testConnectionMutation.isError && <Alert tone="crit">{errorMessage(testConnectionMutation.error)}</Alert>}
      {detectMutation.isError && <Alert tone="crit">{errorMessage(detectMutation.error)}</Alert>}

      {!hasBeenProbed && (
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Not yet probed{canEdit ? " - run Detect Capabilities to check version, edition, RAC, Statspack, and view privileges." : "."}
        </p>
      )}

      {hasBeenProbed && (
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <ProfileRow label="Edition">
            <Badge tone="info">{profile.detected_edition ?? "Unknown"}</Badge>
          </ProfileRow>
          <ProfileRow label="Version">{profile.detected_version ?? "—"}</ProfileRow>
          <ProfileRow label="RAC">{profile.is_rac === null ? "Unknown" : profile.is_rac ? "Yes" : "No"}</ProfileRow>
          <ProfileRow label="Statspack">
            {profile.statspack_available === null ? "Unknown" : profile.statspack_available ? "Detected" : "Not detected"}
          </ProfileRow>
          <ProfileRow label="CPU cores">{profile.cpu_count ?? "Unknown"}</ProfileRow>
          <ProfileRow label="Diagnostics/Tuning Pack">
            {profile.diagnostics_tuning_pack_licensed_confirmed ? "Confirmed licensed" : "Not confirmed - treated as unavailable"}
          </ProfileRow>
          <ProfileRow label="Last probed">{new Date(profile.last_probed_at!).toLocaleString()}</ProfileRow>
        </dl>
      )}

      {hasBeenProbed && profile.privilege_report && <PrivilegeReport report={profile.privilege_report} />}
    </div>
  );
}

function ProfileRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-400 dark:text-slate-500">{label}</dt>
      <dd className="text-slate-800 dark:text-slate-200">{children}</dd>
    </div>
  );
}

function PrivilegeReport({ report }: { report: Record<string, boolean> }) {
  const views = Object.entries(report).sort(([a], [b]) => a.localeCompare(b));
  const missing = views.filter(([, ok]) => !ok);

  return (
    <div>
      <p className="mb-2 text-xs uppercase tracking-wide text-slate-400 dark:text-slate-500">
        Base-view access ({views.length - missing.length}/{views.length})
      </p>
      {missing.length > 0 && (
        <div className="mb-2">
          <Alert tone="warn">
            The monitoring account cannot read: {missing.map(([view]) => view).join(", ")}. Grant SELECT on these
            views before onboarding collectors against this database.
          </Alert>
        </div>
      )}
      <div className="flex flex-wrap gap-1">
        {views.map(([view, ok]) => (
          <Badge key={view} tone={ok ? "good" : "crit"}>
            {view}
          </Badge>
        ))}
      </div>
    </div>
  );
}
