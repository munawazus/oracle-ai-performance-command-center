import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import { NAV_ITEMS } from "../../lib/nav";
import { useInvestigation } from "../../lib/investigation-context";
import { GlobalSearch } from "./GlobalSearch";
import { NotificationsBell } from "./NotificationsBell";
import { UserMenu } from "./UserMenu";

export function Header() {
  const location = useLocation();
  const current = NAV_ITEMS.find((item) => item.path === location.pathname);
  const queryClient = useQueryClient();
  const isFetching = useIsFetching() > 0;
  const { isOpen: isInvestigationOpen, toggle: toggleInvestigation } = useInvestigation();

  return (
    <header className="flex h-14 items-center justify-between border-b border-slate-200 bg-white px-6 dark:border-slate-800 dark:bg-slate-900">
      <h1 className="text-sm font-semibold text-slate-800 dark:text-slate-200">{current?.label ?? "Oracle AI Performance Command Center"}</h1>
      <div className="flex items-center gap-1">
        <GlobalSearch />
        <button
          type="button"
          onClick={() => queryClient.invalidateQueries()}
          aria-label="Refresh"
          title="Refresh"
          className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-200"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="none"
            className={`h-5 w-5 ${isFetching ? "animate-spin" : ""}`}
            aria-hidden="true"
          >
            <path
              d="M16 5.5V9h-3.5M4 14.5V11h3.5M15.5 8.5A5.5 5.5 0 0 0 5.6 6.6M4.5 11.5a5.5 5.5 0 0 0 9.9 1.9"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <NotificationsBell />
        <button
          type="button"
          onClick={toggleInvestigation}
          aria-label="Toggle investigation panel"
          aria-pressed={isInvestigationOpen}
          title="Investigation"
          className={`rounded-md p-1.5 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200 ${
            isInvestigationOpen ? "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200" : "text-slate-500 dark:text-slate-400"
          }`}
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none" className="h-5 w-5" aria-hidden="true">
            <rect x="2.5" y="3.5" width="15" height="13" rx="1.5" stroke="currentColor" strokeWidth="1.6" />
            <path d="M13 3.5v13" stroke="currentColor" strokeWidth="1.6" />
          </svg>
        </button>
        <div className="mx-1 h-6 w-px bg-slate-200 dark:bg-slate-800" />
        <UserMenu />
      </div>
    </header>
  );
}
