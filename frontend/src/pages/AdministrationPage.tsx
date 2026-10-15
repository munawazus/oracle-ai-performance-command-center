import { useState } from "react";

import { Tabs } from "../components/ui/Tabs";
import { EmptyState } from "../components/ui/EmptyState";
import { useAuth } from "../lib/auth-context";
import { AuditPanel } from "./administration/AuditPanel";
import { IntegrationsPanel } from "./administration/IntegrationsPanel";
import { NotificationsPanel } from "./administration/NotificationsPanel";
import { UsersPanel } from "./administration/UsersPanel";

export function AdministrationPage() {
  const { hasPermission } = useAuth();
  const canManageUsers = hasPermission("admin:users");
  const canViewAudit = hasPermission("audit:view");
  const canConfigureNotifications = hasPermission("notification:configure");

  const availableTabs = [
    canManageUsers ? { id: "users", label: "Users" } : null,
    canConfigureNotifications ? { id: "notifications", label: "Notifications" } : null,
    canConfigureNotifications ? { id: "integrations", label: "Integrations" } : null,
    canViewAudit ? { id: "audit", label: "Audit Log" } : null,
  ].filter((t): t is { id: string; label: string } => t !== null);

  const [activeTab, setActiveTab] = useState(availableTabs[0]?.id ?? "");

  if (availableTabs.length === 0) {
    return (
      <EmptyState
        title="Nothing to administer"
        description="Your role doesn't include admin:users, notification:configure, or audit:view, so there's nothing to show here."
      />
    );
  }

  const currentTab = activeTab || availableTabs[0].id;

  return (
    <div className="flex flex-col gap-4">
      <Tabs tabs={availableTabs} activeId={currentTab} onChange={setActiveTab} />
      {currentTab === "users" && canManageUsers && <UsersPanel />}
      {currentTab === "notifications" && canConfigureNotifications && <NotificationsPanel />}
      {currentTab === "integrations" && canConfigureNotifications && <IntegrationsPanel />}
      {currentTab === "audit" && canViewAudit && <AuditPanel />}
    </div>
  );
}
