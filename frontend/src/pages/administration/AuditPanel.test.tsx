import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AuditPanel } from "./AuditPanel";
import type { AuditEvent } from "../../types/api";

const mockApiFetch = vi.fn();

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuditPanel />
    </QueryClientProvider>,
  );
}

function makeEvent(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id: "audit-1",
    occurred_at: "2026-01-01T00:00:00Z",
    actor_user_id: "user-1",
    action: "auth.login",
    actor_email: "admin@ocpcc.dev",
    target_type: null,
    target_id: null,
    success: true,
    ip_address: "10.0.0.5",
    event_metadata: null,
    ...overrides,
  };
}

describe("AuditPanel", () => {
  it("shows an empty state when there are no matching events", async () => {
    mockApiFetch.mockResolvedValue([]);
    renderPanel();

    expect(await screen.findByText("No matching audit events")).toBeInTheDocument();
  });

  it("lists an audit event with its action and result", async () => {
    mockApiFetch.mockResolvedValue([makeEvent()]);
    renderPanel();

    expect(await screen.findByText("auth.login")).toBeInTheDocument();
    expect(screen.getByText("admin@ocpcc.dev")).toBeInTheDocument();
    expect(screen.getByText("Success")).toBeInTheDocument();
  });

  it("filters by action prefix and resets the offset", async () => {
    mockApiFetch.mockResolvedValue([]);
    renderPanel();

    await screen.findByText("No matching audit events");
    await userEvent.type(screen.getByLabelText("Filter by action prefix"), "auth.");

    expect(mockApiFetch).toHaveBeenLastCalledWith("/api/audit?limit=50&offset=0&action_prefix=auth.");
  });

  it("pages forward and back through results", async () => {
    const fullPage = Array.from({ length: 50 }, (_, i) => makeEvent({ id: `audit-${i}`, action: `action.${i}` }));
    mockApiFetch.mockImplementation((path: string) => {
      if (path.includes("offset=50")) return Promise.resolve([makeEvent({ id: "audit-50", action: "action.50" })]);
      return Promise.resolve(fullPage);
    });
    renderPanel();

    await screen.findByText("action.0");
    const previousButton = screen.getByRole("button", { name: "Previous" });
    const nextButton = screen.getByRole("button", { name: "Next" });
    expect(previousButton).toBeDisabled();
    expect(nextButton).toBeEnabled();

    await userEvent.click(nextButton);

    expect(await screen.findByText("action.50")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenLastCalledWith("/api/audit?limit=50&offset=50");
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Previous" }));

    expect(await screen.findByText("action.0")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenLastCalledWith("/api/audit?limit=50&offset=0");
  });
});
