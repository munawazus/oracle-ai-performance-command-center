import { NavLink } from "react-router-dom";

import { useAuth } from "../../lib/auth-context";
import { NAV_ITEMS, type NavGroup, type NavItem } from "../../lib/nav";

const GROUP_ORDER: NavGroup[] = ["Monitoring", "Intelligence", "Response"];

function groupItems(items: NavItem[]): { group: NavGroup; items: NavItem[] }[] {
  return GROUP_ORDER.map((group) => ({ group, items: items.filter((item) => item.group === group) })).filter(
    (section) => section.items.length > 0,
  );
}

export function Sidebar() {
  const { hasPermission } = useAuth();
  const items = NAV_ITEMS.filter((item) => !item.requiresPermission || hasPermission(item.requiresPermission));
  const sections = groupItems(items);
  const ungrouped = items.filter((item) => !item.group);

  const linkClassName = ({ isActive }: { isActive: boolean }) =>
    `flex items-center justify-between gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
      isActive
        ? "bg-brand-50 text-brand-700 dark:bg-brand-900/30 dark:text-brand-300"
        : "text-slate-600 hover:bg-slate-50 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
    }`;

  return (
    <nav aria-label="Primary" className="flex h-full w-56 flex-col border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
      <div className="px-4 py-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-brand-600 dark:text-brand-400">OCPCC</p>
        <p className="text-xs text-slate-500 dark:text-slate-400">Command Center</p>
      </div>
      <ul className="flex-1 space-y-3 overflow-y-auto px-2 pb-4">
        {sections.map((section) => (
          <li key={section.group}>
            <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
              {section.group}
            </p>
            <ul className="space-y-0.5">
              {section.items.map((item) => (
                <li key={item.path}>
                  <NavLink to={item.path} end={item.path === "/"} className={linkClassName}>
                    <span>{item.label}</span>
                    {item.isPlaceholder && (
                      <span
                        title="Not available yet"
                        className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400 dark:bg-slate-800 dark:text-slate-500"
                      >
                        Soon
                      </span>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </li>
        ))}

        {ungrouped.length > 0 && (
          <li className="border-t border-slate-200 pt-3 dark:border-slate-800">
            <ul className="space-y-0.5">
              {ungrouped.map((item) => (
                <li key={item.path}>
                  <NavLink to={item.path} end={item.path === "/"} className={linkClassName}>
                    <span>{item.label}</span>
                  </NavLink>
                </li>
              ))}
            </ul>
          </li>
        )}
      </ul>
    </nav>
  );
}
