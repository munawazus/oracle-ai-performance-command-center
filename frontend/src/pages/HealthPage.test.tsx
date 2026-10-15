import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  editionMismatch,
  HealthPage,
  humanizeErrorCategory,
  isCapabilityStale,
  newestOpenIncident,
  summarizeFleetHealth,
  type HealthRow,
} from "./HealthPage";
import type { CapabilityProfile, CollectorStatus, Incident, MonitoredDatabase } from "../types/api";

const mockApiFetch = vi.fn();

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

beforeEach(() => {
  mockApiFetch.mockClear();
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <HealthPage />
      </QueryClientProvider>
    </MemoryRouter>,
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

const UNPROBED: CapabilityProfile = {
  detected_version: null,
  detected_edition: null,
  is_rac: null,
  statspack_available: null,
  diagnostics_tuning_pack_licensed_confirmed: false,
  privilege_report: null,
  last_probed_at: null,
  cpu_count: null,
};

const FAILING: CollectorStatus = {
  last_run_at: "2026-09-01T13:00:00Z",
  last_success_at: "2026-09-01T11:00:00Z",
  last_failure_at: "2026-09-01T11:02:00Z",
  consecutive_failures: 47,
  last_error_category: "unreachable",
  last_run_duration_ms: 5000,
};

describe("humanizeErrorCategory", () => {
  it("maps each real OracleConnectError category to a short label", () => {
    expect(humanizeErrorCategory("unreachable")).toBe("Unreachable");
    expect(humanizeErrorCategory("authentication_failed")).toBe("Authentication failing");
    expect(humanizeErrorCategory("unknown_error")).toBe("Connection failing");
  });

  it("falls back to a generic label for an unrecognized or null category, never guessing", () => {
    expect(humanizeErrorCategory("something_new")).toBe("Connection failing");
    expect(humanizeErrorCategory(null)).toBe("Unknown reason");
  });
});

function row(status: CollectorStatus, overrides: Partial<MonitoredDatabase> = {}): HealthRow {
  return { database: { ...DB, ...overrides }, status, capabilities: UNPROBED, incidents: [] };
}

const OPEN_INCIDENT: Incident = {
  id: "inc-1",
  monitored_database_id: "db-1",
  status: "OPEN",
  severity: "CRITICAL",
  title: "Database unreachable",
  opened_at: "2026-09-01T11:00:00Z",
  last_evidence_at: "2026-09-01T13:00:00Z",
  acknowledged_at: null,
  acknowledged_by_user_id: null,
  assigned_to_user_id: null,
  resolved_at: null,
  resolved_by_user_id: null,
  resolution_note: null,
};

describe("newestOpenIncident", () => {
  it("returns null when there are no incidents", () => {
    expect(newestOpenIncident([])).toBeNull();
  });

  it("ignores resolved incidents", () => {
    expect(newestOpenIncident([{ ...OPEN_INCIDENT, status: "RESOLVED" }])).toBeNull();
  });

  it("picks the open incident with the most recent evidence", () => {
    const older = { ...OPEN_INCIDENT, id: "inc-older", last_evidence_at: "2026-09-01T10:00:00Z" };
    const newer = { ...OPEN_INCIDENT, id: "inc-newer", last_evidence_at: "2026-09-01T12:00:00Z" };
    const resolved = { ...OPEN_INCIDENT, id: "inc-resolved", status: "RESOLVED" as const, last_evidence_at: "2026-09-01T14:00:00Z" };
    expect(newestOpenIncident([older, newer, resolved])?.id).toBe("inc-newer");
  });
});

describe("isCapabilityStale", () => {
  it("is false for a capability probed recently", () => {
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-09-01T00:00:00Z").getTime());
    expect(isCapabilityStale("2026-08-30T00:00:00Z")).toBe(false);
    vi.restoreAllMocks();
  });

  it("is true once a capability probe is more than 30 days old", () => {
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-09-01T00:00:00Z").getTime());
    expect(isCapabilityStale("2026-07-01T00:00:00Z")).toBe(true);
    vi.restoreAllMocks();
  });
});

describe("editionMismatch", () => {
  it("flags a real disagreement between the registered and detected edition", () => {
    expect(editionMismatch("SE2", "EE")).toBe(true);
    expect(editionMismatch("EE", "SE2")).toBe(true);
  });

  it("is false when both sides agree", () => {
    expect(editionMismatch("EE", "EE")).toBe(false);
  });

  it("is false when nothing has been probed yet", () => {
    expect(editionMismatch("SE2", null)).toBe(false);
  });

  it("is false when the probe itself couldn't tell (UNKNOWN), not a real disagreement", () => {
    expect(editionMismatch("SE2", "UNKNOWN")).toBe(false);
  });

  it("is false when the registered hint was never set - nothing to disagree with", () => {
    expect(editionMismatch("UNKNOWN", "EE")).toBe(false);
  });
});

describe("summarizeFleetHealth", () => {
  it("counts each database exactly once, in the same three buckets the Collector column renders", () => {
    expect(
      summarizeFleetHealth([
        row({ ...NEVER_COLLECTED, last_run_at: "x", last_success_at: "x" }), // healthy
        row(FAILING),
        row(NEVER_COLLECTED),
      ]),
    ).toEqual({ healthy: 1, failing: 1, notYetCollecting: 1 });
  });

  it("returns all zeros for an empty fleet", () => {
    expect(summarizeFleetHealth([])).toEqual({ healthy: 0, failing: 0, notYetCollecting: 0 });
  });
});

describe("HealthPage", () => {
  it("shows an empty state when no databases are registered", async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    expect(await screen.findByText("No databases registered yet")).toBeInTheDocument();
  });

  it("renders fleet-wide collector and capability status for a registered database", async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/databases/db-1/collector-status") return Promise.resolve(NEVER_COLLECTED);
      if (path === "/api/databases/db-1/capabilities") return Promise.resolve(UNPROBED);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    expect(await screen.findByText("PROD01")).toBeInTheDocument();
    expect(screen.getByText("Not yet collecting")).toBeInTheDocument();
    expect(screen.getByText("Never probed")).toBeInTheDocument();
  });

  it("shows what kind of failure and since when, not just a bare count", async () => {
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-09-01T13:02:00Z").getTime());
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/databases/db-1/collector-status") return Promise.resolve(FAILING);
      if (path === "/api/databases/db-1/capabilities") return Promise.resolve(UNPROBED);
      if (path === "/api/incidents?monitored_database_id=db-1") return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    expect(await screen.findByText("Failing (47)")).toBeInTheDocument();
    expect(screen.getByText("Unreachable · since 2h ago")).toBeInTheDocument();
    expect(screen.queryByText("View incident")).not.toBeInTheDocument();

    vi.restoreAllMocks();
  });

  it("links a failing collector to its real open incident", async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/databases/db-1/collector-status") return Promise.resolve(FAILING);
      if (path === "/api/databases/db-1/capabilities") return Promise.resolve(UNPROBED);
      if (path === "/api/incidents?monitored_database_id=db-1") return Promise.resolve([OPEN_INCIDENT]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const link = await screen.findByRole("link", { name: "View incident" });
    expect(link).toHaveAttribute("href", "/incidents?incident_id=inc-1");
  });

  it("flags a detected edition that disagrees with the registered edition hint", async () => {
    const MISMATCHED_PROBE: CapabilityProfile = { ...UNPROBED, detected_edition: "EE" };
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]); // DB is registered as SE2
      if (path === "/api/databases/db-1/collector-status") return Promise.resolve(NEVER_COLLECTED);
      if (path === "/api/databases/db-1/capabilities") return Promise.resolve(MISMATCHED_PROBE);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const badge = await screen.findByText("EE");
    expect(badge.className).toMatch(/warn/);
    expect(screen.getByText("Doesn't match registered edition (SE2)")).toBeInTheDocument();
  });

  it("does not flag a detected edition that matches the registered hint", async () => {
    const MATCHING_PROBE: CapabilityProfile = { ...UNPROBED, detected_edition: "SE2" };
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/databases/db-1/collector-status") return Promise.resolve(NEVER_COLLECTED);
      if (path === "/api/databases/db-1/capabilities") return Promise.resolve(MATCHING_PROBE);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    expect(await screen.findByText("PROD01")).toBeInTheDocument();
    expect(screen.getAllByText("SE2")).toHaveLength(2); // Edition badge + plain Detected edition text, no mismatch styling
    expect(screen.queryByText(/Doesn't match registered edition/)).not.toBeInTheDocument();
  });

  it("does not fetch incidents for a healthy or not-yet-collecting database", async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/databases/db-1/collector-status") return Promise.resolve(NEVER_COLLECTED);
      if (path === "/api/databases/db-1/capabilities") return Promise.resolve(UNPROBED);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    expect(await screen.findByText("Not yet collecting")).toBeInTheDocument();
    expect(mockApiFetch).not.toHaveBeenCalledWith(expect.stringContaining("/api/incidents"));
  });

  it("shows a fleet-wide alert banner and summary line across a real mix of statuses", async () => {
    const DB2: MonitoredDatabase = { ...DB, id: "db-2", name: "PROD02" };
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB, DB2]);
      if (path === "/api/databases/db-1/collector-status") return Promise.resolve(FAILING);
      if (path === "/api/databases/db-2/collector-status")
        return Promise.resolve({ ...NEVER_COLLECTED, last_run_at: "2026-09-01T13:00:00Z", last_success_at: "2026-09-01T13:00:00Z" });
      if (path.endsWith("/capabilities")) return Promise.resolve(UNPROBED);
      if (path === "/api/incidents?monitored_database_id=db-1") return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    expect(await screen.findByText("1 of 2 database(s) failing to collect - PROD01")).toBeInTheDocument();
    expect(screen.getByText("2 databases registered - 1 healthy, 1 failing.")).toBeInTheDocument();
  });

  it("flags a stale capability probe with a warn badge and a re-probe hint", async () => {
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-09-01T00:00:00Z").getTime());
    const STALE_PROBE: CapabilityProfile = { ...UNPROBED, last_probed_at: "2026-07-01T00:00:00Z" };
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/databases/db-1/collector-status") return Promise.resolve(NEVER_COLLECTED);
      if (path === "/api/databases/db-1/capabilities") return Promise.resolve(STALE_PROBE);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const badge = await screen.findByText("62d ago");
    expect(badge.className).toMatch(/warn/);
    expect(screen.getByText("Consider re-probing")).toBeInTheDocument();

    vi.restoreAllMocks();
  });

  it("shows a fresh capability probe with a good badge and no re-probe hint", async () => {
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-09-01T00:00:00Z").getTime());
    const FRESH_PROBE: CapabilityProfile = { ...UNPROBED, last_probed_at: "2026-08-30T00:00:00Z" };
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/databases/db-1/collector-status") return Promise.resolve(NEVER_COLLECTED);
      if (path === "/api/databases/db-1/capabilities") return Promise.resolve(FRESH_PROBE);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const badge = await screen.findByText("2d ago");
    expect(badge.className).toMatch(/good/);
    expect(screen.queryByText("Consider re-probing")).not.toBeInTheDocument();

    vi.restoreAllMocks();
  });

  it("omits the alert banner entirely when every database is healthy", async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/databases/db-1/collector-status")
        return Promise.resolve({ ...NEVER_COLLECTED, last_run_at: "2026-09-01T13:00:00Z", last_success_at: "2026-09-01T13:00:00Z" });
      if (path === "/api/databases/db-1/capabilities") return Promise.resolve(UNPROBED);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    expect(await screen.findByText("1 database registered - 1 healthy.")).toBeInTheDocument();
    expect(screen.queryByText(/failing to collect/)).not.toBeInTheDocument();
  });
});
