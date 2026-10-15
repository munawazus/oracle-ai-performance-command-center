import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { addCpuLayer, currentWaitProfile, groupWaitClasses, WaitAnalysisPage } from "./WaitAnalysisPage";
import type {
  CapabilityProfile,
  CpuHistoryPoint,
  MonitoredDatabase,
  SessionsSnapshot,
  WaitClassHistoryPoint,
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
      <WaitAnalysisPage />
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

const UNPROBED_CAPABILITIES: CapabilityProfile = {
  detected_version: null,
  detected_edition: null,
  is_rac: null,
  statspack_available: null,
  diagnostics_tuning_pack_licensed_confirmed: false,
  privilege_report: null,
  last_probed_at: null,
  cpu_count: null,
};

const NO_SESSIONS: SessionsSnapshot = { available: false, reason: null, sampled_at: null, session_count: 0, sessions: [] };

function mockFetch(
  points: WaitClassHistoryPoint[],
  cpuHistory: CpuHistoryPoint[] = [],
  capabilities: CapabilityProfile = UNPROBED_CAPABILITIES,
  sessions: SessionsSnapshot = NO_SESSIONS,
) {
  mockApiFetch.mockImplementation((path: string) => {
    if (path === "/api/databases") return Promise.resolve([DB]);
    if (path.startsWith("/api/databases/db-1/waits/history")) return Promise.resolve(points);
    if (path.startsWith("/api/databases/db-1/cpu/history")) return Promise.resolve(cpuHistory);
    if (path === "/api/databases/db-1/capabilities") return Promise.resolve(capabilities);
    if (path === "/api/databases/db-1/sessions") return Promise.resolve(sessions);
    return Promise.reject(new Error(`unexpected fetch: ${path}`));
  });
}

describe("groupWaitClasses", () => {
  it("keeps each core wait class as its own series", () => {
    const { series } = groupWaitClasses([
      {
        sampled_at: "2026-09-01T12:00:00Z",
        elapsed_seconds: 60,
        wait_classes: [
          { wait_class: "User I/O", centisec_waited_per_sec: 10 },
          { wait_class: "Application", centisec_waited_per_sec: 5 },
        ],
      },
    ]);

    expect(series.map((s) => s.key)).toEqual(["Application", "User I/O"]);
  });

  it("folds non-core classes (including Oracle's own Other) into one combined series", () => {
    const { chartData, series } = groupWaitClasses([
      {
        sampled_at: "2026-09-01T12:00:00Z",
        elapsed_seconds: 60,
        wait_classes: [
          { wait_class: "User I/O", centisec_waited_per_sec: 10 },
          { wait_class: "Administrative", centisec_waited_per_sec: 2 },
          { wait_class: "Scheduler", centisec_waited_per_sec: 1 },
          { wait_class: "Other", centisec_waited_per_sec: 3 },
        ],
      },
    ]);

    expect(series.map((s) => s.key)).toEqual(["Other classes", "User I/O"]);
    expect(chartData[0]["Other classes"]).toBe(6); // 2 + 1 + 3, summed
    expect(chartData[0]["Administrative"]).toBeUndefined(); // not its own series anymore
  });

  it("never produces more than 8 series, even with all 10 real Oracle wait classes present", () => {
    const allTenClasses = [
      "Administrative",
      "Application",
      "Commit",
      "Concurrency",
      "Configuration",
      "Network",
      "Other",
      "Scheduler",
      "System I/O",
      "User I/O",
    ];
    const { series } = groupWaitClasses([
      {
        sampled_at: "2026-09-01T12:00:00Z",
        elapsed_seconds: 60,
        wait_classes: allTenClasses.map((wait_class) => ({ wait_class, centisec_waited_per_sec: 1 })),
      },
    ]);

    expect(series.length).toBeLessThanOrEqual(8);
  });

  it("omits the combined series entirely when no non-core class has a real value", () => {
    const { series } = groupWaitClasses([
      {
        sampled_at: "2026-09-01T12:00:00Z",
        elapsed_seconds: 60,
        wait_classes: [{ wait_class: "User I/O", centisec_waited_per_sec: 10 }],
      },
    ]);

    expect(series.map((s) => s.key)).not.toContain("Other classes");
  });
});

describe("addCpuLayer", () => {
  const waitSeries = [{ key: "User I/O", label: "User I/O" }];

  it("scales every value (wait classes and CPU alike) from centiseconds/sec to average active sessions", () => {
    const waitChartData = [{ time: "t0", __rawTime: "2026-09-01T12:00:00Z", "User I/O": 50 }];
    const cpuPoints: CpuHistoryPoint[] = [
      { sampled_at: "2026-09-01T12:00:00Z", elapsed_seconds: 15, cpu_centisec_used_per_sec: 20 },
    ];

    const { chartData, series } = addCpuLayer(waitChartData, waitSeries, cpuPoints);

    expect(chartData[0]["User I/O"]).toBe(0.5); // 50 / 100
    expect(chartData[0]["CPU"]).toBe(0.2); // 20 / 100
    expect(series.map((s) => s.key)).toEqual(["CPU", "User I/O"]); // CPU first - Oracle's own stack order
  });

  it("matches CPU points to the nearest wait round within tolerance, not by array index", () => {
    const waitChartData = [{ time: "t0", __rawTime: "2026-09-01T12:00:03Z", "User I/O": 10 }]; // 3s off from the CPU point
    const cpuPoints: CpuHistoryPoint[] = [
      { sampled_at: "2026-09-01T12:00:00Z", elapsed_seconds: 15, cpu_centisec_used_per_sec: 100 },
    ];

    const { chartData } = addCpuLayer(waitChartData, waitSeries, cpuPoints);

    expect(chartData[0]["CPU"]).toBe(1); // 100 / 100 - still matched despite the small gap
  });

  it("leaves a round unmerged (no CPU key) when no CPU point is within tolerance", () => {
    const waitChartData = [{ time: "t0", __rawTime: "2026-09-01T12:00:00Z", "User I/O": 10 }];
    const cpuPoints: CpuHistoryPoint[] = [
      { sampled_at: "2026-09-01T13:00:00Z", elapsed_seconds: 15, cpu_centisec_used_per_sec: 100 }, // an hour away
    ];

    const { chartData, series } = addCpuLayer(waitChartData, waitSeries, cpuPoints);

    expect(chartData[0]["CPU"]).toBeUndefined();
    expect(series.map((s) => s.key)).not.toContain("CPU"); // never claim a series with no real data
  });

  it("returns the wait series unchanged when there's no CPU data at all", () => {
    const waitChartData = [{ time: "t0", __rawTime: "2026-09-01T12:00:00Z", "User I/O": 10 }];

    const { series } = addCpuLayer(waitChartData, waitSeries, []);

    expect(series).toEqual(waitSeries);
  });
});

describe("currentWaitProfile", () => {
  const series = [{ key: "System I/O", label: "System I/O" }, { key: "User I/O", label: "User I/O" }];

  it("returns null when there's no data at all", () => {
    expect(currentWaitProfile([], series)).toBeNull();
  });

  it("sums the latest point's values and names the biggest contributor", () => {
    const chartData = [
      { time: "t0", "System I/O": 5, "User I/O": 1 },
      { time: "t1", "System I/O": 30, "User I/O": 12 }, // the latest point is the one that counts
    ];

    expect(currentWaitProfile(chartData, series)).toEqual({ total: 42, topClass: "System I/O", topValue: 30 });
  });

  it("reports no dominant class (not an arbitrary pick) when everything reads zero", () => {
    const chartData = [{ time: "t0", "System I/O": 0, "User I/O": 0 }];

    expect(currentWaitProfile(chartData, series)).toEqual({ total: 0, topClass: null, topValue: 0 });
  });

  it("treats a missing key as zero rather than throwing", () => {
    const chartData = [{ time: "t0", "System I/O": 7 }]; // "User I/O" absent this round

    expect(currentWaitProfile(chartData, series)).toEqual({ total: 7, topClass: "System I/O", topValue: 7 });
  });
});

describe("WaitAnalysisPage", () => {
  it("shows an honest empty state when rates cannot be computed yet", async () => {
    mockFetch([]);
    renderPage();

    expect(await screen.findByText("Not enough telemetry in this window")).toBeInTheDocument();
  });

  it("renders a wait-class trend chart instead of an empty state", async () => {
    mockFetch([
      {
        sampled_at: "2026-09-01T12:00:00Z",
        elapsed_seconds: 60,
        wait_classes: [
          { wait_class: "User I/O", centisec_waited_per_sec: 12.5 },
          { wait_class: "Application", centisec_waited_per_sec: 3.2 },
        ],
      },
      {
        sampled_at: "2026-09-01T12:01:00Z",
        elapsed_seconds: 60,
        wait_classes: [
          { wait_class: "User I/O", centisec_waited_per_sec: 10.1 },
          { wait_class: "Application", centisec_waited_per_sec: 2.8 },
        ],
      },
    ]);
    renderPage();

    // recharts' ResponsiveContainer measures 0x0 in jsdom (no ResizeObserver), so the
    // axis/area internals don't render - assert the surrounding chart chrome instead.
    expect(await screen.findByText(/Average active sessions, by CPU and wait class/)).toBeInTheDocument();
    expect(screen.queryByText("Not enough telemetry in this window")).not.toBeInTheDocument();

    // WaitDetailPanel's summary reflects the *latest* round, scaled to average active
    // sessions: User I/O 10.1/100 = 0.1, Application 2.8/100 = 0.03, total 0.13.
    expect(await screen.findByText("Total 0.13 AAS · dominated by User I/O (0.10)")).toBeInTheDocument();
  });

  it("merges a real /cpu/history fetch into the chart, included in the stat tile", async () => {
    mockFetch(
      [
        {
          sampled_at: "2026-09-01T12:00:00Z",
          elapsed_seconds: 60,
          wait_classes: [{ wait_class: "User I/O", centisec_waited_per_sec: 10 }],
        },
        {
          sampled_at: "2026-09-01T12:01:00Z",
          elapsed_seconds: 60,
          wait_classes: [{ wait_class: "User I/O", centisec_waited_per_sec: 10 }],
        },
      ],
      [
        { sampled_at: "2026-09-01T12:00:00Z", elapsed_seconds: 60, cpu_centisec_used_per_sec: 10 },
        { sampled_at: "2026-09-01T12:01:00Z", elapsed_seconds: 60, cpu_centisec_used_per_sec: 5000 },
      ],
    );
    renderPage();

    // CPU (5000/100 = 50.0) dominates over User I/O (10/100 = 0.1) - proves the real
    // /cpu/history fetch merges into the chart end to end, not just the pure function alone.
    expect(await screen.findByText("Total 50.10 AAS · dominated by CPU (50.00)")).toBeInTheDocument();
  });

  it("fetches the database's real CPU core count to drive the chart's reference line", async () => {
    mockFetch(
      [
        {
          sampled_at: "2026-09-01T12:00:00Z",
          elapsed_seconds: 60,
          wait_classes: [{ wait_class: "User I/O", centisec_waited_per_sec: 10 }],
        },
      ],
      [],
      { ...UNPROBED_CAPABILITIES, last_probed_at: "2026-01-01T00:00:00Z", cpu_count: 8 },
    );
    renderPage();

    await screen.findByText(/Average active sessions, by CPU and wait class/);
    // Same shared cache key CapabilityPanel already uses - this proves the real fetch (and
    // thus a real cpu_count) reaches this page, not just an inline computation never wired up.
    expect(mockApiFetch).toHaveBeenCalledWith("/api/databases/db-1/capabilities");
  });

  it("requests the selected preset range", async () => {
    mockFetch([]);
    renderPage();

    await screen.findByText("Not enough telemetry in this window");
    expect(mockApiFetch).toHaveBeenCalledWith("/api/databases/db-1/waits/history?hours=1");
  });
});
