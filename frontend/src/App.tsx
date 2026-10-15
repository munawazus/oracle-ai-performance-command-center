import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/layout/AppShell";
import { ProtectedRoute, RequirePermission } from "./components/layout/ProtectedRoute";
import { Loading } from "./components/ui/Loading";
import { ActivityPage } from "./pages/ActivityPage";
import { AdministrationPage } from "./pages/AdministrationPage";
import { AiCopilotPage } from "./pages/AiCopilotPage";
import { BlockingLocksPage } from "./pages/BlockingLocksPage";
import { CapacityPage } from "./pages/CapacityPage";
import { ChangeIntelligencePage } from "./pages/ChangeIntelligencePage";
import { DatabasesPage } from "./pages/DatabasesPage";
import { HealthPage } from "./pages/HealthPage";
import { IncidentsPage } from "./pages/IncidentsPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { ReportsPage } from "./pages/ReportsPage";
import { SessionsPage } from "./pages/SessionsPage";
import { SqlIntelligencePage } from "./pages/SqlIntelligencePage";
import { NAV_ITEMS } from "./lib/nav";

// Chart-heavy pages (recharts) are split into their own chunk - loaded on demand instead of
// adding ~150kB gzipped to every route.
const PerformancePage = lazy(() => import("./pages/PerformancePage").then((m) => ({ default: m.PerformancePage })));
const WaitAnalysisPage = lazy(() => import("./pages/WaitAnalysisPage").then((m) => ({ default: m.WaitAnalysisPage })));

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route path="/" element={<OverviewPage />} />
          <Route
            path="/databases"
            element={
              <RequirePermission permission="database:view">
                <DatabasesPage />
              </RequirePermission>
            }
          />
          <Route
            path="/activity"
            element={
              <RequirePermission permission="database:view">
                <ActivityPage />
              </RequirePermission>
            }
          />
          <Route
            path="/performance"
            element={
              <RequirePermission permission="database:view">
                <Suspense fallback={<Loading label="Loading…" />}>
                  <PerformancePage />
                </Suspense>
              </RequirePermission>
            }
          />
          <Route
            path="/sessions"
            element={
              <RequirePermission permission="database:view">
                <SessionsPage />
              </RequirePermission>
            }
          />
          <Route
            path="/wait-analysis"
            element={
              <RequirePermission permission="database:view">
                <Suspense fallback={<Loading label="Loading…" />}>
                  <WaitAnalysisPage />
                </Suspense>
              </RequirePermission>
            }
          />
          <Route
            path="/sql-intelligence"
            element={
              <RequirePermission permission="database:view">
                <SqlIntelligencePage />
              </RequirePermission>
            }
          />
          <Route
            path="/blocking-locks"
            element={
              <RequirePermission permission="database:view">
                <BlockingLocksPage />
              </RequirePermission>
            }
          />
          <Route
            path="/capacity"
            element={
              <RequirePermission permission="database:view">
                <CapacityPage />
              </RequirePermission>
            }
          />
          <Route
            path="/change-intelligence"
            element={
              <RequirePermission permission="database:view">
                <ChangeIntelligencePage />
              </RequirePermission>
            }
          />
          <Route
            path="/health"
            element={
              <RequirePermission permission="database:view">
                <HealthPage />
              </RequirePermission>
            }
          />
          <Route
            path="/incidents"
            element={
              <RequirePermission permission="incident:view">
                <IncidentsPage />
              </RequirePermission>
            }
          />
          <Route
            path="/ai-copilot"
            element={
              <RequirePermission permission="ai:query">
                <AiCopilotPage />
              </RequirePermission>
            }
          />
          <Route
            path="/reports"
            element={
              <RequirePermission permission="report:create">
                <ReportsPage />
              </RequirePermission>
            }
          />
          <Route path="/administration" element={<AdministrationPage />} />

          {NAV_ITEMS.filter((item) => item.isPlaceholder).map((item) => (
            <Route key={item.path} path={item.path} element={<PlaceholderPage title={item.label} />} />
          ))}

          <Route path="/404" element={<NotFoundPage />} />
          <Route path="*" element={<Navigate to="/404" replace />} />
        </Route>
      </Route>
    </Routes>
  );
}
