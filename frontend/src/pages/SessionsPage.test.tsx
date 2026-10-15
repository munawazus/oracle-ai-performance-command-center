import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { formatDurationSeconds, sessionDurationLabel, sessionStatusTone, SessionsPage } from "./SessionsPage";
import type { MonitoredDatabase, Session, SessionsSnapshot } from "../types/api";

const mockApiFetch = vi.fn();

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SessionsPage />
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

function mockFetch(sessions: SessionsSnapshot) {
  mockApiFetch.mockImplementation((path: string) => {
    if (path === "/api/databases") return Promise.resolve([DB]);
    if (path === "/api/databases/db-1/sessions") return Promise.resolve(sessions);
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

describe("sessionStatusTone", () => {
  it("is Inactive/neutral when the session isn't ACTIVE at all", () => {
    expect(sessionStatusTone(session({ status: "INACTIVE" }))).toEqual({ label: "Inactive", tone: "neutral" });
  });

  it("is Blocked/warn when a real blocking_session is present, regardless of state", () => {
    expect(sessionStatusTone(session({ status: "ACTIVE", blocking_session: 42, state: "WAITED SHORT TIME" }))).toEqual({
      label: "Blocked",
      tone: "warn",
    });
  });

  it("is Active/good when ACTIVE and unblocked, even while WAITING on a resource like I/O", () => {
    // WAITING with no blocker is normal operation (e.g. disk I/O) - not the same signal as
    // being blocked by another session's lock, so this must not read as orange too.
    expect(sessionStatusTone(session({ status: "ACTIVE", blocking_session: null, state: "WAITING" }))).toEqual({
      label: "Active",
      tone: "good",
    });
  });
});

describe("SessionsPage", () => {
  it("prompts for a database selection before any database is chosen", async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    expect(await screen.findByText("Select a database to view its sessions")).toBeInTheDocument();
  });

  it("shows an honest empty state when no session telemetry is available yet", async () => {
    mockFetch({ available: false, reason: "The collector has not run yet.", sampled_at: null, session_count: 0, sessions: [] });
    renderPage();

    expect(await screen.findByText("No session telemetry yet")).toBeInTheDocument();
    expect(screen.getByText("The collector has not run yet.")).toBeInTheDocument();
  });

  it("renders a table of active sessions including a blocked-by badge", async () => {
    mockFetch({
      available: true,
      reason: null,
      sampled_at: "2026-09-01T12:00:00Z",
      session_count: 2,
      sessions: [
        {
          sid: 42,
          serial_num: 100,
          username: "APP_USER",
          sql_id: "abc123xyz",
          event: "db file sequential read",
          wait_class: "User I/O",
          status: "ACTIVE",
          blocking_session: null,
          machine: "app-host-1",
          program: "sqlplus",
          module: null,
          action: null,
          state: "WAITING",
          last_call_et: 5,
        },
        {
          sid: 43,
          serial_num: 101,
          username: "APP_USER",
          sql_id: null,
          event: "enq: TX - row lock contention",
          wait_class: "Application",
          status: "ACTIVE",
          blocking_session: 42,
          machine: "app-host-2",
          program: "sqlplus",
          module: null,
          action: null,
          state: "WAITED SHORT TIME",
          last_call_et: 7200,
        },
      ],
    });
    renderPage();

    expect(await screen.findByText("42")).toBeInTheDocument();
    expect(screen.getByText("43")).toBeInTheDocument();
    expect(screen.getByText("SID 42")).toBeInTheDocument();
    expect(screen.getByText(/2 session\(s\) as of/)).toBeInTheDocument();
    // SID 42 is genuinely WAITING (blocked on its event right now); SID 43 recently
    // finished a wait and may really be on CPU - the whole point of Finding 7's fix.
    expect(screen.getByText("WAITING")).toBeInTheDocument();
    expect(screen.getByText("WAITED SHORT TIME")).toBeInTheDocument();
    // SID 42 has no blocking_session (unblocked, reads Active); SID 43 does (reads Blocked) -
    // the combined status pill, not just the separate "SID 42" blocked-by badge above.
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Blocked")).toBeInTheDocument();
    // Both are ACTIVE (SID 43 is "Blocked" but its underlying status is still ACTIVE) - a
    // 5-second wait and a 2-hour one previously rendered identically.
    expect(screen.getByText("Active for 5s")).toBeInTheDocument();
    expect(screen.getByText("Active for 2h")).toBeInTheDocument();
  });
});

describe("formatDurationSeconds", () => {
  it("formats sub-minute durations in seconds", () => {
    expect(formatDurationSeconds(45)).toBe("45s");
  });

  it("formats sub-hour durations in minutes", () => {
    expect(formatDurationSeconds(125)).toBe("2m");
  });

  it("formats sub-day durations in hours", () => {
    expect(formatDurationSeconds(7200)).toBe("2h");
  });

  it("formats multi-day durations in days", () => {
    expect(formatDurationSeconds(200_000)).toBe("2d");
  });
});

describe("sessionDurationLabel", () => {
  it("is a dash when last_call_et was never collected", () => {
    expect(sessionDurationLabel(session({ last_call_et: null }))).toBe("—");
  });

  it("reads 'Active for' when the session's status is ACTIVE", () => {
    expect(sessionDurationLabel(session({ status: "ACTIVE", last_call_et: 90 }))).toBe("Active for 1m");
  });

  it("reads 'Idle for' when the session's status is not ACTIVE", () => {
    expect(sessionDurationLabel(session({ status: "INACTIVE", last_call_et: 90 }))).toBe("Idle for 1m");
  });
});
