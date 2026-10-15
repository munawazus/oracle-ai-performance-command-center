import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { computeCostTiers, regressionAlertTone, SqlIntelligencePage, worstRegressionSeverity } from "./SqlIntelligencePage";
import type {
  CapabilityProfile,
  MonitoredDatabase,
  SessionsSnapshot,
  SqlDetail,
  SqlPlanLine,
  SqlPlanResponse,
  SqlRegression,
  StatspackSummaryResponse,
  TopSqlResponse,
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
      <SqlIntelligencePage />
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

const REGRESSION: SqlRegression = {
  id: "reg-1",
  sql_id: "abc123sqlid",
  sql_text: "SELECT * FROM orders",
  regression_kind: "ELAPSED_PER_EXEC",
  severity: "HIGH",
  status: "OPEN",
  first_detected_at: "2026-09-01T12:00:00Z",
  last_detected_at: "2026-09-01T12:05:00Z",
  resolved_at: null,
  baseline_elapsed_per_exec_us: 1000,
  current_elapsed_per_exec_us: 15000,
  ratio: 15.0,
  baseline_executions: 10,
  current_executions: 10,
  baseline_plan_hash_value: 111,
  current_plan_hash_value: 111,
  detection_note: "Elapsed time per execution went from 1000us to 15000us (15.0x).",
};

const TOP_SQL_ROW = {
  sql_id: "abc123sqlid",
  plan_hash_value: 111,
  sql_text: "SELECT * FROM orders",
  sql_text_truncated: false,
  parsing_schema_name: "APPUSER",
  child_cursor_count: 1,
  executions_delta: 10,
  elapsed_time_us_delta: 150_000,
  cpu_time_us_delta: 120_000,
  buffer_gets_delta: 400,
  disk_reads_delta: 4,
  rows_processed_delta: 10,
  elapsed_per_exec_us: 15_000,
  elapsed_time_us_per_sec: 500,
  plan_count: 1,
  open_regression_kind: "ELAPSED_PER_EXEC" as const,
  open_regression_severity: "HIGH" as const,
};

const CAPABILITIES_WITHOUT_STATSPACK: CapabilityProfile = {
  detected_version: "19.0.0.0.0",
  detected_edition: "SE2",
  is_rac: false,
  statspack_available: false,
  diagnostics_tuning_pack_licensed_confirmed: false,
  privilege_report: {},
  last_probed_at: "2026-09-01T00:00:00Z",
  cpu_count: 4,
};

const NO_SESSIONS: SessionsSnapshot = { available: true, reason: null, sampled_at: "2026-09-01T12:05:00Z", session_count: 0, sessions: [] };

function mockFetch({
  topSql,
  regressions = [],
  sqlDetail,
  plan,
  capabilities = CAPABILITIES_WITHOUT_STATSPACK,
  statspack,
  sessions = NO_SESSIONS,
}: {
  topSql: TopSqlResponse;
  regressions?: SqlRegression[];
  sqlDetail?: SqlDetail;
  plan?: SqlPlanResponse;
  capabilities?: CapabilityProfile;
  statspack?: StatspackSummaryResponse;
  sessions?: SessionsSnapshot;
}) {
  mockApiFetch.mockImplementation((path: string) => {
    if (path === "/api/databases") return Promise.resolve([DB]);
    if (path.startsWith("/api/databases/db-1/top-sql")) return Promise.resolve(topSql);
    if (path.startsWith("/api/databases/db-1/sql-regressions")) return Promise.resolve(regressions);
    if (path === "/api/databases/db-1/sql/abc123sqlid" && sqlDetail) return Promise.resolve(sqlDetail);
    if (path === "/api/databases/db-1/sql/abc123sqlid/plan/111" && plan) return Promise.resolve(plan);
    if (path === "/api/databases/db-1/capabilities") return Promise.resolve(capabilities);
    if (path === "/api/databases/db-1/statspack" && statspack) return Promise.resolve(statspack);
    if (path === "/api/databases/db-1/sessions") return Promise.resolve(sessions);
    return Promise.reject(new Error(`unexpected fetch: ${path}`));
  });
}

describe("worstRegressionSeverity", () => {
  it("returns null for an empty list", () => {
    expect(worstRegressionSeverity([])).toBeNull();
  });

  it("picks the worst severity present, regardless of list order", () => {
    const low = { ...REGRESSION, id: "reg-low", severity: "LOW" as const };
    const critical = { ...REGRESSION, id: "reg-crit", severity: "CRITICAL" as const };
    const medium = { ...REGRESSION, id: "reg-med", severity: "MEDIUM" as const };
    expect(worstRegressionSeverity([low, critical, medium])).toBe("CRITICAL");
  });
});

describe("regressionAlertTone", () => {
  it("maps HIGH and CRITICAL to the alert's crit tone", () => {
    expect(regressionAlertTone("HIGH")).toBe("crit");
    expect(regressionAlertTone("CRITICAL")).toBe("crit");
  });

  it("maps MEDIUM to warn", () => {
    expect(regressionAlertTone("MEDIUM")).toBe("warn");
  });

  it("maps LOW and INFO to info - a plan change that got faster is still a 'regression' at LOW severity, never critical", () => {
    expect(regressionAlertTone("LOW")).toBe("info");
    expect(regressionAlertTone("INFO")).toBe("info");
  });
});

describe("SqlIntelligencePage", () => {
  it("shows an honest unavailable state before two SQL collection rounds", async () => {
    mockFetch({
      topSql: {
        available: false,
        reason: "fewer than two SQL collection rounds - no per-interval delta can be computed yet",
        sampled_at: null,
        previous_sampled_at: null,
        elapsed_seconds: null,
        sort_by: "elapsed_time",
        rows: [],
      },
    });
    renderPage();

    expect(await screen.findByText("Not enough SQL telemetry yet")).toBeInTheDocument();
  });

  it("renders top SQL with an open-regression badge and a summary banner", async () => {
    mockFetch({
      topSql: {
        available: true,
        reason: null,
        sampled_at: "2026-09-01T12:05:00Z",
        previous_sampled_at: "2026-09-01T12:00:00Z",
        elapsed_seconds: 300,
        sort_by: "elapsed_time",
        rows: [TOP_SQL_ROW],
      },
      regressions: [REGRESSION],
    });
    renderPage();

    expect(await screen.findByText("abc123sqlid")).toBeInTheDocument();
    expect(screen.getByText("ELAPSED_PER_EXEC")).toBeInTheDocument();
    const banner = await screen.findByText(/1 open SQL regression\(s\) detected/);
    expect(banner.closest('[role="alert"]')?.className).toMatch(/status-crit/);
  });

  it("shows CPU time and disk reads deltas, matching the sort options that already rank by them", async () => {
    mockFetch({
      topSql: {
        available: true,
        reason: null,
        sampled_at: "2026-09-01T12:05:00Z",
        previous_sampled_at: "2026-09-01T12:00:00Z",
        elapsed_seconds: 300,
        sort_by: "elapsed_time",
        rows: [TOP_SQL_ROW], // cpu_time_us_delta: 120_000, disk_reads_delta: 4
      },
    });
    renderPage();

    expect(await screen.findByText("abc123sqlid")).toBeInTheDocument();
    expect(screen.getByText("CPU time Δ")).toBeInTheDocument();
    expect(screen.getByText("120.00ms")).toBeInTheDocument();
    expect(screen.getByText("Disk reads Δ")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("does not show the regression banner as critical when every open regression is only LOW severity", async () => {
    const lowSeverityRegression: SqlRegression = { ...REGRESSION, severity: "LOW" };
    mockFetch({
      topSql: {
        available: true,
        reason: null,
        sampled_at: "2026-09-01T12:05:00Z",
        previous_sampled_at: "2026-09-01T12:00:00Z",
        elapsed_seconds: 300,
        sort_by: "elapsed_time",
        rows: [TOP_SQL_ROW],
      },
      regressions: [lowSeverityRegression],
    });
    renderPage();

    const banner = await screen.findByText(/1 open SQL regression\(s\) detected/);
    const alertEl = banner.closest('[role="alert"]');
    expect(alertEl?.className).toMatch(/status-info/);
    expect(alertEl?.className).not.toMatch(/status-crit/);
  });

  it("opens the SQL detail drawer and renders a plan tree on row click", async () => {
    const sqlDetail: SqlDetail = {
      available: true,
      reason: null,
      sql_id: "abc123sqlid",
      sql_text: "SELECT * FROM orders",
      sql_text_truncated: false,
      parsing_schema_name: "APPUSER",
      first_seen_at: "2026-09-01T11:00:00Z",
      last_seen_at: "2026-09-01T12:05:00Z",
      plans: [
        {
          plan_hash_value: 111,
          first_seen_at: "2026-09-01T11:00:00Z",
          last_seen_at: "2026-09-01T12:05:00Z",
          plan_line_count: 2,
          is_current: true,
        },
      ],
      recent_sessions: [],
      open_regressions: [REGRESSION],
    };
    const plan: SqlPlanResponse = {
      available: true,
      reason: null,
      sql_id: "abc123sqlid",
      plan_hash_value: 111,
      first_seen_at: "2026-09-01T11:00:00Z",
      last_seen_at: "2026-09-01T12:05:00Z",
      plan_lines: [
        {
          id: 0,
          parent_id: null,
          depth: 0,
          position: 1,
          operation: "SELECT STATEMENT",
          options: null,
          object_owner: null,
          object_name: null,
          object_type: null,
          cost: 5,
          cardinality: null,
          bytes: null,
        },
        {
          id: 1,
          parent_id: 0,
          depth: 1,
          position: 1,
          operation: "TABLE ACCESS",
          options: "FULL",
          object_owner: "APPUSER",
          object_name: "ORDERS",
          object_type: "TABLE",
          cost: 4,
          cardinality: 100,
          bytes: 8000,
        },
      ],
    };
    mockFetch({
      topSql: {
        available: true,
        reason: null,
        sampled_at: "2026-09-01T12:05:00Z",
        previous_sampled_at: "2026-09-01T12:00:00Z",
        elapsed_seconds: 300,
        sort_by: "elapsed_time",
        rows: [TOP_SQL_ROW],
      },
      sqlDetail,
      plan,
    });
    renderPage();

    await userEvent.click(await screen.findByText("abc123sqlid"));

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("SELECT * FROM orders")).toBeInTheDocument();
    expect(
      within(drawer).getByText("Elapsed time per execution went from 1000us to 15000us (15.0x)."),
    ).toBeInTheDocument();

    await userEvent.click(within(drawer).getByText("111"));

    expect(await within(drawer).findByText(/TABLE ACCESS FULL of ORDERS/)).toBeInTheDocument();
  });

  it("renders no Statspack panel for a database without it installed", async () => {
    mockFetch({
      topSql: {
        available: false, reason: "fewer than two SQL collection rounds - no per-interval delta can be computed yet",
        sampled_at: null, previous_sampled_at: null, elapsed_seconds: null, sort_by: "elapsed_time", rows: [],
      },
    });
    renderPage();

    await screen.findByText("Not enough SQL telemetry yet");
    expect(screen.queryByText("Statspack")).not.toBeInTheDocument();
  });

  it("renders the Statspack panel with real wait classes and top SQL when installed", async () => {
    mockFetch({
      topSql: {
        available: false, reason: "fewer than two SQL collection rounds - no per-interval delta can be computed yet",
        sampled_at: null, previous_sampled_at: null, elapsed_seconds: null, sort_by: "elapsed_time", rows: [],
      },
      capabilities: { ...CAPABILITIES_WITHOUT_STATSPACK, statspack_available: true },
      statspack: {
        available: true,
        reason: null,
        from_snapshot: { snap_id: 101, snap_time: "2026-09-01T10:00:00Z" },
        to_snapshot: { snap_id: 102, snap_time: "2026-09-01T11:00:00Z" },
        elapsed_hours: 1.0,
        wait_classes: [{ wait_class: "User I/O", centisec_waited_per_hour: 400 }],
        top_sql: [
          {
            sql_id: "abc123sqlid",
            plan_hash_value: 111,
            sql_text: "SELECT * FROM orders",
            executions_delta: 10,
            elapsed_time_us_delta: 150_000,
            elapsed_per_exec_us: 15_000,
          },
        ],
      },
    });
    renderPage();

    expect(await screen.findByText("Statspack")).toBeInTheDocument();
    expect(screen.getByText("User I/O")).toBeInTheDocument();
    expect(screen.getByText("SELECT * FROM orders")).toBeInTheDocument();
  });

  const EMPTY_TOP_SQL: TopSqlResponse = {
    available: false,
    reason: "fewer than two SQL collection rounds - no per-interval delta can be computed yet",
    sampled_at: null,
    previous_sampled_at: null,
    elapsed_seconds: null,
    sort_by: "elapsed_time",
    rows: [],
  };

  const SESSION_A = {
    sid: 101,
    serial_num: 1,
    username: "APPUSER",
    sql_id: "abc123sqlid",
    event: "db file sequential read",
    wait_class: "User I/O",
    status: "ACTIVE",
    blocking_session: null,
    machine: "app01",
    program: "JDBC Thin Client",
    module: "OrderService",
    action: "placeOrder",
    state: "WAITING",
    last_call_et: 5,
  };
  const SESSION_B = {
    sid: 102,
    serial_num: 2,
    username: "REPORTING",
    sql_id: null,
    event: null,
    wait_class: "Concurrency",
    status: "INACTIVE",
    blocking_session: null,
    machine: "report02",
    program: "sqlplus",
    module: null,
    action: null,
    state: null,
    last_call_et: null,
  };

  it("renders real sessions in the Sessions panel", async () => {
    mockFetch({
      topSql: EMPTY_TOP_SQL,
      sessions: { available: true, reason: null, sampled_at: "2026-09-01T12:05:00Z", session_count: 2, sessions: [SESSION_A, SESSION_B] },
    });
    renderPage();

    expect(await screen.findByText("Sessions")).toBeInTheDocument();
    expect(await screen.findByText("APPUSER")).toBeInTheDocument();
    expect(screen.getByText("REPORTING")).toBeInTheDocument();
    expect(screen.queryByText(/Showing \d+ of \d+/)).not.toBeInTheDocument();
  });

  it("shows a 'Showing N of M' hint instead of silently dropping sessions past the display cap", async () => {
    const manySessions = Array.from({ length: 60 }, (_, i) => ({ ...SESSION_A, sid: 200 + i }));
    mockFetch({
      topSql: EMPTY_TOP_SQL,
      sessions: { available: true, reason: null, sampled_at: "2026-09-01T12:05:00Z", session_count: 60, sessions: manySessions },
    });
    renderPage();

    expect(await screen.findByText("Sessions")).toBeInTheDocument();
    expect(await screen.findByText("Showing 50 of 60")).toBeInTheDocument();
  });

  it("filters the Sessions panel to a chip after clicking a wait-class value", async () => {
    mockFetch({
      topSql: EMPTY_TOP_SQL,
      sessions: { available: true, reason: null, sampled_at: "2026-09-01T12:05:00Z", session_count: 2, sessions: [SESSION_A, SESSION_B] },
    });
    renderPage();

    await screen.findByText("APPUSER");
    await userEvent.click(screen.getByText("User I/O"));

    expect(await screen.findByText("Wait class: User I/O")).toBeInTheDocument();
    expect(screen.getByText("APPUSER")).toBeInTheDocument();
    expect(screen.queryByText("REPORTING")).not.toBeInTheDocument();
  });

  it("opens the SQL detail drawer when a session's SQL ID is clicked", async () => {
    const sqlDetail: SqlDetail = {
      available: true,
      reason: null,
      sql_id: "abc123sqlid",
      sql_text: "SELECT * FROM orders",
      sql_text_truncated: false,
      parsing_schema_name: "APPUSER",
      first_seen_at: "2026-09-01T11:00:00Z",
      last_seen_at: "2026-09-01T12:05:00Z",
      plans: [],
      recent_sessions: [],
      open_regressions: [],
    };
    mockFetch({
      topSql: EMPTY_TOP_SQL,
      sqlDetail,
      sessions: { available: true, reason: null, sampled_at: "2026-09-01T12:05:00Z", session_count: 1, sessions: [SESSION_A] },
    });
    renderPage();

    await userEvent.click(await screen.findByText("abc123sqlid"));

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("SELECT * FROM orders")).toBeInTheDocument();
  });
});

describe("computeCostTiers", () => {
  function line(id: number, cost: number | null): SqlPlanLine {
    return {
      id,
      parent_id: null,
      depth: 0,
      position: 1,
      operation: "OP",
      options: null,
      object_owner: null,
      object_name: null,
      object_type: null,
      cost,
      cardinality: null,
      bytes: null,
    };
  }

  it("marks the highest-cost line critical and the lowest untiered, relative to the plan's own lines", () => {
    const tiers = computeCostTiers([line(1, 10), line(2, 500), line(3, 50_000)]);
    expect(tiers.get(3)).toBe("critical");
    expect(tiers.get(1)).toBeNull();
  });

  it("never tiers a line with no recorded cost", () => {
    const tiers = computeCostTiers([line(1, null), line(2, 100)]);
    expect(tiers.get(1)).toBeNull();
  });

  it("tiers nothing when no line in the plan has a cost", () => {
    const tiers = computeCostTiers([line(1, null), line(2, null)]);
    expect(tiers.get(1)).toBeNull();
    expect(tiers.get(2)).toBeNull();
  });
});
