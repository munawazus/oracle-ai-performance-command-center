import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WaitDetailPanel } from "./WaitDetailPanel";
import type { Session, SessionsSnapshot } from "../../types/api";

const mockApiFetch = vi.fn();

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

function renderPanel(waitProfile: { total: number; topClass: string | null; topValue: number } | null = null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <WaitDetailPanel databaseId="db-1" waitProfile={waitProfile} />
    </QueryClientProvider>,
  );
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

function mockFetch(snapshot: SessionsSnapshot) {
  mockApiFetch.mockImplementation((path: string) => {
    if (path === "/api/databases/db-1/sessions") return Promise.resolve(snapshot);
    return Promise.reject(new Error(`unexpected fetch: ${path}`));
  });
}

describe("WaitDetailPanel", () => {
  it("shows an honest empty state before any session telemetry has been collected", async () => {
    mockFetch({ available: false, reason: "The collector has not run yet.", sampled_at: null, session_count: 0, sessions: [] });
    renderPanel();

    expect(await screen.findByText("No session telemetry yet")).toBeInTheDocument();
    expect(screen.getByText("The collector has not run yet.")).toBeInTheDocument();
  });

  it("shows an honest message when nothing is ACTIVE in the latest sample", async () => {
    mockFetch({
      available: true,
      reason: null,
      sampled_at: "2026-09-01T12:00:00Z",
      session_count: 1,
      sessions: [session({ status: "INACTIVE" })],
    });
    renderPanel();

    expect(await screen.findByText("No active sessions in the latest sample.")).toBeInTheDocument();
  });

  it("renders real active sessions with their SQL ID and wait class, excluding inactive ones", async () => {
    mockFetch({
      available: true,
      reason: null,
      sampled_at: "2026-09-01T12:00:00Z",
      session_count: 2,
      sessions: [
        session({ sid: 42, sql_id: "abc123xyz", wait_class: "User I/O", event: "db file sequential read" }),
        session({ sid: 99, status: "INACTIVE" }),
      ],
    });
    renderPanel();

    expect(await screen.findByText("42")).toBeInTheDocument();
    expect(screen.getByText("abc123xyz")).toBeInTheDocument();
    expect(screen.getByText("User I/O")).toBeInTheDocument();
    expect(screen.getByText("db file sequential read")).toBeInTheDocument();
    expect(screen.queryByText("99")).not.toBeInTheDocument();
  });

  it("sorts genuinely-waiting sessions before merely idle-active ones", async () => {
    mockFetch({
      available: true,
      reason: null,
      sampled_at: "2026-09-01T12:00:00Z",
      session_count: 2,
      sessions: [
        session({ sid: 1, wait_class: "Idle", event: "SQL*Net message from client" }),
        session({ sid: 2, wait_class: "User I/O", event: "db file sequential read" }),
      ],
    });
    renderPanel();

    const sidCells = await screen.findAllByText(/^[12]$/);
    expect(sidCells.map((el) => el.textContent)).toEqual(["2", "1"]);
  });

  it("shows the real wait-profile summary, dominant class included", async () => {
    mockFetch({ available: true, reason: null, sampled_at: "2026-09-01T12:00:00Z", session_count: 0, sessions: [] });
    renderPanel({ total: 0.13, topClass: "User I/O", topValue: 0.1 });

    expect(await screen.findByText("Total 0.13 AAS · dominated by User I/O (0.10)")).toBeInTheDocument();
  });

  it("shows no-activity in the summary when the wait profile is empty", async () => {
    mockFetch({ available: true, reason: null, sampled_at: "2026-09-01T12:00:00Z", session_count: 0, sessions: [] });
    renderPanel({ total: 0, topClass: null, topValue: 0 });

    expect(await screen.findByText("No activity right now")).toBeInTheDocument();
  });
});
