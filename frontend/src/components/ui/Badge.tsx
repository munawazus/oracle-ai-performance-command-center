import type { ReactNode } from "react";

export type Tone = "good" | "info" | "warn" | "crit" | "critical" | "neutral";

const TONE_CLASSES: Record<Tone, string> = {
  good: "bg-green-50 text-status-good dark:bg-green-950/40 dark:text-green-400",
  info: "bg-blue-50 text-status-info dark:bg-blue-950/40 dark:text-blue-300",
  warn: "bg-amber-50 text-status-warn dark:bg-amber-950/40 dark:text-amber-400",
  crit: "bg-red-50 text-status-crit dark:bg-red-950/40 dark:text-red-400",
  // Solid fill, not a soft tint - CRITICAL must read as a step above HIGH ("crit"), not identical to it.
  critical: "bg-status-crit text-white dark:bg-red-500",
  neutral: "bg-slate-100 text-status-neutral dark:bg-slate-800 dark:text-slate-300",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${TONE_CLASSES[tone]}`}
    >
      {children}
    </span>
  );
}
