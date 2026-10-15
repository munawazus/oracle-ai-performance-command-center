import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { IntegrationsPanel } from "./IntegrationsPanel";
import { ToastProvider } from "../../components/ui/Toast";
import type { Integration, IntegrationDelivery, TestIntegrationResult } from "../../types/api";

const mockApiFetch = vi.fn();

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <IntegrationsPanel />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const INTEGRATION: Integration = {
  id: "integ-1",
  type: "SLACK",
  name: "Ops Slack",
  target_url: "https://hooks.slack.invalid/services/x/y/z",
  has_secret: true,
  enabled: true,
  trigger_severities: ["HIGH", "CRITICAL"],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("IntegrationsPanel", () => {
  it("shows an empty state when no integrations exist", async () => {
    mockApiFetch.mockResolvedValue([]);
    renderPanel();

    expect(await screen.findByText("No integrations configured")).toBeInTheDocument();
  });

  it("lists an integration with its type, target, and trigger severities", async () => {
    mockApiFetch.mockResolvedValue([INTEGRATION]);
    renderPanel();

    expect(await screen.findByText("Ops Slack")).toBeInTheDocument();
    expect(screen.getByText("Slack")).toBeInTheDocument();
    expect(screen.getByText(INTEGRATION.target_url)).toBeInTheDocument();
    expect(screen.getByText("HIGH")).toBeInTheDocument();
    expect(screen.getByText("CRITICAL")).toBeInTheDocument();
    expect(screen.getByText("Enabled")).toBeInTheDocument();
  });

  it("creates a new webhook integration", async () => {
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/integrations" && options?.method === "POST") {
        return Promise.resolve({ ...INTEGRATION, id: "integ-2", type: "WEBHOOK", name: "Ops Webhook" });
      }
      if (path === "/api/integrations") return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPanel();

    await screen.findByText("No integrations configured");
    await userEvent.click(screen.getAllByRole("button", { name: "New Integration" })[0]);
    await userEvent.type(screen.getByLabelText("Name"), "Ops Webhook");
    await userEvent.type(screen.getByLabelText("Target URL"), "https://example.invalid/hook");
    await userEvent.click(screen.getByRole("button", { name: "Create integration" }));

    expect(await screen.findByText("Integration created.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/integrations",
      expect.objectContaining({
        method: "POST",
        body: expect.objectContaining({ name: "Ops Webhook", target_url: "https://example.invalid/hook" }),
      }),
    );
  });

  it("tests an integration and reports the result", async () => {
    const result: TestIntegrationResult = { ok: false, status_code: null, message: "simulated failure" };
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/integrations/integ-1/test" && options?.method === "POST") return Promise.resolve(result);
      if (path === "/api/integrations") return Promise.resolve([INTEGRATION]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPanel();

    await screen.findByText("Ops Slack");
    await userEvent.click(screen.getByRole("button", { name: "Test" }));

    expect(await screen.findByText(/Test delivery failed: simulated failure/)).toBeInTheDocument();
  });

  it("opens delivery history for an integration", async () => {
    const delivery: IntegrationDelivery = {
      id: "delivery-1",
      integration_id: "integ-1",
      incident_id: "inc-1",
      event_type: "incident_escalated",
      status: "SENT",
      attempt_count: 1,
      last_error_category: null,
      response_status_code: 200,
      sent_at: "2026-01-01T00:10:00Z",
      created_at: "2026-01-01T00:05:00Z",
    };
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/integrations/deliveries?integration_id=integ-1") return Promise.resolve([delivery]);
      if (path === "/api/integrations") return Promise.resolve([INTEGRATION]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPanel();

    await screen.findByText("Ops Slack");
    await userEvent.click(screen.getByRole("button", { name: "History" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("SENT")).toBeInTheDocument();
    expect(within(dialog).getByText("incident_escalated")).toBeInTheDocument();
  });

  it("deletes an integration after confirmation", async () => {
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/integrations/integ-1" && options?.method === "DELETE") return Promise.resolve(undefined);
      if (path === "/api/integrations") return Promise.resolve([INTEGRATION]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPanel();

    await screen.findByText("Ops Slack");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    expect(await screen.findByText("Integration deleted.")).toBeInTheDocument();
  });
});
