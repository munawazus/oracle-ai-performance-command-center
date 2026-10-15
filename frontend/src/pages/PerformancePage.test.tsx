import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { countComputingSessions, PerformancePage } from "./PerformancePage";
import type {
  Anomaly,
  CpuHistoryPoint,
  InstanceHealthPoint,
  MetricCompareResponse,
  MonitoredDatabase,
  Session,
  SessionsSnapshot,
} from "../types/api";

const mockApiFetch = vi.fn();

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PerformancePage />
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

const NOT_AVAILABLE_COMPARE: MetricCompareResponse = {
  available: false,
  reason: "No current telemetry for this metric yet.",
  metric_key: null,
  metric_label: null,
  hour_of_day: null,
  observed_value: null,
  baseline_mean: null,
  baseline_stddev: null,
  sample_count: 0,
  min_samples_for_anomaly: 20,
  z_score: null,
  severity: null,
  baseline_updated_at: null,
};

const NO_SESSIONS: SessionsSnapshot = { available: false, reason: null, sampled_at: null, session_count: 0, sessions: [] };

function mockFetch(
  healthHistory: InstanceHealthPoint[],
  anomalies: Anomaly[],
  compare: MetricCompareResponse = NOT_AVAILABLE_COMPARE,
  cpuHistory: CpuHistoryPoint[] = [],
  sessions: SessionsSnapshot = NO_SESSIONS,
) {
  mockApiFetch.mockImplementation((path: string) => {
    if (path === "/api/databases") return Promise.resolve([DB]);
    if (path.startsWith("/api/databases/db-1/health-history")) return Promise.resolve(healthHistory);
    if (path === "/api/databases/db-1/anomalies?limit=200") return Promise.resolve(anomalies);
    if (path.startsWith("/api/databases/db-1/cpu/history")) return Promise.resolve(cpuHistory);
    if (path === "/api/databases/db-1/sessions") return Promise.resolve(sessions);
    if (path.startsWith("/api/databases/db-1/metrics/compare")) {
      // Two CompareModePanel instances share this route prefix, one per metric_key query
      // param - route the mocked `compare` fixture only to the panel it names.
      const matchesMetric = path.includes(`metric_key=${encodeURIComponent(compare.metric_key ?? "")}`);
      return Promise.resolve(matchesMetric ? compare : NOT_AVAILABLE_COMPARE);
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`));
  });
}

function session(overrides: Partial<Session>): Session {
  return {
    sid: 1,
    serial_num: 1,
    username: "APP_USER",
    sql_id: null,
    event: null,
    wait_class: null,
    status: "ACTIVE",
    blocking_session: null,
    machine: "host1",
    program: "sqlplus",
    module: null,
    action: null,
    state: null,
    last_call_et: null,
    ...overrides,
  };
}

describe("countComputingSessions", () => {
  it("counts only ACTIVE sessions, and only WAITED-state ones as computing", () => {
    const sessions = [
      session({ sid: 1, status: "ACTIVE", state: "WAITING" }), // blocked right now, not computing
      session({ sid: 2, status: "ACTIVE", state: "WAITED SHORT TIME" }), // genuinely on CPU
      session({ sid: 3, status: "ACTIVE", state: "WAITED KNOWN TIME" }), // genuinely on CPU
      session({ sid: 4, status: "INACTIVE", state: "WAITED SHORT TIME" }), // not active at all
    ];

    expect(countComputingSessions(sessions)).toEqual({ active: 3, computing: 2 });
  });

  it("treats a null state as not computing", () => {
    expect(countComputingSessions([session({ status: "ACTIVE", state: null })])).toEqual({
      active: 1,
      computing: 0,
    });
  });
});

describe("PerformancePage", () => {
  it("shows an honest empty state when there is no telemetry in the selected window", async () => {
    mockFetch([], []);
    renderPage();

    expect(await screen.findByText("No telemetry in this window")).toBeInTheDocument();
    expect(screen.getByText("No anomalies detected in the selected time range.")).toBeInTheDocument();
  });

  it("renders the active-session timeline and anomalies within the window", async () => {
    mockFetch(
      [
        { sampled_at: "2026-09-01T12:00:00Z", reachability: "OK", instance_status: "OPEN", active_session_count: 12 },
        { sampled_at: "2026-09-01T12:01:00Z", reachability: "OK", instance_status: "OPEN", active_session_count: 15 },
      ],
      [
        {
          id: "anom-1",
          detected_at: new Date().toISOString(),
          metric_key: "active_session_count",
          observed_value: 40,
          baseline_mean: 15,
          baseline_stddev: 3,
          sample_count: 20,
          severity: "HIGH",
        },
      ],
      {
        ...NOT_AVAILABLE_COMPARE,
        available: true,
        metric_key: "active_session_count",
        metric_label: "Active session count",
        hour_of_day: 12,
        observed_value: 40,
        baseline_mean: 15,
        baseline_stddev: 3,
        sample_count: 20,
        z_score: 8.33,
        severity: "HIGH",
      },
    );
    renderPage();

    expect(await screen.findByText("Active session count")).toBeInTheDocument();
    expect(await screen.findByText("active_session_count")).toBeInTheDocument();
    expect(screen.getAllByText("HIGH").length).toBeGreaterThan(0);
    expect(await screen.findByText("Active session count vs. its baseline for this hour")).toBeInTheDocument();
    expect(screen.getByText("Baseline 15.0 ± 3.0 (20 of 20+ samples)")).toBeInTheDocument();
  });

  it("renders the CPU usage chart and its Compare Mode panel", async () => {
    mockFetch(
      [],
      [],
      {
        ...NOT_AVAILABLE_COMPARE,
        available: true,
        metric_key: "cpu_centisec_used_per_sec",
        metric_label: "Database CPU usage",
        hour_of_day: 12,
        observed_value: 55.1,
        baseline_mean: 40,
        baseline_stddev: 5,
        sample_count: 25,
        z_score: 3.02,
        severity: "MEDIUM",
      },
      [
        { sampled_at: "2026-09-01T12:00:00Z", elapsed_seconds: 60, cpu_centisec_used_per_sec: 42.5 },
        { sampled_at: "2026-09-01T12:01:00Z", elapsed_seconds: 60, cpu_centisec_used_per_sec: 55.1 },
      ],
    );
    renderPage();

    expect(await screen.findByText("Database CPU usage (% of one CPU core)")).toBeInTheDocument();
    expect(await screen.findByText("Database CPU usage vs. its baseline for this hour")).toBeInTheDocument();
    expect(screen.getByText("Baseline 40.0% ± 5.0% (25 of 20+ samples)")).toBeInTheDocument();
    expect(screen.getAllByText("MEDIUM").length).toBeGreaterThan(0);
  });

  it("explains a CPU reading with real active-session context", async () => {
    mockFetch(
      [],
      [],
      {
        ...NOT_AVAILABLE_COMPARE,
        available: true,
        metric_key: "cpu_centisec_used_per_sec",
        metric_label: "Database CPU usage",
        observed_value: 55.1,
      },
      [],
      {
        available: true,
        reason: null,
        sampled_at: "2026-09-01T12:00:00Z",
        session_count: 3,
        sessions: [
          session({ sid: 1, status: "ACTIVE", state: "WAITED SHORT TIME" }),
          session({ sid: 2, status: "ACTIVE", state: "WAITING" }),
          session({ sid: 3, status: "INACTIVE", state: null }),
        ],
      },
    );
    renderPage();

    expect(await screen.findByText("2 active sessions · 1 genuinely computing right now")).toBeInTheDocument();
  });

  it("requests hours and until for a custom range", async () => {
    mockFetch([], []);
    renderPage();
    await screen.findByText("No telemetry in this window");

    await userEvent.click(screen.getByRole("button", { name: "Custom" }));
    await userEvent.type(screen.getByLabelText("Start"), "2026-01-01T00:00");
    await userEvent.type(screen.getByLabelText("End"), "2026-01-01T02:00");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    const expectedUntil = new Date("2026-01-01T02:00").toISOString();
    expect(await screen.findByText("No telemetry in this window")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith(
      `/api/databases/db-1/health-history?hours=2${`&until=${encodeURIComponent(expectedUntil)}`}`,
    );
  });
});
