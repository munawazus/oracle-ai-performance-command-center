import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CapabilityPanel } from "./CapabilityPanel";
import type { CapabilityProfile } from "../../types/api";

const mockApiFetch = vi.fn();

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

function renderWithQueryClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

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

const PROBED: CapabilityProfile = {
  detected_version: "19.0.0.0.0",
  detected_edition: "SE2",
  is_rac: false,
  statspack_available: true,
  diagnostics_tuning_pack_licensed_confirmed: false,
  privilege_report: { "V$SESSION": true, "V$LOCK": false },
  last_probed_at: "2026-01-01T00:00:00Z",
  cpu_count: 8,
};

describe("CapabilityPanel", () => {
  it("shows a not-yet-probed state and offers to probe when the user can edit", async () => {
    mockApiFetch.mockResolvedValueOnce(UNPROBED);
    renderWithQueryClient(<CapabilityPanel databaseId="db-1" canEdit />);

    expect(await screen.findByText(/not yet probed/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Detect Capabilities" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test Connection" })).toBeInTheDocument();
  });

  it("hides probe actions for a user without edit permission", async () => {
    mockApiFetch.mockResolvedValueOnce(UNPROBED);
    renderWithQueryClient(<CapabilityPanel databaseId="db-1" canEdit={false} />);

    await screen.findByText(/not yet probed/i);
    expect(screen.queryByRole("button", { name: "Detect Capabilities" })).not.toBeInTheDocument();
  });

  it("renders the probe results including a missing-privilege warning", async () => {
    mockApiFetch.mockResolvedValueOnce(PROBED);
    renderWithQueryClient(<CapabilityPanel databaseId="db-1" canEdit />);

    expect(await screen.findByText("SE2")).toBeInTheDocument();
    expect(screen.getByText("19.0.0.0.0")).toBeInTheDocument();
    expect(screen.getAllByText(/V\$LOCK/).length).toBeGreaterThan(0);
    expect(screen.getByText(/cannot read/i)).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
  });

  it("shows CPU cores as Unknown rather than a fabricated number when the probe couldn't read it", async () => {
    mockApiFetch.mockResolvedValueOnce({ ...PROBED, cpu_count: null });
    renderWithQueryClient(<CapabilityPanel databaseId="db-1" canEdit />);

    expect(await screen.findByText("SE2")).toBeInTheDocument();
    expect(screen.getByText("Unknown")).toBeInTheDocument();
  });
});
