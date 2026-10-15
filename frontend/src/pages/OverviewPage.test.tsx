import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { OverviewPage } from "./OverviewPage";
import type { CollectorStatus, DatabaseGroup, Incident, MonitoredDatabase } from "../types/api";

const mockApiFetch = vi.fn();
const mockHasPermission = vi.fn();

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

vi.mock("../lib/auth-context", () => ({
  useAuth: () => ({ hasPermission: mockHasPermission }),
}));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const DB: MonitoredDatabase = {
  id: "db-1",
  name: "PROD01",
  host: "db.internal",
  port: 1521,
  service_name: "ORCLPDB1",
  edition_hint: "SE2",
  is_active: true,
  tags: [],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const NEVER_COLLECTED: CollectorStatus = {
  last_run_at: null,
  last_success_at: null,
  last_failure_at: null,
  consecutive_failures: 0,
  last_error_category: null,
  last_run_duration_ms: null,
};

const FAILING: CollectorStatus = {
  last_run_at: "2026-01-01T00:00:10Z",
  last_success_at: null,
  last_failure_at: "2026-01-01T00:00:10Z",
  consecutive_failures: 3,
  last_error_category: "unreachable",
  last_run_duration_ms: 120,
};

function mockFetch(collectorStatus: CollectorStatus) {
  mockApiFetch.mockImplementation((path: string) => {
    if (path === "/api/databases/db-1/collector-status") return Promise.resolve(collectorStatus);
    if (path.startsWith("/api/databases")) return Promise.resolve([DB]);
    if (path === "/api/database-groups") return Promise.resolve([]);
    if (path.startsWith("/api/incidents")) return Promise.resolve([]);
    return Promise.reject(new Error(`unexpected fetch: ${path}`));
  });
}

describe("OverviewPage", () => {
  it("shows a not-yet-collecting badge for a freshly registered database", async () => {
    mockHasPermission.mockReturnValue(true);
    mockFetch(NEVER_COLLECTED);
    renderPage();

    expect(await screen.findByText("Not yet collecting")).toBeInTheDocument();
  });

  it("shows a failing badge with the consecutive failure count", async () => {
    mockHasPermission.mockReturnValue(true);
    mockFetch(FAILING);
    renderPage();

    expect(await screen.findByText(/Collection failing \(3\)/)).toBeInTheDocument();
  });

  it("shows an empty state for a user without database:view", async () => {
    mockHasPermission.mockReturnValue(false);
    renderPage();

    expect(await screen.findByText("Nothing to show yet")).toBeInTheDocument();
  });

  it("shows the open-incident count as a drill-down link to the Incidents view", async () => {
    mockHasPermission.mockReturnValue(true);
    const incident: Incident = {
      id: "inc-1",
      monitored_database_id: "db-1",
      status: "OPEN",
      severity: "HIGH",
      title: "Anomaly",
      opened_at: "2026-01-01T00:00:00Z",
      last_evidence_at: "2026-01-01T00:00:00Z",
      acknowledged_at: null,
      acknowledged_by_user_id: null,
      assigned_to_user_id: null,
      resolved_at: null,
      resolved_by_user_id: null,
      resolution_note: null,
    };
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases/db-1/collector-status") return Promise.resolve(NEVER_COLLECTED);
      if (path.startsWith("/api/databases")) return Promise.resolve([DB]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path.startsWith("/api/incidents")) return Promise.resolve([incident]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const link = await screen.findByRole("link", { name: /1 open incident\(s\)/ });
    expect(link).toHaveAttribute("href", "/incidents?database_id=db-1");
  });

  it("filters the database list by group", async () => {
    const group: DatabaseGroup = {
      id: "grp-1",
      name: "Production",
      description: "",
      database_count: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };
    mockHasPermission.mockReturnValue(true);
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases/db-1/collector-status") return Promise.resolve(NEVER_COLLECTED);
      if (path === "/api/databases?group_id=grp-1") return Promise.resolve([DB]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/database-groups") return Promise.resolve([group]);
      if (path.startsWith("/api/incidents")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    await screen.findByText("PROD01");
    await userEvent.selectOptions(screen.getByLabelText("Group"), "grp-1");

    expect(mockApiFetch).toHaveBeenCalledWith("/api/databases?group_id=grp-1");
  });
});
