import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { NotificationsPanel } from "./NotificationsPanel";
import { ToastProvider } from "../../components/ui/Toast";
import type { NotificationSettings } from "../../types/api";

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
        <NotificationsPanel />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const SETTINGS: NotificationSettings = {
  smtp_host: "smtp.internal.ocpcc.dev",
  smtp_port: 587,
  smtp_use_tls: true,
  smtp_username: "alerts",
  has_smtp_password: true,
  sender_email: "alerts@ocpcc.dev",
  sender_name: "OCPCC Alerts",
  default_recipients: ["oncall@ocpcc.dev"],
  escalation_recipients: [],
};

describe("NotificationsPanel", () => {
  it("populates the form from the loaded settings without exposing the password", async () => {
    mockApiFetch.mockResolvedValue(SETTINGS);
    renderPanel();

    expect(await screen.findByDisplayValue("smtp.internal.ocpcc.dev")).toBeInTheDocument();
    expect(screen.getByDisplayValue("oncall@ocpcc.dev")).toBeInTheDocument();
    expect(screen.getByLabelText(/leave blank to keep current/i)).toHaveValue("");
  });

  it("saves updated settings via a PUT request", async () => {
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/notifications/settings" && options?.method === "PUT") {
        return Promise.resolve({ ...SETTINGS, sender_name: "New Name" });
      }
      if (path === "/api/notifications/settings") return Promise.resolve(SETTINGS);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPanel();

    await screen.findByDisplayValue("smtp.internal.ocpcc.dev");
    const saveButton = screen.getByRole("button", { name: "Save settings" });
    await userEvent.click(saveButton);

    expect(await screen.findByText("Notification settings saved.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/notifications/settings",
      expect.objectContaining({ method: "PUT" }),
    );
  });
});
