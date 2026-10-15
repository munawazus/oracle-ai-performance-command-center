import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import { Alert } from "../../components/ui/Alert";
import { Button } from "../../components/ui/Button";
import { ErrorState } from "../../components/ui/ErrorState";
import { Input } from "../../components/ui/Input";
import { Loading } from "../../components/ui/Loading";
import { useToast } from "../../components/ui/Toast";
import { apiFetch, errorMessage } from "../../lib/api";
import type { NotificationSettings, UpdateNotificationSettingsInput } from "../../types/api";

function parseEmailList(value: string): string[] {
  return value
    .split(",")
    .map((e) => e.trim())
    .filter(Boolean);
}

export function NotificationsPanel() {
  const { show } = useToast();
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: ["notification-settings"],
    queryFn: () => apiFetch<NotificationSettings>("/api/notifications/settings"),
  });

  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState("587");
  const [smtpUseTls, setSmtpUseTls] = useState(true);
  const [smtpUsername, setSmtpUsername] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [senderEmail, setSenderEmail] = useState("");
  const [senderName, setSenderName] = useState("");
  const [defaultRecipients, setDefaultRecipients] = useState("");
  const [escalationRecipients, setEscalationRecipients] = useState("");

  useEffect(() => {
    if (!settingsQuery.data) return;
    const s = settingsQuery.data;
    setSmtpHost(s.smtp_host ?? "");
    setSmtpPort(String(s.smtp_port));
    setSmtpUseTls(s.smtp_use_tls);
    setSmtpUsername(s.smtp_username ?? "");
    setSenderEmail(s.sender_email ?? "");
    setSenderName(s.sender_name);
    setDefaultRecipients(s.default_recipients.join(", "));
    setEscalationRecipients(s.escalation_recipients.join(", "));
  }, [settingsQuery.data]);

  const updateMutation = useMutation({
    mutationFn: (input: UpdateNotificationSettingsInput) =>
      apiFetch<NotificationSettings>("/api/notifications/settings", { method: "PUT", body: input }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notification-settings"] });
      setSmtpPassword("");
      show("Notification settings saved.", "good");
    },
  });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const input: UpdateNotificationSettingsInput = {
      smtp_host: smtpHost,
      smtp_port: Number(smtpPort),
      smtp_use_tls: smtpUseTls,
      smtp_username: smtpUsername,
      sender_email: senderEmail,
      sender_name: senderName,
      default_recipients: parseEmailList(defaultRecipients),
      escalation_recipients: parseEmailList(escalationRecipients),
    };
    if (smtpPassword) input.smtp_password = smtpPassword;
    updateMutation.mutate(input);
  };

  if (settingsQuery.isPending) return <Loading label="Loading notification settings…" />;
  if (settingsQuery.isError) {
    return <ErrorState message={errorMessage(settingsQuery.error)} onRetry={() => settingsQuery.refetch()} />;
  }

  return (
    <form onSubmit={handleSubmit} className="flex max-w-xl flex-col gap-4" noValidate>
      <p className="text-sm text-slate-500 dark:text-slate-400">
        HIGH and CRITICAL incidents email these recipients automatically - once per incident per
        severity level reached, not repeatedly while it stays open.
      </p>

      {updateMutation.isError && <Alert tone="crit">{errorMessage(updateMutation.error)}</Alert>}

      <div className="grid grid-cols-3 gap-3">
        <div className="col-span-2">
          <Input label="SMTP host" value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} />
        </div>
        <Input label="Port" type="number" value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)} />
      </div>

      <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
        <input type="checkbox" checked={smtpUseTls} onChange={(e) => setSmtpUseTls(e.target.checked)} />
        Use STARTTLS
      </label>

      <Input label="SMTP username" value={smtpUsername} onChange={(e) => setSmtpUsername(e.target.value)} />
      <Input
        label={settingsQuery.data.has_smtp_password ? "SMTP password (leave blank to keep current)" : "SMTP password"}
        type="password"
        autoComplete="new-password"
        value={smtpPassword}
        onChange={(e) => setSmtpPassword(e.target.value)}
        hint="Stored encrypted. Never shown again after saving."
      />

      <div className="grid grid-cols-2 gap-3">
        <Input label="Sender email" type="email" value={senderEmail} onChange={(e) => setSenderEmail(e.target.value)} />
        <Input label="Sender name" value={senderName} onChange={(e) => setSenderName(e.target.value)} />
      </div>

      <Input
        label="Default recipients"
        value={defaultRecipients}
        onChange={(e) => setDefaultRecipients(e.target.value)}
        hint="Comma-separated emails. Notified on every HIGH or CRITICAL incident."
      />
      <Input
        label="Escalation recipients"
        value={escalationRecipients}
        onChange={(e) => setEscalationRecipients(e.target.value)}
        hint="Comma-separated emails. Notified in addition to the above, only for CRITICAL."
      />

      <Button type="submit" isLoading={updateMutation.isPending} className="w-fit">
        Save settings
      </Button>
    </form>
  );
}
