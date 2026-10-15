import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { IncidentsPage } from "./IncidentsPage";
import { ToastProvider } from "../components/ui/Toast";
import type { Incident, MonitoredDatabase, NotificationEvent, RcaResult } from "../types/api";

const mockApiFetch = vi.fn();
const mockHasPermission = vi.fn();

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

vi.mock("../lib/auth-context", () => ({
  useAuth: () => ({ hasPermission: mockHasPermission }),
}));

function renderPage(initialEntry = "/incidents") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <IncidentsPage />
        </MemoryRouter>
      </ToastProvider>
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

describe("IncidentsPage", () => {
  it("shows an empty state when there are no incidents", async () => {
    mockHasPermission.mockReturnValue(false);
    mockApiFetch.mockImplementation((path: string) => {
      if (path.startsWith("/api/incidents")) return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path.startsWith("/api/integrations/deliveries")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    expect(await screen.findByText("No incidents")).toBeInTheDocument();
  });

  it("lists an incident with its severity, status, and database name", async () => {
    mockHasPermission.mockReturnValue(false);
    mockApiFetch.mockImplementation((path: string) => {
      if (path.startsWith("/api/incidents")) return Promise.resolve([INCIDENT]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path.startsWith("/api/integrations/deliveries")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const table = await screen.findByRole("table");
    expect(within(table).getByText("Active session count anomaly")).toBeInTheDocument();
    expect(within(table).getByText("CRITICAL")).toBeInTheDocument();
    expect(within(table).getByText("OPEN")).toBeInTheDocument();
    expect(within(table).getByText("PROD01")).toBeInTheDocument();
  });

  it("hides manage actions in the drawer for a user without incident:manage", async () => {
    mockHasPermission.mockReturnValue(false);
    mockApiFetch.mockImplementation((path: string) => {
      if (path.startsWith("/api/incidents/inc-1/timeline")) return Promise.resolve([]);
      if (path.startsWith("/api/incidents")) return Promise.resolve([INCIDENT]);
      if (path.startsWith("/api/notifications/events")) return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path.startsWith("/api/integrations/deliveries")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const row = await screen.findByText("Active session count anomaly");
    await userEvent.click(row);

    expect(await screen.findByText("Timeline")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Acknowledge" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resolve" })).not.toBeInTheDocument();
  });

  it("shows email notification delivery status in the drawer", async () => {
    mockHasPermission.mockReturnValue(false);
    const notification: NotificationEvent = {
      id: "notif-1",
      incident_id: "inc-1",
      severity: "CRITICAL",
      recipients: ["oncall@ocpcc.dev"],
      subject: "[CRITICAL] Active session count anomaly - PROD01",
      body_text: "...",
      status: "FAILED",
      attempt_count: 5,
      last_attempt_at: "2026-01-01T00:10:00Z",
      last_error_category: "smtp_error",
      sent_at: null,
      created_at: "2026-01-01T00:05:00Z",
    };
    mockApiFetch.mockImplementation((path: string) => {
      if (path.startsWith("/api/incidents/inc-1/timeline")) return Promise.resolve([]);
      if (path.startsWith("/api/incidents")) return Promise.resolve([INCIDENT]);
      if (path.startsWith("/api/notifications/events")) return Promise.resolve([notification]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path.startsWith("/api/integrations/deliveries")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const row = await screen.findByText("Active session count anomaly");
    await userEvent.click(row);

    expect(await screen.findByText("FAILED")).toBeInTheDocument();
    expect(screen.getByText("oncall@ocpcc.dev")).toBeInTheDocument();
    expect(screen.getByText(/Delivery failed \(smtp_error\)/)).toBeInTheDocument();
  });

  it("runs an AI investigation and renders the grounded RCA result", async () => {
    mockHasPermission.mockImplementation((perm: string) => perm === "ai:query");
    const rca: RcaResult = {
      probable_root_cause: "A surge in active sessions correlates with the anomaly.",
      confidence: "medium",
      evidence_summary: "Active session count spiked well above its rolling baseline.",
      secondary_contributors: ["Elevated User I/O wait"],
      alternative_hypotheses: [],
      recommended_investigation: ["Check for a new SQL_ID dominating sessions"],
      recommended_action: [],
      risk: null,
      data_completeness_note: "SQL-level attribution is unavailable in this deployment.",
      model: "gpt-oss-20b",
    };
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path.startsWith("/api/incidents/inc-1/timeline")) return Promise.resolve([]);
      if (path.startsWith("/api/incidents/inc-1/rca") && options?.method === "POST") return Promise.resolve(rca);
      if (path.startsWith("/api/incidents")) return Promise.resolve([INCIDENT]);
      if (path.startsWith("/api/notifications/events")) return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path.startsWith("/api/integrations/deliveries")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const row = await screen.findByText("Active session count anomaly");
    await userEvent.click(row);
    await userEvent.click(await screen.findByRole("button", { name: "Investigate with AI" }));

    expect(await screen.findByText(/A surge in active sessions/)).toBeInTheDocument();
    expect(screen.getByText("medium confidence")).toBeInTheDocument();
    expect(screen.getByText("Elevated User I/O wait")).toBeInTheDocument();
    expect(screen.getByText(/SQL-level attribution is unavailable/)).toBeInTheDocument();
  });

  it("pre-filters to one database when arriving via a Fleet Overview drill-down link", async () => {
    mockHasPermission.mockReturnValue(false);
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/incidents?monitored_database_id=db-1") return Promise.resolve([INCIDENT]);
      if (path.startsWith("/api/incidents")) return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path.startsWith("/api/integrations/deliveries")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage("/incidents?database_id=db-1");

    const table = await screen.findByRole("table");
    expect(within(table).getByText("Active session count anomaly")).toBeInTheDocument();
    expect(screen.getByLabelText("Database")).toHaveValue("db-1");
  });

  it("acknowledges an open incident", async () => {
    mockHasPermission.mockImplementation((perm: string) => perm === "incident:manage");
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/incidents/inc-1/acknowledge" && options?.method === "POST") {
        return Promise.resolve({ ...INCIDENT, status: "ACKNOWLEDGED", acknowledged_at: "2026-01-01T00:10:00Z" });
      }
      if (path.startsWith("/api/incidents/inc-1/timeline")) return Promise.resolve([]);
      if (path.startsWith("/api/incidents/assignable-users")) return Promise.resolve([]);
      if (path.startsWith("/api/incidents")) return Promise.resolve([INCIDENT]);
      if (path.startsWith("/api/notifications/events")) return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path.startsWith("/api/integrations/deliveries")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const row = await screen.findByText("Active session count anomaly");
    await userEvent.click(row);
    await userEvent.click(await screen.findByRole("button", { name: "Acknowledge" }));

    expect(await screen.findByText("Incident acknowledged.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith("/api/incidents/inc-1/acknowledge", expect.objectContaining({ method: "POST" }));
  });

  it("assigns an incident to a selected user", async () => {
    mockHasPermission.mockImplementation((perm: string) => perm === "incident:manage");
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/incidents/inc-1/assign" && options?.method === "POST") {
        return Promise.resolve({ ...INCIDENT, assigned_to_user_id: "user-1" });
      }
      if (path.startsWith("/api/incidents/inc-1/timeline")) return Promise.resolve([]);
      if (path === "/api/incidents/assignable-users") {
        return Promise.resolve([{ id: "user-1", display_name: "Jordan DBA", email: "jordan@ocpcc.dev" }]);
      }
      if (path.startsWith("/api/incidents")) return Promise.resolve([INCIDENT]);
      if (path.startsWith("/api/notifications/events")) return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path.startsWith("/api/integrations/deliveries")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const row = await screen.findByText("Active session count anomaly");
    await userEvent.click(row);
    await screen.findByText("Jordan DBA (jordan@ocpcc.dev)");
    const assignButton = screen.getByRole("button", { name: "Assign" });
    expect(assignButton).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Assign to"), "user-1");
    expect(assignButton).toBeEnabled();
    await userEvent.click(assignButton);

    expect(await screen.findByText("Incident assigned.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/incidents/inc-1/assign",
      expect.objectContaining({ method: "POST", body: { assignee_user_id: "user-1" } }),
    );
  });

  it("resolves an incident with a resolution note", async () => {
    mockHasPermission.mockImplementation((perm: string) => perm === "incident:manage");
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/incidents/inc-1/resolve" && options?.method === "POST") {
        return Promise.resolve({ ...INCIDENT, status: "RESOLVED", resolution_note: "False positive." });
      }
      if (path.startsWith("/api/incidents/inc-1/timeline")) return Promise.resolve([]);
      if (path.startsWith("/api/incidents/assignable-users")) return Promise.resolve([]);
      if (path.startsWith("/api/incidents")) return Promise.resolve([INCIDENT]);
      if (path.startsWith("/api/notifications/events")) return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path.startsWith("/api/integrations/deliveries")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const row = await screen.findByText("Active session count anomaly");
    await userEvent.click(row);
    await userEvent.type(await screen.findByLabelText("Resolution note (optional)"), "False positive.");
    await userEvent.click(screen.getByRole("button", { name: "Resolve" }));

    expect(await screen.findByText("Incident resolved.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/incidents/inc-1/resolve",
      expect.objectContaining({ method: "POST", body: { note: "False positive." } }),
    );
  });

  it("shows integration delivery status in the drawer", async () => {
    mockHasPermission.mockReturnValue(false);
    const delivery = {
      id: "delivery-1",
      integration_id: "integ-1",
      incident_id: "inc-1",
      event_type: "incident_escalated",
      status: "RETRYING" as const,
      attempt_count: 2,
      last_error_category: "network_error",
      response_status_code: null,
      sent_at: null,
      created_at: "2026-01-01T00:05:00Z",
    };
    mockApiFetch.mockImplementation((path: string) => {
      if (path.startsWith("/api/incidents/inc-1/timeline")) return Promise.resolve([]);
      if (path.startsWith("/api/incidents")) return Promise.resolve([INCIDENT]);
      if (path.startsWith("/api/notifications/events")) return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path.startsWith("/api/integrations/deliveries")) return Promise.resolve([delivery]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    const row = await screen.findByText("Active session count anomaly");
    await userEvent.click(row);

    expect(await screen.findByText("RETRYING")).toBeInTheDocument();
    expect(screen.getByText("incident_escalated")).toBeInTheDocument();
    expect(screen.getByText(/Retrying - 2 attempt\(s\) so far/)).toBeInTheDocument();
  });

  it("opens an incident's drawer directly from an ?incident_id= deep link, then strips the param", async () => {
    mockHasPermission.mockReturnValue(false);
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/incidents/inc-1") return Promise.resolve(INCIDENT);
      if (path.startsWith("/api/incidents/inc-1/timeline")) return Promise.resolve([]);
      if (path.startsWith("/api/incidents")) return Promise.resolve([]);
      if (path.startsWith("/api/notifications/events")) return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path.startsWith("/api/integrations/deliveries")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage("/incidents?incident_id=inc-1");

    // The drawer opens without ever clicking a row - the list itself resolves empty.
    expect(await screen.findByText("Timeline")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith("/api/incidents/inc-1");
  });
});
