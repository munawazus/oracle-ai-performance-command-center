import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { Incident } from "../../types/api";
import { NotificationsBell } from "./NotificationsBell";

const mockApiFetch = vi.fn();
const mockNavigate = vi.fn();

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

function incident(overrides: Partial<Incident>): Incident {
  return {
    id: "inc-x",
    monitored_database_id: "db-1",
    status: "OPEN",
    severity: "LOW",
    title: "An incident",
    opened_at: "2026-01-01T00:00:00Z",
    last_evidence_at: "2026-01-01T00:00:00Z",
    acknowledged_at: null,
    acknowledged_by_user_id: null,
    assigned_to_user_id: null,
    resolved_at: null,
    resolved_by_user_id: null,
    resolution_note: null,
    ...overrides,
  };
}

const LOW = incident({ id: "inc-low", severity: "LOW", title: "Low severity noise" });
const MEDIUM = incident({ id: "inc-medium", severity: "MEDIUM", title: "Medium severity blip" });
const HIGH = incident({
  id: "inc-high",
  severity: "HIGH",
  title: "High severity wait spike",
  last_evidence_at: "2026-01-01T00:10:00Z",
});
const CRITICAL = incident({
  id: "inc-critical",
  severity: "CRITICAL",
  title: "Critical session storm",
  last_evidence_at: "2026-01-01T00:20:00Z",
});

function renderBell(incidents: Incident[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  mockApiFetch.mockImplementation((path: string) => {
    if (path === "/api/incidents?status=OPEN") return Promise.resolve(incidents);
    return Promise.reject(new Error(`unexpected fetch: ${path}`));
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <NotificationsBell />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("NotificationsBell", () => {
  it("badges only the HIGH+CRITICAL count, not LOW/MEDIUM", async () => {
    renderBell([LOW, MEDIUM, HIGH, CRITICAL]);

    expect(await screen.findByLabelText("Notifications - 2 open HIGH or CRITICAL incident(s)")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("lists HIGH/CRITICAL incidents most-recent-first, excluding LOW/MEDIUM", async () => {
    renderBell([LOW, MEDIUM, HIGH, CRITICAL]);
    await userEvent.click(await screen.findByLabelText(/Notifications/));

    expect(screen.queryByText("Low severity noise")).not.toBeInTheDocument();
    expect(screen.queryByText("Medium severity blip")).not.toBeInTheDocument();
    const dialog = screen.getByRole("dialog");
    const items = within(dialog).getAllByRole("button");
    expect(items[0]).toHaveTextContent("Critical session storm");
    expect(items[1]).toHaveTextContent("High severity wait spike");
  });

  it("shows a quiet empty state and no badge when nothing is open", async () => {
    renderBell([LOW, MEDIUM]);
    await userEvent.click(await screen.findByLabelText(/Notifications/));

    expect(screen.getByText("Nothing open right now.")).toBeInTheDocument();
  });

  it("navigates to the incident's deep link when a notification is clicked", async () => {
    renderBell([CRITICAL]);
    await userEvent.click(await screen.findByLabelText(/Notifications/));
    await userEvent.click(screen.getByText("Critical session storm"));

    expect(mockNavigate).toHaveBeenCalledWith("/incidents?incident_id=inc-critical");
  });
});
