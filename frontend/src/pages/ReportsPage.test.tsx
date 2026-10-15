import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ReportsPage } from "./ReportsPage";
import { ToastProvider } from "../components/ui/Toast";
import { ApiError } from "../lib/api";
import type { MonitoredDatabase, ReportDetail, ReportSummary } from "../types/api";

const mockApiFetch = vi.fn();
const mockDownloadFile = vi.fn();

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    apiFetch: (...args: unknown[]) => mockApiFetch(...args),
    downloadFile: (...args: unknown[]) => mockDownloadFile(...args),
  };
});

function renderPage(initialPath = "/reports") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <ToastProvider>
          <ReportsPage />
        </ToastProvider>
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

const REPORT_SUMMARY: ReportSummary = {
  id: "report-1",
  report_type: "HEALTH",
  requested_by_email: "admin@ocpcc.dev",
  monitored_database_id: "db-1",
  database_name_snapshot: "PROD01",
  incident_id: null,
  incident_title_snapshot: null,
  period_start: null,
  period_end: null,
  timezone: "UTC",
  generated_at: "2026-01-01T12:00:00Z",
  data_available: true,
};

const REPORT_DETAIL: ReportDetail = {
  ...REPORT_SUMMARY,
  data_completeness_note: "3 of 11 health categories were actually evaluated from real telemetry.",
  limitations: ["8 of 11 health categories are unavailable (no collector implemented yet): storage, sql_regression."],
  content: {
    overall: { overall_status: "PASS", categories_evaluated: 3, categories_total: 11 },
    categories: [{ category: "availability", status: "PASS", detail: "Reachable and fresh." }],
  },
};

function defaultFetch(reports: ReportSummary[] = [REPORT_SUMMARY]) {
  mockApiFetch.mockImplementation((path: string) => {
    if (path === "/api/databases") return Promise.resolve([DB]);
    if (path.startsWith("/api/reports/report-1")) return Promise.resolve(REPORT_DETAIL);
    if (path.startsWith("/api/reports")) return Promise.resolve(reports);
    if (path.startsWith("/api/incidents")) return Promise.resolve([]);
    return Promise.reject(new Error(`unexpected fetch: ${path}`));
  });
}

describe("ReportsPage", () => {
  it("shows an empty state when no reports have been generated", async () => {
    defaultFetch([]);
    renderPage();

    expect(await screen.findByText("No reports yet")).toBeInTheDocument();
  });

  it("lists a generated report with its type, scope, and data-availability badge", async () => {
    defaultFetch();
    renderPage();

    const table = await screen.findByRole("table");
    expect(within(table).getByText("Health report")).toBeInTheDocument();
    expect(within(table).getByText("PROD01")).toBeInTheDocument();
    expect(within(table).getByText("Available")).toBeInTheDocument();
    expect(within(table).getByText("admin@ocpcc.dev")).toBeInTheDocument();
  });

  it("generates a report and opens its content in the view drawer", async () => {
    defaultFetch([]);
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/reports" && options?.method === "POST") return Promise.resolve(REPORT_DETAIL);
      if (path.startsWith("/api/reports/report-1")) return Promise.resolve(REPORT_DETAIL);
      if (path.startsWith("/api/reports")) return Promise.resolve([]);
      if (path.startsWith("/api/incidents")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    await screen.findByText("No reports yet");
    const dbSelect = screen.getByLabelText("Database");
    await waitFor(() => expect(within(dbSelect).getByText("PROD01")).toBeInTheDocument());
    await userEvent.selectOptions(dbSelect, "db-1");
    await userEvent.click(screen.getByRole("button", { name: "Generate" }));

    expect(await screen.findByText("3 of 11 health categories were actually evaluated from real telemetry.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/reports",
      expect.objectContaining({ method: "POST", body: expect.objectContaining({ report_type: "DAILY_DBA", database_id: "db-1" }) }),
    );
  });

  it("shows a validation error from the API when generation fails", async () => {
    defaultFetch([]);
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/reports" && options?.method === "POST") {
        return Promise.reject(new ApiError(422, "period_start must be before period_end."));
      }
      if (path.startsWith("/api/reports")) return Promise.resolve([]);
      if (path.startsWith("/api/incidents")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const dbSelect = screen.getByLabelText("Database");
    await waitFor(() => expect(within(dbSelect).getByText("PROD01")).toBeInTheDocument());
    await userEvent.selectOptions(dbSelect, "db-1");
    await userEvent.click(screen.getByRole("button", { name: "Generate" }));

    expect(await screen.findByText("period_start must be before period_end.")).toBeInTheDocument();
  });

  it("filters the report list by type", async () => {
    defaultFetch();
    renderPage();

    await screen.findByRole("table");
    const typeSelect = screen.getByLabelText("Filter by type");
    await userEvent.selectOptions(typeSelect, "HEALTH");

    await waitFor(() => {
      expect(mockApiFetch).toHaveBeenCalledWith("/api/reports?report_type=HEALTH");
    });
  });

  it("opens the view drawer with limitations and downloads a CSV export", async () => {
    defaultFetch();
    renderPage();

    const table = await screen.findByRole("table");
    await userEvent.click(within(table).getByText("Health report"));

    expect(await screen.findByText(/8 of 11 health categories are unavailable/)).toBeInTheDocument();

    mockDownloadFile.mockResolvedValueOnce(undefined);
    await userEvent.click(screen.getByRole("button", { name: "Download CSV" }));

    expect(mockDownloadFile).toHaveBeenCalledWith("/api/reports/report-1/export?format=csv", "report-report-1.csv");
  });

  it("pre-selects the database in the generate form from a database_id URL param", async () => {
    defaultFetch([]);
    renderPage("/reports?database_id=db-1");

    const select = (await screen.findByLabelText("Database")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("db-1"));
  });
});
