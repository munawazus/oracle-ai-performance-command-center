import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { SEVERITY_TONE } from "../../lib/severity";
import { apiFetch } from "../../lib/api";
import type { Incident, MonitoredDatabase } from "../../types/api";
import { Badge } from "../ui/Badge";

function matches(haystacks: (string | null | undefined)[], needle: string): boolean {
  const q = needle.toLowerCase();
  return haystacks.some((h) => h?.toLowerCase().includes(q));
}

export function GlobalSearch() {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const databasesQuery = useQuery({
    queryKey: ["databases"],
    queryFn: () => apiFetch<MonitoredDatabase[]>("/api/databases"),
    enabled: isOpen,
  });

  const incidentsQuery = useQuery({
    queryKey: ["incidents", "fleet-search"],
    queryFn: () => apiFetch<Incident[]>("/api/incidents"),
    enabled: isOpen,
  });

  useEffect(() => {
    if (!isOpen) return;
    inputRef.current?.focus();
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

  const close = () => {
    setIsOpen(false);
    setQuery("");
  };

  const trimmed = query.trim();
  const matchedDatabases = trimmed
    ? (databasesQuery.data ?? []).filter((d) => matches([d.name, d.host, ...d.tags], trimmed))
    : [];
  const matchedIncidents = trimmed
    ? (incidentsQuery.data ?? []).filter((i) => matches([i.title, i.severity, i.id], trimmed))
    : [];
  const hasResults = matchedDatabases.length > 0 || matchedIncidents.length > 0;

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-label="Search databases and incidents"
        className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-200"
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none" className="h-5 w-5" aria-hidden="true">
          <circle cx="9" cy="9" r="6" stroke="currentColor" strokeWidth="1.6" />
          <path d="M17 17L13.5 13.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
      </button>

      {isOpen && (
        <div
          role="dialog"
          className="absolute right-0 z-20 mt-1 w-80 rounded-md border border-slate-200 bg-white p-2 shadow-lg dark:border-slate-800 dark:bg-slate-900"
        >
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search databases or incidents…"
            className="w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-900 outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:focus:border-brand-400 dark:focus:ring-brand-400"
          />

          {trimmed && (
            <div className="mt-2 max-h-80 overflow-y-auto">
              {!hasResults && <p className="px-2 py-3 text-sm text-slate-500 dark:text-slate-400">No matches.</p>}

              {matchedDatabases.length > 0 && (
                <div className="mb-2">
                  <p className="px-2 py-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
                    Databases
                  </p>
                  {matchedDatabases.map((d) => (
                    <button
                      key={d.id}
                      type="button"
                      onClick={() => {
                        close();
                        navigate(`/databases?id=${d.id}`);
                      }}
                      className="block w-full rounded px-2 py-1.5 text-left text-sm text-slate-700 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"
                    >
                      <span className="font-medium text-slate-900 dark:text-slate-100">{d.name}</span>
                      <span className="ml-1.5 text-xs text-slate-400 dark:text-slate-500">{d.host}</span>
                    </button>
                  ))}
                </div>
              )}

              {matchedIncidents.length > 0 && (
                <div>
                  <p className="px-2 py-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
                    Incidents
                  </p>
                  {matchedIncidents.map((i) => (
                    <button
                      key={i.id}
                      type="button"
                      onClick={() => {
                        close();
                        navigate(`/incidents?incident_id=${i.id}`);
                      }}
                      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm text-slate-700 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"
                    >
                      <Badge tone={SEVERITY_TONE[i.severity]}>{i.severity}</Badge>
                      <span className="truncate">{i.title}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
