import type { ReactNode } from "react";

type Tone = "info" | "warn" | "crit" | "good";

const TONE_CLASSES: Record<Tone, string> = {
  info: "border-status-info/30 bg-blue-50 text-status-info dark:border-blue-800/40 dark:bg-blue-950/40 dark:text-blue-300",
  warn: "border-status-warn/30 bg-amber-50 text-status-warn dark:border-amber-800/40 dark:bg-amber-950/40 dark:text-amber-400",
  crit: "border-status-crit/30 bg-red-50 text-status-crit dark:border-red-800/40 dark:bg-red-950/40 dark:text-red-400",
  good: "border-status-good/30 bg-green-50 text-status-good dark:border-green-800/40 dark:bg-green-950/40 dark:text-green-400",
};

export function Alert({ tone = "info", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <div role="alert" className={`rounded-md border px-3 py-2 text-sm ${TONE_CLASSES[tone]}`}>
      {children}
    </div>
  );
}
