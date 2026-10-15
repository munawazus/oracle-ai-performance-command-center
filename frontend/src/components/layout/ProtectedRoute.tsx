import { Navigate, Outlet, useLocation } from "react-router-dom";

import { Loading } from "../ui/Loading";
import { useAuth } from "../../lib/auth-context";
import { ForbiddenPage } from "../../pages/ForbiddenPage";

export function ProtectedRoute() {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loading label="Checking your session…" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}

export function RequirePermission({ permission, children }: { permission: string; children: React.ReactNode }) {
  const { hasPermission } = useAuth();
  if (!hasPermission(permission)) {
    return <ForbiddenPage />;
  }
  return <>{children}</>;
}
