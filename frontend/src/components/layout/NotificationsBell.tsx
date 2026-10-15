import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { apiFetch } from "../../lib/api";
import { SEVERITY_TONE } from "../../lib/severity";
import { formatRelativeTime } from "../../lib/time";
import type { Incident } from "../../types/api";
import { Badge } from "../ui/Badge";

const NOTIFIABLE_SEVERITIES = new Set(["HIGH", "CRITICAL"]);
const MAX_SHOWN = 10;

export function NotificationsBell() {
  const [isOpen, setIsOpen] = useState(false);
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);

  const incidentsQuery = useQuery({
    queryKey: ["incidents", "fleet-notifications"],
    queryFn: () => apiFetch<Incident[]>("/api/incidents?status=OPEN"),
    refetchInterval: 20_000,
  });

  const notifiable = (incidentsQuery.data ?? [])
    .filter((i) => NOTIFIABLE_SEVERITIES.has(i.severity))
    .sort((a, b) => new Date(b.last_evidence_at).getTime() - new Date(a.last_evidence_at).getTime());

  useEffect(() => {
    if (!isOpen) return;
    const onMouseDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsOpen(false);
    };
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [isOpen]);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-label={`Notifications - ${notifiable.length} open HIGH or CRITICAL incident(s)`}
        className="relative rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-200"
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none" className="h-5 w-5" aria-hidden="true">
          <path
            d="M5 8a5 5 0 0 1 10 0c0 3.2 1 4.5 1.5 5H3.5C4 12.5 5 11.2 5 8Z"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinejoin="round"
          />
          <path d="M8.2 15.5a1.8 1.8 0 0 0 3.6 0" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        {notifiable.length > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-status-crit px-1 text-[10px] font-semibold leading-none text-white dark:bg-red-500">
            {notifiable.length > 99 ? "99+" : notifiable.length}
          </span>
        )}
      </button>

      {isOpen && (
        <div
          role="dialog"
          className="absolute right-0 z-20 mt-1 w-96 rounded-md border border-slate-200 bg-white p-2 shadow-lg dark:border-slate-800 dark:bg-slate-900"
        >
          <p className="px-2 py-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
            Open HIGH / CRITICAL incidents
          </p>
          {notifiable.length === 0 ? (
            <p className="px-2 py-3 text-sm text-slate-500 dark:text-slate-400">Nothing open right now.</p>
          ) : (
            <div className="max-h-96 overflow-y-auto">
              {notifiable.slice(0, MAX_SHOWN).map((i) => (
                <button
                  key={i.id}
                  type="button"
                  onClick={() => {
                    setIsOpen(false);
                    navigate(`/incidents?incident_id=${i.id}`);
                  }}
                  className="flex w-full items-start justify-between gap-2 rounded px-2 py-2 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-800"
                >
                  <span className="flex flex-col gap-0.5">
                    <span className="flex items-center gap-2">
                      <Badge tone={SEVERITY_TONE[i.severity]}>{i.severity}</Badge>
                      <span className="text-slate-700 dark:text-slate-300">{i.title}</span>
                    </span>
                  </span>
                  <span className="shrink-0 text-xs text-slate-400 dark:text-slate-500">
                    {formatRelativeTime(i.last_evidence_at)}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
