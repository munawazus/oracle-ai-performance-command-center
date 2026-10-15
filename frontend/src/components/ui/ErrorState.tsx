import { Button } from "./Button";

interface ErrorStateProps {
  message: string;
  onRetry?: () => void;
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 rounded-lg border border-status-crit/30 bg-red-50 px-6 py-10 text-center dark:border-red-800/40 dark:bg-red-950/40"
    >
      <p className="text-sm font-medium text-status-crit dark:text-red-400">{message}</p>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
