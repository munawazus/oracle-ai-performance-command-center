import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { alertContextLine, CapacityPage } from "./CapacityPage";
import type {
  CapacityAlert,
  CapacityOverviewResponse,
  CapacityThresholds,
  MonitoredDatabase,
  TablespaceDetailResponse,
} from "../types/api";

const mockApiFetch = vi.fn();
const mockHasPermission = vi.fn().mockReturnValue(true);

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
      <CapacityPage />
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

const ALERT: CapacityAlert = {
  id: "alert-1",
  tablespace_name: "SYSTEM",
  alert_kind: "PCT_USED",
  severity: "CRITICAL",
  status: "OPEN",
  first_detected_at: "2026-09-01T12:00:00Z",
  last_detected_at: "2026-09-01T12:00:00Z",
  resolved_at: null,
  pct_used: 96.0,
  pct_basis: "allocated",
  projected_days_to_full: null,
  current_used_bytes: 960,
  current_allocated_bytes: 1000,
  current_max_bytes: 2000,
  detection_note: "Tablespace SYSTEM is 96.0% used (960 of 1000 allocated bytes).",
};

const SYSTEM_ROW = {
  tablespace_name: "SYSTEM",
  contents: "PERMANENT" as const,
  status: "ONLINE",
  allocated_bytes: 1000,
  used_bytes: 960,
  max_bytes: 2000,
  file_count: 1,
  pct_used: 96.0,
  pct_of_max: 48.0,
  growth_available: false,
  growth_reason: "fewer than 3 samples in the last 7 days - no trend can be computed yet",
  growth_bytes_per_day: null,
  projected_days_to_full: null,
  projection_reason: null,
  open_alert_kind: "PCT_USED" as const,
  open_alert_severity: "CRITICAL" as const,
};

const NO_OVERRIDES: CapacityThresholds = {
  warn_pct_used: null,
  critical_pct_used: null,
  warn_days_to_full: null,
  critical_days_to_full: null,
  default_warn_pct_used: 85,
  default_critical_pct_used: 95,
  default_warn_days_to_full: 30,
  default_critical_days_to_full: 7,
};

function mockFetch({
  overview,
  alerts = [],
  detail,
  thresholds = NO_OVERRIDES,
}: {
  overview: CapacityOverviewResponse;
  alerts?: CapacityAlert[];
  detail?: TablespaceDetailResponse;
  thresholds?: CapacityThresholds;
}) {
  mockApiFetch.mockImplementation((path: string, options?: { method?: string; body?: unknown }) => {
    if (path === "/api/databases") return Promise.resolve([DB]);
    if (path === "/api/databases/db-1/capacity") return Promise.resolve(overview);
    if (path.startsWith("/api/databases/db-1/capacity-alerts")) return Promise.resolve(alerts);
    if (path === "/api/databases/db-1/capacity/SYSTEM" && detail) return Promise.resolve(detail);
    if (path === "/api/databases/db-1/capacity-thresholds") {
      if (options?.method === "PUT") {
        const body = options.body as Record<string, number | null>;
        return Promise.resolve({ ...thresholds, ...body });
      }
      return Promise.resolve(thresholds);
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`));
  });
}

describe("alertContextLine", () => {
  // Date.now() only - not vi.useFakeTimers(), which would also stall userEvent's own internal
  // timers in the interaction tests below.
  beforeEach(() => {
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-09-01T14:00:00Z").getTime());
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("names the basis for a PCT_USED alert measured against allocated size", () => {
    expect(alertContextLine(ALERT)).toBe("2h ago · 96.0% used of allocated at detection");
  });

  it("names the basis for a PCT_USED alert measured against max size", () => {
    expect(alertContextLine({ ...ALERT, pct_basis: "max size" })).toBe("2h ago · 96.0% used of max size at detection");
  });

  it("omits the basis for an alert that predates it being tracked, rather than guessing", () => {
    expect(alertContextLine({ ...ALERT, pct_basis: null })).toBe("2h ago · 96.0% used at detection");
  });

  it("shows the detection-time projection for a PROJECTED_FULL alert", () => {
    expect(
      alertContextLine({ ...ALERT, alert_kind: "PROJECTED_FULL", projected_days_to_full: 5.2 }),
    ).toBe("2h ago · 5 day(s) at detection");
  });
});

describe("CapacityPage", () => {
  it("shows an honest unavailable state before any capacity collection round", async () => {
    mockFetch({ overview: { available: false, reason: "no capacity telemetry collected yet", sampled_at: null, rows: [] } });
    renderPage();

    expect(await screen.findByText("Not enough capacity telemetry yet")).toBeInTheDocument();
  });

  it("renders tablespace usage with an alert badge and a summary banner", async () => {
    mockFetch({
      overview: { available: true, reason: null, sampled_at: "2026-09-01T12:00:00Z", rows: [SYSTEM_ROW] },
      alerts: [ALERT],
    });
    renderPage();

    expect(await screen.findByText("SYSTEM")).toBeInTheDocument();
    expect(screen.getByText("PCT_USED")).toBeInTheDocument();
    expect(screen.getByText(/1 open capacity alert\(s\)/)).toBeInTheDocument();

    // Real-world case this fixture models: 96% of the tablespace's *current* 1000-byte
    // allocation, but only 48% of its real 2000-byte autoextend max size - both numbers are
    // genuinely different facts, so both must be visible. % of max size is primary (it's what
    // actually drives severity, matching capacity_alerts.py's own preference) - a DBA reading
    // only "96.0% of allocated" would have no way to tell there's 4x the real headroom left.
    // No allocated-bytes figure repeated here - it's not restated anywhere in the row.
    expect(screen.getByText("48.0% used of max size")).toBeInTheDocument();
    expect(screen.getByText("96.0% used of allocated")).toBeInTheDocument();
  });

  it("falls back to % of allocated as primary when there's no known max size", async () => {
    mockFetch({
      overview: {
        available: true,
        reason: null,
        sampled_at: "2026-09-01T12:00:00Z",
        rows: [{ ...SYSTEM_ROW, max_bytes: null, pct_of_max: null }],
      },
    });
    renderPage();

    expect(await screen.findByText("96.0% used of allocated")).toBeInTheDocument();
    // Two honest instances: the % used cell's own secondary caption, and the separate Max
    // size column - both real, both correctly blank rather than showing a stale number.
    expect(screen.getAllByText("no known max size").length).toBe(2);
  });

  it("colors the badge from a real per-database threshold override, not a hardcoded default", async () => {
    // pct_of_max is 20% here - "good" under the fleet-wide default (warn at 85%), but this
    // database has its own warn_pct_used override of 15%, so it must read "warn" instead.
    mockFetch({
      overview: {
        available: true,
        reason: null,
        sampled_at: "2026-09-01T12:00:00Z",
        rows: [{ ...SYSTEM_ROW, pct_used: 40.0, pct_of_max: 20.0 }],
      },
      thresholds: { ...NO_OVERRIDES, warn_pct_used: 15 },
    });
    renderPage();

    const badge = await screen.findByText("20.0% used of max size");
    expect(badge.className).toContain("warn");
  });

  it("shows when and at what value the open alert actually fired, not just a bare badge", async () => {
    const dateNowSpy = vi.spyOn(Date, "now").mockReturnValue(new Date("2026-09-01T14:00:00Z").getTime());

    mockFetch({
      overview: { available: true, reason: null, sampled_at: "2026-09-01T12:00:00Z", rows: [SYSTEM_ROW] },
      alerts: [ALERT],
    });
    renderPage();

    expect(await screen.findByText("PCT_USED")).toBeInTheDocument();
    expect(screen.getByText("2h ago · 96.0% used of allocated at detection")).toBeInTheDocument();

    dateNowSpy.mockRestore();
  });

  it("opens the tablespace detail drawer on row click", async () => {
    const dateNowSpy = vi.spyOn(Date, "now").mockReturnValue(new Date("2026-09-01T14:00:00Z").getTime());

    const detail: TablespaceDetailResponse = {
      available: true,
      reason: null,
      tablespace_name: "SYSTEM",
      contents: "PERMANENT",
      status: "ONLINE",
      usage: SYSTEM_ROW,
      history: [
        { sampled_at: "2026-09-01T11:00:00Z", allocated_bytes: 1000, used_bytes: 900, max_bytes: 2000 },
        { sampled_at: "2026-09-01T12:00:00Z", allocated_bytes: 1000, used_bytes: 960, max_bytes: 2000 },
      ],
      open_alerts: [ALERT],
    };
    mockFetch({
      overview: { available: true, reason: null, sampled_at: "2026-09-01T12:00:00Z", rows: [SYSTEM_ROW] },
      detail,
    });
    renderPage();

    await userEvent.click(await screen.findByText("SYSTEM"));

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("Tablespace SYSTEM is 96.0% used (960 of 1000 allocated bytes).")).toBeInTheDocument();
    // Scoped to <li> specifically - the drawer's own "% used" rows below now also contain the
    // substring "used of", so an unscoped match would double-count them.
    expect(within(drawer).getAllByText(/used of/, { selector: "li" }).length).toBe(2);
    expect(within(drawer).getByText("48.0% used of max size")).toBeInTheDocument();
    // The drawer's own "% of allocated" label already says "allocated" - the value here is
    // just the number, and the allocated-bytes figure isn't restated anywhere in the drawer.
    expect(within(drawer).getByText("96.0% used")).toBeInTheDocument();
    expect(within(drawer).getByText("· last confirmed 2h ago")).toBeInTheDocument();

    dateNowSpy.mockRestore();
  });

  it("shows the global defaults, clearly labeled, before any override is set", async () => {
    mockFetch({ overview: { available: true, reason: null, sampled_at: "2026-09-01T12:00:00Z", rows: [SYSTEM_ROW] } });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Alert thresholds" }));

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("Currently applied: 85% (default)")).toBeInTheDocument();
    expect(within(drawer).getByPlaceholderText("Default: 85%")).toBeInTheDocument();
  });

  it("lets an editor save a new override", async () => {
    mockHasPermission.mockReturnValue(true);
    mockFetch({ overview: { available: true, reason: null, sampled_at: "2026-09-01T12:00:00Z", rows: [SYSTEM_ROW] } });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Alert thresholds" }));
    const drawer = await screen.findByRole("dialog");
    const warnPctInput = within(drawer).getByLabelText("Warn at % used");
    await userEvent.clear(warnPctInput);
    await userEvent.type(warnPctInput, "40");
    await userEvent.click(within(drawer).getByRole("button", { name: "Save" }));

    await screen.findByText("Currently applied: 40% (override)");
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/databases/db-1/capacity-thresholds",
      expect.objectContaining({
        method: "PUT",
        body: { warn_pct_used: 40, critical_pct_used: null, warn_days_to_full: null, critical_days_to_full: null },
      }),
    );
  });

  it("hides save controls and disables inputs for a user without edit permission", async () => {
    mockHasPermission.mockReturnValue(false);
    mockFetch({ overview: { available: true, reason: null, sampled_at: "2026-09-01T12:00:00Z", rows: [SYSTEM_ROW] } });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Alert thresholds" }));

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText(/don't have permission to edit/)).toBeInTheDocument();
    expect(within(drawer).queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(within(drawer).queryByRole("button", { name: "Reset to defaults" })).not.toBeInTheDocument();
    expect(within(drawer).getByLabelText("Warn at % used")).toBeDisabled();
  });

  it("resets every field back to the default in one action", async () => {
    mockHasPermission.mockReturnValue(true);
    mockFetch({
      overview: { available: true, reason: null, sampled_at: "2026-09-01T12:00:00Z", rows: [SYSTEM_ROW] },
      thresholds: { ...NO_OVERRIDES, warn_pct_used: 40, critical_days_to_full: 2 },
    });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Alert thresholds" }));
    const drawer = await screen.findByRole("dialog");
    await screen.findByText("Currently applied: 40% (override)");
    await userEvent.click(within(drawer).getByRole("button", { name: "Reset to defaults" }));

    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/databases/db-1/capacity-thresholds",
      expect.objectContaining({
        method: "PUT",
        body: { warn_pct_used: null, critical_pct_used: null, warn_days_to_full: null, critical_days_to_full: null },
      }),
    );
    await screen.findByText("Currently applied: 85% (default)");
  });
});
