import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { Incident, MonitoredDatabase } from "../../types/api";
import { GlobalSearch } from "./GlobalSearch";

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

const DB: MonitoredDatabase = {
  id: "db-1",
  name: "PROD01",
  host: "db.internal",
  port: 1521,
  service_name: "ORCLPDB1",
  edition_hint: "SE2",
  is_active: true,
  tags: ["east"],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const INCIDENT: Incident = {
  id: "inc-1",
  monitored_database_id: "db-1",
  status: "OPEN",
  severity: "CRITICAL",
  title: "Active session count anomaly",
  opened_at: "2026-01-01T00:00:00Z",
  last_evidence_at: "2026-01-01T00:05:00Z",
  acknowledged_at: null,
  acknowledged_by_user_id: null,
  assigned_to_user_id: null,
  resolved_at: null,
  resolved_by_user_id: null,
  resolution_note: null,
};

function renderSearch() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  mockApiFetch.mockImplementation((path: string) => {
    if (path === "/api/databases") return Promise.resolve([DB]);
    if (path === "/api/incidents") return Promise.resolve([INCIDENT]);
    return Promise.reject(new Error(`unexpected fetch: ${path}`));
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <GlobalSearch />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("GlobalSearch", () => {
  it("shows only the matching database when typing a database-name substring", async () => {
    renderSearch();
    await userEvent.click(screen.getByRole("button", { name: /search databases and incidents/i }));
    await userEvent.type(screen.getByPlaceholderText("Search databases or incidents…"), "prod01");

    expect(await screen.findByText("PROD01")).toBeInTheDocument();
    expect(screen.queryByText("Active session count anomaly")).not.toBeInTheDocument();
  });

  it("shows only the matching incident when typing a title substring", async () => {
    renderSearch();
    await userEvent.click(screen.getByRole("button", { name: /search databases and incidents/i }));
    await userEvent.type(screen.getByPlaceholderText("Search databases or incidents…"), "session count");

    expect(await screen.findByText("Active session count anomaly")).toBeInTheDocument();
    expect(screen.queryByText("PROD01")).not.toBeInTheDocument();
  });

  it("navigates to the database's deep link when a database result is clicked", async () => {
    renderSearch();
    await userEvent.click(screen.getByRole("button", { name: /search databases and incidents/i }));
    await userEvent.type(screen.getByPlaceholderText("Search databases or incidents…"), "prod01");
    await userEvent.click(await screen.findByText("PROD01"));

    expect(mockNavigate).toHaveBeenCalledWith("/databases?id=db-1");
  });

  it("navigates to the incident's deep link when an incident result is clicked", async () => {
    renderSearch();
    await userEvent.click(screen.getByRole("button", { name: /search databases and incidents/i }));
    await userEvent.type(screen.getByPlaceholderText("Search databases or incidents…"), "anomaly");
    await userEvent.click(await screen.findByText("Active session count anomaly"));

    expect(mockNavigate).toHaveBeenCalledWith("/incidents?incident_id=inc-1");
  });
});
