import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChangeIntelligencePage, summarizeChangeCounts } from "./ChangeIntelligencePage";
import type { ConfigChangeEvent, ConfigurationSummaryResponse, MonitoredDatabase, ParameterChangeItem } from "../types/api";

const mockApiFetch = vi.fn();

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChangeIntelligencePage />
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

function mockFetch(configuration: ConfigurationSummaryResponse, history: ConfigChangeEvent[] = []) {
  mockApiFetch.mockImplementation((path: string) => {
    if (path === "/api/databases") return Promise.resolve([DB]);
    if (path === "/api/databases/db-1/configuration") return Promise.resolve(configuration);
    if (path.startsWith("/api/databases/db-1/configuration/history")) return Promise.resolve(history);
    return Promise.reject(new Error(`unexpected fetch: ${path}`));
  });
}

function change(overrides: Partial<ParameterChangeItem>): ParameterChangeItem {
  return { parameter_name: "some_param", previous_value: null, current_value: null, change_type: "changed", ...overrides };
}

describe("summarizeChangeCounts", () => {
  it("counts each change type, only including ones that actually occurred", () => {
    expect(
      summarizeChangeCounts([
        change({ change_type: "changed" }),
        change({ change_type: "changed" }),
        change({ change_type: "newly_set" }),
      ]),
    ).toBe("2 changed, 1 newly set");
  });

  it("returns an empty string for no changes", () => {
    expect(summarizeChangeCounts([])).toBe("");
  });

  it("orders reverted_to_default last regardless of input order", () => {
    expect(
      summarizeChangeCounts([change({ change_type: "reverted_to_default" }), change({ change_type: "changed" })]),
    ).toBe("1 changed, 1 reverted to default");
  });
});

describe("ChangeIntelligencePage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows an honest unavailable state before two configuration collection rounds exist", async () => {
    mockFetch({
      available: false,
      reason: "no configuration telemetry has been collected yet",
      from_sampled_at: null,
      to_sampled_at: null,
      changes: [],
    });
    renderPage();

    expect(await screen.findByText("Not enough configuration telemetry yet")).toBeInTheDocument();
  });

  it("shows an honest empty state when two rounds exist but nothing changed", async () => {
    mockFetch({
      available: true,
      reason: null,
      from_sampled_at: "2026-09-01T11:00:00Z",
      to_sampled_at: "2026-09-01T12:00:00Z",
      changes: [],
    });
    renderPage();

    expect(await screen.findByText("No configuration changes detected")).toBeInTheDocument();
  });

  it("renders detected changes with their change-type badges", async () => {
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-09-01T14:00:00Z").getTime());
    mockFetch({
      available: true,
      reason: null,
      from_sampled_at: "2026-09-01T11:00:00Z",
      to_sampled_at: "2026-09-01T12:00:00Z",
      changes: [
        { parameter_name: "open_cursors", previous_value: "300", current_value: "500", change_type: "changed" },
        { parameter_name: "audit_trail", previous_value: null, current_value: "DB", change_type: "newly_set" },
        {
          parameter_name: "sga_target",
          previous_value: "4000000000",
          current_value: null,
          change_type: "reverted_to_default",
        },
      ],
    });
    renderPage();

    expect(await screen.findByText("open_cursors")).toBeInTheDocument();
    expect(screen.getByText("Changed")).toBeInTheDocument();
    expect(screen.getByText("Newly set")).toBeInTheDocument();
    expect(screen.getByText("Reverted to default")).toBeInTheDocument();
    expect(screen.getByText("500")).toBeInTheDocument();
    expect(screen.getAllByText("(default)").length).toBe(2);

    // Quick-glance breakdown, real relative freshness, and the persistence caveat all present.
    expect(
      screen.getByText((_, el) => el?.tagName === "P" && (el.textContent?.startsWith("3 changes (1 changed, 1 newly set, 1 reverted to default) since") ?? false)),
    ).toBeInTheDocument();
    expect(screen.getByText(/\(2h ago\)\.$/)).toBeInTheDocument();
    expect(
      screen.getByText(/Reflects the value currently in effect only/),
    ).toBeInTheDocument();
  });

  it("shows an honest empty state for history when nothing changed in the window", async () => {
    mockFetch({ available: true, reason: null, from_sampled_at: null, to_sampled_at: null, changes: [] });
    renderPage();

    expect(await screen.findByText("No configuration changes in this window")).toBeInTheDocument();
  });

  it("renders history events across a window, each with its own relative time", async () => {
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-09-01T14:00:00Z").getTime());
    mockFetch(
      { available: true, reason: null, from_sampled_at: null, to_sampled_at: null, changes: [] },
      [
        {
          detected_at: "2026-09-01T12:00:00Z",
          parameter_name: "open_cursors",
          previous_value: "300",
          current_value: "500",
          change_type: "changed",
        },
        {
          detected_at: "2026-09-01T09:00:00Z",
          parameter_name: "open_cursors",
          previous_value: "500",
          current_value: "300",
          change_type: "changed",
        },
      ],
    );
    renderPage();

    expect(await screen.findByText("2h ago")).toBeInTheDocument();
    expect(screen.getByText("5h ago")).toBeInTheDocument();
  });

  it("requests the selected history window", async () => {
    mockFetch({ available: true, reason: null, from_sampled_at: null, to_sampled_at: null, changes: [] });
    renderPage();

    await screen.findByText("No configuration changes in this window");
    // 24h default (not the app's usual 1h) - config changes are infrequent enough that a 1h
    // default would almost always show nothing here.
    expect(mockApiFetch).toHaveBeenCalledWith("/api/databases/db-1/configuration/history?hours=24");
  });
});
