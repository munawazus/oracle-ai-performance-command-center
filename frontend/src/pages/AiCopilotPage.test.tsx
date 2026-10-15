import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AiCopilotPage } from "./AiCopilotPage";
import { ApiError } from "../lib/api";
import type { CopilotAnswer, Incident, MonitoredDatabase } from "../types/api";

const mockApiFetch = vi.fn();

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

beforeEach(() => {
  mockApiFetch.mockClear();
});

function renderPage(initialPath = "/ai-copilot") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <AiCopilotPage />
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

describe("AiCopilotPage", () => {
  it("shows an empty state before any question is asked", async () => {
    mockApiFetch.mockResolvedValue([DB]);
    renderPage();
    expect(await screen.findByText("Ask about a monitored database")).toBeInTheDocument();
  });

  it("asks a question and renders the grounded answer with tool badges", async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/ai/ask") {
        return Promise.resolve({
          answer: "Sessions are within normal range.",
          model: "gpt-oss-20b",
          tools_used: ["get_database_health"],
          tool_statuses: [],
          evidence_coverage: null,
          findings: [],
        } satisfies CopilotAnswer);
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const input = await screen.findByPlaceholderText(/ask a question/i);
    await userEvent.type(input, "Is it healthy?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(await screen.findByText("Sessions are within normal range.")).toBeInTheDocument();
    expect(screen.getByText("gpt-oss-20b")).toBeInTheDocument();
    // Backward-compat: an empty tool_statuses list falls back to rendering the legacy flat
    // tools_used names, so a not-yet-upgraded/older response shape still renders sensibly.
    expect(screen.getByText("get_database_health")).toBeInTheDocument();
  });

  it("distinguishes a tool that found evidence from one that was checked but empty", async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/ai/ask") {
        return Promise.resolve({
          answer: "No blocking sessions right now.",
          model: "qwen2.5:7b-instruct",
          tools_used: ["get_blocking_chain", "get_wait_profile"],
          tool_statuses: [
            { tool_name: "get_blocking_chain", called: true, returned_evidence: true, reason: null },
            { tool_name: "get_wait_profile", called: true, returned_evidence: false, reason: "fewer than two collection rounds" },
          ],
          evidence_coverage: "sparse",
          findings: [],
        } satisfies CopilotAnswer);
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const input = await screen.findByPlaceholderText(/ask a question/i);
    await userEvent.type(input, "Any blocking?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    const found = await screen.findByText("get_blocking_chain");
    const empty = await screen.findByText("get_wait_profile (no findings)");
    expect(found.className).toMatch(/good/);
    expect(empty.className).toMatch(/neutral/);
    expect(screen.getByText("evidence: sparse")).toBeInTheDocument();
  });

  it("shows a finding's freshness and flags it stale, with an inspectable evidence disclosure", async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/ai/ask") {
        return Promise.resolve({
          answer: "SID 421 is the primary blocker.",
          model: "qwen2.5:7b-instruct",
          tools_used: ["get_blocking_chain"],
          tool_statuses: [{ tool_name: "get_blocking_chain", called: true, returned_evidence: true, reason: null }],
          evidence_coverage: "clear",
          findings: [
            {
              finding_id: "blocking_421",
              category: "blocking",
              severity: "CRITICAL",
              summary: "SID 421 has been holding a transaction for 812s and is blocking 37 session(s).",
              entities: { blocker_sid: 421, waiter_count: 37 },
              sampled_at: "2020-01-01T00:00:00Z", // old and fixed, so it's reliably stale
              age_seconds: 999999,
              is_stale: true,
            },
          ],
        } satisfies CopilotAnswer);
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const input = await screen.findByPlaceholderText(/ask a question/i);
    await userEvent.type(input, "Who is blocking?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(await screen.findByText(/SID 421 has been holding/)).toBeInTheDocument();
    expect(screen.getByText(/stale/)).toBeInTheDocument();
    const details = screen.getByText(/SID 421 has been holding/).closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(screen.getByText("blocker_sid: 421")).toBeInTheDocument();
  });

  it("does not allow submitting a second question while the first is still in flight", async () => {
    let resolveFirst: (value: CopilotAnswer) => void = () => {};
    const firstCallPromise = new Promise<CopilotAnswer>((resolve) => {
      resolveFirst = resolve;
    });
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/ai/ask") return firstCallPromise;
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const input = (await screen.findByPlaceholderText(/ask a question/i)) as HTMLInputElement;
    await userEvent.type(input, "First question");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(input).toBeDisabled();
    // A second Enter press while the first request is still outstanding must not fire another call.
    await userEvent.type(input, "Second question{Enter}", { skipClick: true }).catch(() => {});
    expect(mockApiFetch.mock.calls.filter((c) => c[0] === "/api/ai/ask")).toHaveLength(1);

    resolveFirst({
      answer: "Done.",
      model: "m",
      tools_used: [],
      tool_statuses: [],
      evidence_coverage: null,
      findings: [],
    });
    await screen.findByText("Done.");
  });

  it("shows the backend's honest error when AI is not enabled", async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/ai/ask") {
        return Promise.reject(new ApiError(502, "AI is not enabled on this deployment."));
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const input = await screen.findByPlaceholderText(/ask a question/i);
    await userEvent.type(input, "Is it healthy?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    // Exactly once, not once in the chat transcript AND again in a separate banner - a real
    // duplicate-rendering bug found via live QA testing (docs/QA_LIVE_FINDINGS.md).
    expect(await screen.findAllByText("AI is not enabled on this deployment.")).toHaveLength(1);
  });

  it("pre-selects the database from a database_id URL param", async () => {
    mockApiFetch.mockResolvedValue([DB]);
    renderPage("/ai-copilot?database_id=db-1");

    const select = (await screen.findByLabelText("Database context")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("db-1"));
  });

  it("carries incident_id into the request and shows an investigating-incident banner", async () => {
    const INCIDENT: Incident = {
      id: "inc-1",
      monitored_database_id: "db-1",
      status: "OPEN",
      severity: "CRITICAL",
      title: "Database connection failing",
      opened_at: "2026-01-01T00:00:00Z",
      last_evidence_at: "2026-01-01T00:05:00Z",
      acknowledged_at: null,
      acknowledged_by_user_id: null,
      assigned_to_user_id: null,
      resolved_at: null,
      resolved_by_user_id: null,
      resolution_note: null,
    };
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/incidents/inc-1") return Promise.resolve(INCIDENT);
      if (path === "/api/ai/ask") {
        return Promise.resolve({
          answer: "Root cause identified.",
          model: "m",
          tools_used: [],
          tool_statuses: [],
          evidence_coverage: "clear",
          findings: [],
        } satisfies CopilotAnswer);
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage("/ai-copilot?database_id=db-1&incident_id=inc-1");

    expect(await screen.findByText("Database connection failing")).toBeInTheDocument();
    expect(screen.getByText("CRITICAL")).toBeInTheDocument();
    // Locked to the incident's own database - the UI itself must never be able to construct
    // the incident_id/database_id mismatch the backend rejects with a 409.
    const select = (await screen.findByLabelText("Database context")) as HTMLSelectElement;
    expect(select).toBeDisabled();

    const input = await screen.findByPlaceholderText(/ask a question/i);
    await userEvent.type(input, "What's the root cause?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText("Root cause identified.");

    const askCall = mockApiFetch.mock.calls.find((c) => c[0] === "/api/ai/ask");
    expect(askCall?.[1]?.body).toMatchObject({ incident_id: "inc-1", database_id: "db-1" });
  });

  it("sends fleet_scope and drops the selected database when the fleet toggle is on", async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/ai/ask") {
        return Promise.resolve({
          answer: "Everything is calm fleet-wide.",
          model: "m",
          tools_used: [],
          tool_statuses: [],
          evidence_coverage: "clear",
          findings: [],
        } satisfies CopilotAnswer);
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage("/ai-copilot?database_id=db-1");

    const select = (await screen.findByLabelText("Database context")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("db-1"));

    const toggle = screen.getByLabelText("Ask about the whole fleet");
    await userEvent.click(toggle);
    expect(select).toBeDisabled();
    expect(await screen.findByText(/is anything wrong anywhere/i)).toBeInTheDocument();

    const input = await screen.findByPlaceholderText(/ask a question/i);
    await userEvent.type(input, "Is anything wrong anywhere?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    await screen.findByText("Everything is calm fleet-wide.");
    const askCall = mockApiFetch.mock.calls.find((c) => c[0] === "/api/ai/ask");
    expect(askCall?.[1]?.body).toMatchObject({ fleet_scope: true });
    expect(askCall?.[1]?.body).not.toHaveProperty("database_id", "db-1");
  });

  it("disables the fleet toggle while an incident is in context", async () => {
    mockApiFetch.mockResolvedValue([DB]);
    renderPage("/ai-copilot?database_id=db-1&incident_id=inc-1");

    const toggle = await screen.findByLabelText("Ask about the whole fleet");
    expect(toggle).toBeDisabled();
  });
});
