import { Outlet } from "react-router-dom";

import { Header } from "./Header";
import { InvestigationDrawer } from "./InvestigationDrawer";
import { Sidebar } from "./Sidebar";

export function AppShell() {
  return (
    <div className="flex h-screen w-full bg-slate-50 dark:bg-slate-950">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
      <InvestigationDrawer />
    </div>
  );
}
