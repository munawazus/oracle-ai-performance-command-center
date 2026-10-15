import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-2 text-center">
      <p className="text-2xl font-semibold text-slate-800 dark:text-slate-200">Page not found</p>
      <p className="text-sm text-slate-500 dark:text-slate-400">The page you're looking for doesn't exist.</p>
      <Link to="/" className="mt-2 text-sm font-medium text-brand-600 hover:underline dark:text-brand-400">
        Back to Overview
      </Link>
    </div>
  );
}
