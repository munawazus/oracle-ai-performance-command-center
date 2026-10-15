export type NavGroup = "Monitoring" | "Intelligence" | "Response";

export interface NavItem {
  label: string;
  path: string;
  /** If set, the item is only shown to users who hold this permission. */
  requiresPermission?: string;
  /** Marks a section not yet built in this phase - routes to a clear "not available" page. */
  isPlaceholder?: boolean;
  /** Sidebar section this item renders under. Omit to render ungrouped, after a divider. */
  group?: NavGroup;
}

export const NAV_ITEMS: NavItem[] = [
  { label: "Overview", path: "/", group: "Monitoring" },
  { label: "Databases", path: "/databases", requiresPermission: "database:view", group: "Monitoring" },
  { label: "Performance", path: "/performance", requiresPermission: "database:view", group: "Monitoring" },
  { label: "Activity", path: "/activity", requiresPermission: "database:view", group: "Monitoring" },
  { label: "SQL Intelligence", path: "/sql-intelligence", requiresPermission: "database:view", group: "Intelligence" },
  { label: "Sessions", path: "/sessions", requiresPermission: "database:view", group: "Intelligence" },
  { label: "Wait Analysis", path: "/wait-analysis", requiresPermission: "database:view", group: "Intelligence" },
  { label: "Blocking & Locks", path: "/blocking-locks", requiresPermission: "database:view", group: "Intelligence" },
  { label: "Capacity", path: "/capacity", requiresPermission: "database:view", group: "Intelligence" },
  { label: "Change Intelligence", path: "/change-intelligence", requiresPermission: "database:view", group: "Intelligence" },
  { label: "Health", path: "/health", requiresPermission: "database:view", group: "Intelligence" },
  { label: "Incidents", path: "/incidents", requiresPermission: "incident:view", group: "Response" },
  { label: "AI Copilot", path: "/ai-copilot", requiresPermission: "ai:query", group: "Response" },
  { label: "Reports", path: "/reports", requiresPermission: "report:create", group: "Response" },
  { label: "Administration", path: "/administration" },
];
