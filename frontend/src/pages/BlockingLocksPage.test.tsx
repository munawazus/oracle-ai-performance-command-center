import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BlockingLocksPage, buildBlockingTree } from "./BlockingLocksPage";
import type { BlockingChainEntry, BlockingSnapshot, MonitoredDatabase } from "../types/api";

const mockApiFetch = vi.fn();

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BlockingLocksPage />
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

function mockFetch(blocking: BlockingSnapshot) {
  mockApiFetch.mockImplementation((path: string) => {
    if (path === "/api/databases") return Promise.resolve([DB]);
    if (path === "/api/databases/db-1/blocking") return Promise.resolve(blocking);
    return Promise.reject(new Error(`unexpected fetch: ${path}`));
  });
}

function chain(overrides: Partial<BlockingChainEntry>): BlockingChainEntry {
  return {
    blocked_sid: 2,
    blocked_username: "APP_USER",
    blocked_sql_id: null,
    blocker_sid: 1,
    blocker_username: "APP_USER",
    blocker_sql_id: null,
    blocker_in_this_sample: true,
    ...overrides,
  };
}

describe("BlockingLocksPage", () => {
  it("shows a no-blocking state when nothing is currently blocked", async () => {
    mockFetch({ available: true, reason: null, sampled_at: "2026-09-01T12:00:00Z", blocking_chains: [] });
    renderPage();

    expect(await screen.findByText("No blocking sessions")).toBeInTheDocument();
  });

  it("renders a blocking chain with a not-in-this-sample badge for an out-of-sample blocker", async () => {
    mockFetch({
      available: true,
      reason: null,
      sampled_at: "2026-09-01T12:00:00Z",
      blocking_chains: [
        {
          blocked_sid: 43,
          blocked_username: "APP_USER",
          blocked_sql_id: "def456",
          blocker_sid: 42,
          blocker_username: "APP_USER",
          blocker_sql_id: null,
          blocker_in_this_sample: false,
        },
      ],
    });
    renderPage();

    const waiter = (await screen.findByText("SID 43")).closest("li");
    expect(waiter).not.toBeNull();
    expect(within(waiter!).getByText("(APP_USER)")).toBeInTheDocument();
    expect(screen.getByText("not in this sample")).toBeInTheDocument();
  });

  it("nests a waiter under its direct blocker in a two-level chain", async () => {
    mockFetch({
      available: true,
      reason: null,
      sampled_at: "2026-09-01T12:00:00Z",
      blocking_chains: [
        chain({ blocked_sid: 20, blocker_sid: 10 }),
        chain({ blocked_sid: 30, blocker_sid: 10 }),
      ],
    });
    renderPage();

    const blocker = await screen.findByText("SID 10");
    const waiterA = screen.getByText("SID 20");
    const waiterB = screen.getByText("SID 30");
    // Both waiters nest inside the blocker's own <li>, not as siblings of it.
    const blockerItem = blocker.closest("li");
    expect(blockerItem).not.toBeNull();
    expect(blockerItem).toContainElement(waiterA);
    expect(blockerItem).toContainElement(waiterB);
    expect(screen.getByText("2 sessions waiting")).toBeInTheDocument();
  });
});

describe("buildBlockingTree", () => {
  it("builds a single root with one waiter from one pair", () => {
    const tree = buildBlockingTree([chain({ blocked_sid: 2, blocker_sid: 1 })]);

    expect(tree).toHaveLength(1);
    expect(tree[0].sid).toBe(1);
    expect(tree[0].waiters).toHaveLength(1);
    expect(tree[0].waiters[0].sid).toBe(2);
  });

  it("attaches multiple waiters under one blocker", () => {
    const tree = buildBlockingTree([
      chain({ blocked_sid: 2, blocker_sid: 1 }),
      chain({ blocked_sid: 3, blocker_sid: 1 }),
      chain({ blocked_sid: 4, blocker_sid: 1 }),
    ]);

    expect(tree).toHaveLength(1);
    expect(tree[0].waiters.map((w) => w.sid).sort()).toEqual([2, 3, 4]);
  });

  it("nests a genuine 3-level chain: C waits on B waits on A", () => {
    const tree = buildBlockingTree([
      chain({ blocked_sid: 2, blocker_sid: 1 }), // B(2) waits on A(1)
      chain({ blocked_sid: 3, blocker_sid: 2 }), // C(3) waits on B(2)
    ]);

    expect(tree).toHaveLength(1);
    expect(tree[0].sid).toBe(1);
    expect(tree[0].waiters).toHaveLength(1);
    expect(tree[0].waiters[0].sid).toBe(2);
    expect(tree[0].waiters[0].waiters).toHaveLength(1);
    expect(tree[0].waiters[0].waiters[0].sid).toBe(3);
  });

  it("treats a blocker not captured in this sample as a valid childless root", () => {
    const tree = buildBlockingTree([
      chain({ blocked_sid: 2, blocker_sid: 1, blocker_in_this_sample: false }),
    ]);

    expect(tree).toHaveLength(1);
    expect(tree[0].sid).toBe(1);
    expect(tree[0].inThisSample).toBe(false);
    expect(tree[0].waiters).toHaveLength(1);
  });

  it("does not infinite-loop on a contrived cyclical fixture", () => {
    const tree = buildBlockingTree([
      chain({ blocked_sid: 1, blocker_sid: 2 }),
      chain({ blocked_sid: 2, blocker_sid: 1 }),
    ]);

    // Every sid appears as both blocked and blocker, so there is no valid root - the
    // function must terminate (not hang) and simply report no tree rather than throwing.
    expect(tree).toEqual([]);
  });
});
