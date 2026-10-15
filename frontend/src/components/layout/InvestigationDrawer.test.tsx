import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { Header } from "./Header";
import { InvestigationDrawer } from "./InvestigationDrawer";
import { InvestigationProvider, useInvestigation } from "../../lib/investigation-context";
import type { Incident } from "../../types/api";

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

// The Header integration test renders the real Header, which renders UserMenu - give it a
// harmless "not logged in" auth state rather than pulling in the full real AuthProvider flow,
// which is unrelated to what this test is verifying (the investigation icon opening the rail).
vi.mock("../../lib/auth-context", () => ({
  useAuth: () => ({ user: null, isLoading: false, login: vi.fn(), logout: vi.fn(), hasPermission: () => true }),
}));

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

// InvestigationDrawer's isOpen/currentDatabaseId come only from context, not props - this
// test-only helper reaches into the real provider to drive both, the way DatabaseSelector
// (setCurrentDatabase) and the Header icon (toggle) do in the real app.
function Opener({ databaseId, databaseName }: { databaseId?: string; databaseName?: string }) {
  const { toggle, setCurrentDatabase } = useInvestigation();
  return (
    <button
      onClick={() => {
        toggle();
        if (databaseId && databaseName) setCurrentDatabase(databaseId, databaseName);
      }}
    >
      open-and-select
    </button>
  );
}

function renderDrawer({ databaseId, databaseName }: { databaseId?: string; databaseName?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <InvestigationProvider>
          <Opener databaseId={databaseId} databaseName={databaseName} />
          <InvestigationDrawer />
        </InvestigationProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("InvestigationDrawer", () => {
  it("is closed (hidden, not interactive) by default", () => {
    renderDrawer();
    // Queried by data-testid, not role+name: aria-hidden="true" removes the element from the
    // accessibility tree by design (the real behavior under test), which also makes
    // getByRole's own accessible-name computation unreliable for a still-hidden element.
    expect(screen.getByTestId("investigation-drawer")).toHaveAttribute("aria-hidden", "true");
  });

  it("shows an honest empty state when no database has been selected yet", async () => {
    renderDrawer();
    await userEvent.click(screen.getByText("open-and-select"));

    expect(await screen.findByText("No database selected yet")).toBeInTheDocument();
  });

  it("disables Open Incident with an honest label when there are no open incidents", async () => {
    mockApiFetch.mockResolvedValue([incident({ status: "RESOLVED" })]);
    renderDrawer({ databaseId: "db-1", databaseName: "PROD01" });
    await userEvent.click(screen.getByText("open-and-select"));

    expect(await screen.findByText("PROD01")).toBeInTheDocument();
    expect(await screen.findByText("0 open incidents for this database.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Incident" })).toBeDisabled();
  });

  it("opens the newest non-resolved incident by last_evidence_at", async () => {
    mockApiFetch.mockResolvedValue([
      incident({ id: "inc-old", status: "OPEN", last_evidence_at: "2026-01-01T00:00:00Z" }),
      incident({ id: "inc-new", status: "ACKNOWLEDGED", last_evidence_at: "2026-01-02T00:00:00Z" }),
    ]);
    renderDrawer({ databaseId: "db-1", databaseName: "PROD01" });
    await userEvent.click(screen.getByText("open-and-select"));

    const button = await screen.findByRole("button", { name: "Open Incident" });
    expect(button).not.toBeDisabled();
    await userEvent.click(button);

    expect(mockNavigate).toHaveBeenCalledWith("/incidents?incident_id=inc-new");
  });

  it("carries the newest open incident's id into the Ask AI Copilot link and relabels it", async () => {
    mockApiFetch.mockResolvedValue([
      incident({ id: "inc-old", status: "OPEN", last_evidence_at: "2026-01-01T00:00:00Z" }),
      incident({ id: "inc-new", status: "ACKNOWLEDGED", last_evidence_at: "2026-01-02T00:00:00Z" }),
    ]);
    renderDrawer({ databaseId: "db-1", databaseName: "PROD01" });
    await userEvent.click(screen.getByText("open-and-select"));

    const button = await screen.findByRole("button", { name: "Ask AI Copilot about this incident" });
    await userEvent.click(button);

    expect(mockNavigate).toHaveBeenCalledWith("/ai-copilot?database_id=db-1&incident_id=inc-new");
  });

  it("navigates to AI Copilot and Reports pre-filled with the current database", async () => {
    mockApiFetch.mockResolvedValue([]);
    renderDrawer({ databaseId: "db-1", databaseName: "PROD01" });
    await userEvent.click(screen.getByText("open-and-select"));

    await userEvent.click(await screen.findByRole("button", { name: "Ask AI Copilot" }));
    expect(mockNavigate).toHaveBeenCalledWith("/ai-copilot?database_id=db-1");

    await userEvent.click(screen.getByRole("button", { name: "Generate Report" }));
    expect(mockNavigate).toHaveBeenCalledWith("/reports?database_id=db-1");
  });

  it("opens when the Header's investigation icon is clicked", async () => {
    mockApiFetch.mockResolvedValue([]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <InvestigationProvider>
            <Header />
            <InvestigationDrawer />
          </InvestigationProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const panel = screen.getByTestId("investigation-drawer");
    expect(panel).toHaveAttribute("aria-hidden", "true");

    await userEvent.click(screen.getByRole("button", { name: "Toggle investigation panel" }));
    expect(panel).toHaveAttribute("aria-hidden", "false");
  });
});
