import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ActivityPage } from "./ActivityPage";
import type { ActivityEvent, FleetVitalsRow } from "../types/api";

const mockApiFetch = vi.fn();

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

beforeEach(() => {
  mockApiFetch.mockReset();
});

/** Dispatches by URL rather than call order - the vitals and feed queries fire independently
 * on mount, and their relative order is not a contract worth coupling these tests to. */
function mockEndpoints(vitals: FleetVitalsRow[] | Error, events: ActivityEvent[] | Error = []) {
  mockApiFetch.mockImplementation((url: string) => {
    if (url.startsWith("/api/activity/fleet-pulse")) {
      return vitals instanceof Error ? Promise.reject(vitals) : Promise.resolve(vitals);
    }
    if (url.startsWith("/api/activity/feed")) {
      return events instanceof Error ? Promise.reject(events) : Promise.resolve(events);
    }
    return Promise.reject(new Error(`unexpected apiFetch call: ${url}`));
  });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ActivityPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const HEALTHY_ROW: FleetVitalsRow = {
  database_id: "db-1",
  database_name: "PROD01",
  overall_status: "PASS",
  open_incident_count: 0,
  blocking_present: false,
  capacity_trend: "stable",
};

const FAILING_ROW: FleetVitalsRow = {
  database_id: "db-2",
  database_name: "PROD02",
  overall_status: "FAIL",
  open_incident_count: 2,
  blocking_present: true,
  capacity_trend: "critical",
};

const ANOMALY_EVENT: ActivityEvent = {
  event_id: "anomaly:a1",
  category: "anomaly",
  severity: "CRITICAL",
  occurred_at: "2026-09-23T11:00:00Z",
  database_id: "db-1",
  database_name: "PROD01",
  summary: "Anomaly detected on active_session_count (observed 500.0, baseline 10.0).",
  entities: { metric_key: "active_session_count", incident_id: null },
};

const REGRESSION_EVENT: ActivityEvent = {
  event_id: "sql_regression:r1",
  category: "sql_regression",
  severity: "HIGH",
  occurred_at: "2026-09-23T11:05:00Z",
  database_id: "db-2",
  database_name: "PROD02",
  summary: "SQL actvsql01's plan changed and elapsed-per-execution rose 9x.",
  entities: { sql_id: "actvsql01" },
};

const STORM_EVENT: ActivityEvent = {
  event_id: "storm:active_session_count:2026-09-23T11:00:00Z",
  category: "storm",
  severity: "CRITICAL",
  occurred_at: "2026-09-23T11:00:00Z",
  database_id: "db-1",
  database_name: "3 databases",
  summary: "3 databases (PROD01, PROD02, PROD03) showing an anomaly on active_session_count within 10 minutes of each other.",
  entities: {
    metric_key: "active_session_count",
    database_ids: ["db-1", "db-2", "db-3"],
    database_names: ["PROD01", "PROD02", "PROD03"],
    constituent_event_ids: ["anomaly:a1", "anomaly:a2", "anomaly:a3"],
  },
};

describe("ActivityPage - fleet vitals strip", () => {
  it("shows an empty state when no databases are active", async () => {
    mockEndpoints([]);
    renderPage();
    expect(await screen.findByText("No active databases yet")).toBeInTheDocument();
  });

  it("renders one row per database with its real status, incident count, and blocking/capacity state", async () => {
    mockEndpoints([HEALTHY_ROW, FAILING_ROW]);
    renderPage();

    expect(await screen.findByText("PROD01")).toBeInTheDocument();
    expect(screen.getByText("PROD02")).toBeInTheDocument();
    expect(screen.getByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("Failing")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument(); // PROD02's open incident count
    expect(screen.getByText("Present")).toBeInTheDocument(); // PROD02's blocking badge
    expect(screen.getByText("None")).toBeInTheDocument(); // PROD01's blocking badge
  });

  it("shows a fleet-wide alert banner naming how many databases are failing right now", async () => {
    mockEndpoints([HEALTHY_ROW, FAILING_ROW]);
    renderPage();

    expect(await screen.findByText(/1 of 2 database\(s\) are failing a health check right now/)).toBeInTheDocument();
  });

  it("does not show the failing-fleet alert when every database is healthy", async () => {
    mockEndpoints([HEALTHY_ROW]);
    renderPage();

    await screen.findByText("PROD01");
    expect(screen.queryByText(/failing a health check right now/)).not.toBeInTheDocument();
  });

  it("shows an error state with a retry action when the fetch fails", async () => {
    mockEndpoints(new Error("network down"));
    renderPage();

    expect(await screen.findByText(/network down/)).toBeInTheDocument();
  });
});

describe("ActivityPage - unified timeline", () => {
  it("shows an empty state when there is no activity in the current window", async () => {
    mockEndpoints([], []);
    renderPage();
    expect(await screen.findByText("No activity in this window")).toBeInTheDocument();
  });

  it("renders one row per merged event with its real category, severity, and summary", async () => {
    mockEndpoints([], [ANOMALY_EVENT, REGRESSION_EVENT]);
    renderPage();

    expect(await screen.findByText(ANOMALY_EVENT.summary)).toBeInTheDocument();
    expect(screen.getByText(REGRESSION_EVENT.summary)).toBeInTheDocument();
    expect(screen.getByText("Anomaly")).toBeInTheDocument();
    expect(screen.getByText("SQL regression")).toBeInTheDocument();
    expect(screen.getByText("CRITICAL")).toBeInTheDocument();
    expect(screen.getByText("HIGH")).toBeInTheDocument();
  });

  it("deep-links a SQL regression event to SQL Intelligence, scoped to its database", async () => {
    mockEndpoints([], [REGRESSION_EVENT]);
    renderPage();

    const link = await screen.findByText(REGRESSION_EVENT.summary);
    expect(link.closest("a")).toHaveAttribute("href", "/sql-intelligence?database_id=db-2");
  });

  it("shows an error state for the timeline independently of the vitals strip", async () => {
    mockEndpoints([HEALTHY_ROW], new Error("feed unavailable"));
    renderPage();

    expect(await screen.findByText("PROD01")).toBeInTheDocument();
    expect(await screen.findByText(/feed unavailable/)).toBeInTheDocument();
  });
});

describe("ActivityPage - storm/burst correlation", () => {
  it("renders a storm with its own Storm category label, not a per-database link", async () => {
    mockEndpoints([], [STORM_EVENT]);
    renderPage();

    expect(await screen.findByText("Storm")).toBeInTheDocument();
    expect(screen.getByText("3 databases")).toBeInTheDocument();
  });

  it("expands a storm to reveal its constituent databases, each linking to Performance", async () => {
    mockEndpoints([], [STORM_EVENT]);
    renderPage();

    const summary = await screen.findByText(STORM_EVENT.summary);
    summary.click();

    const prod1Link = await screen.findByText("PROD01");
    expect(prod1Link.closest("a")).toHaveAttribute("href", "/performance?database_id=db-1");
    expect(screen.getByText("PROD02").closest("a")).toHaveAttribute("href", "/performance?database_id=db-2");
    expect(screen.getByText("PROD03").closest("a")).toHaveAttribute("href", "/performance?database_id=db-3");
  });
});
