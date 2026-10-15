import { useState } from "react";

export interface TimeRange {
  hours: number;
  /** ISO timestamp. Only set for a Custom range - presets are always "hours before now". */
  until?: string;
}

const PRESETS: { label: string; hours: number }[] = [
  { label: "15m", hours: 0.25 },
  { label: "1h", hours: 1 },
  { label: "6h", hours: 6 },
  { label: "24h", hours: 24 },
  { label: "7d", hours: 168 },
];

interface TimeRangeControlProps {
  value: TimeRange;
  onChange: (range: TimeRange) => void;
}

const inputClassName =
  "rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-900 shadow-sm outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:border-brand-400 dark:focus:ring-brand-400";

export function TimeRangeControl({ value, onChange }: TimeRangeControlProps) {
  const [customOpen, setCustomOpen] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [customError, setCustomError] = useState<string | null>(null);

  const isCustomActive = value.until !== undefined;

  const applyCustom = () => {
    if (!start || !end) {
      setCustomError("Both start and end are required.");
      return;
    }
    const startMs = new Date(start).getTime();
    const endMs = new Date(end).getTime();
    const hours = (endMs - startMs) / 3_600_000;
    if (!(hours > 0)) {
      setCustomError("End must be after start.");
      return;
    }
    setCustomError(null);
    onChange({ hours, until: new Date(endMs).toISOString() });
    setCustomOpen(false);
  };

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex gap-1 rounded-md border border-slate-200 bg-white p-1 dark:border-slate-800 dark:bg-slate-900">
        {PRESETS.map((opt) => (
          <button
            key={opt.label}
            type="button"
            onClick={() => {
              setCustomOpen(false);
              onChange({ hours: opt.hours });
            }}
            className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
              !isCustomActive && value.hours === opt.hours
                ? "bg-brand-600 text-white dark:bg-brand-500"
                : "text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
            }`}
          >
            {opt.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setCustomOpen((v) => !v)}
          className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
            isCustomActive
              ? "bg-brand-600 text-white dark:bg-brand-500"
              : "text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
          }`}
        >
          Custom
        </button>
      </div>

      {customOpen && (
        <div className="flex flex-wrap items-end gap-2 rounded-md border border-slate-200 bg-white p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300" htmlFor="range-start">
              Start
            </label>
            <input
              id="range-start"
              type="datetime-local"
              className={inputClassName}
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300" htmlFor="range-end">
              End
            </label>
            <input
              id="range-end"
              type="datetime-local"
              className={inputClassName}
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </div>
          <button
            type="button"
            onClick={applyCustom}
            className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 dark:bg-brand-500 dark:hover:bg-brand-600"
          >
            Apply
          </button>
          {customError && <p className="w-full text-xs text-status-crit dark:text-red-400">{customError}</p>}
        </div>
      )}
    </div>
  );
}
