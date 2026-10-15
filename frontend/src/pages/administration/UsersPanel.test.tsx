import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { UsersPanel } from "./UsersPanel";
import { ToastProvider } from "../../components/ui/Toast";
import type { AdminUser } from "../../types/api";

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
        <UsersPanel />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const USER: AdminUser = {
  id: "user-1",
  email: "jordan@ocpcc.dev",
  display_name: "Jordan DBA",
  roles: ["VIEWER"],
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
};

describe("UsersPanel", () => {
  it("shows an empty state when there are no users", async () => {
    mockApiFetch.mockResolvedValue([]);
    renderPanel();

    expect(await screen.findByText("No users yet")).toBeInTheDocument();
  });

  it("lists a user with their roles and status", async () => {
    mockApiFetch.mockResolvedValue([USER]);
    renderPanel();

    expect(await screen.findByText("Jordan DBA")).toBeInTheDocument();
    expect(screen.getByText("jordan@ocpcc.dev")).toBeInTheDocument();
    expect(screen.getByText("VIEWER")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("creates a new user with selected roles", async () => {
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/admin/users" && options?.method === "POST") {
        return Promise.resolve({ ...USER, id: "user-2", roles: ["OPERATOR"] });
      }
      if (path === "/api/admin/users") return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPanel();

    await screen.findByText("No users yet");
    await userEvent.click(screen.getByRole("button", { name: "Create User" }));

    const dialog = await screen.findByRole("dialog");
    await userEvent.type(screen.getByLabelText("Email"), "new.user@ocpcc.dev");
    await userEvent.type(screen.getByLabelText("Display name"), "New User");
    await userEvent.type(screen.getByLabelText("Password"), "a-very-long-password");
    await userEvent.click(screen.getAllByRole("checkbox", { name: "OPERATOR" })[0]);
    await userEvent.click(screen.getByRole("button", { name: "Create user" }));

    expect(await screen.findByText("User created.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/admin/users",
      expect.objectContaining({
        method: "POST",
        body: {
          email: "new.user@ocpcc.dev",
          display_name: "New User",
          password: "a-very-long-password",
          roles: ["OPERATOR"],
        },
      }),
    );
    expect(dialog).not.toBeInTheDocument();
  });

  it("updates a user's roles", async () => {
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/admin/users/user-1/roles" && options?.method === "PATCH") {
        return Promise.resolve({ ...USER, roles: ["VIEWER", "OPERATOR"] });
      }
      if (path === "/api/admin/users") return Promise.resolve([USER]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPanel();

    await screen.findByText("Jordan DBA");
    await userEvent.click(screen.getByRole("button", { name: "Edit roles" }));

    const dialog = await screen.findByRole("dialog");
    expect(screen.getByRole("checkbox", { name: "VIEWER" })).toBeChecked();
    await userEvent.click(screen.getByRole("checkbox", { name: "OPERATOR" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Roles updated.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/admin/users/user-1/roles",
      expect.objectContaining({ method: "PATCH", body: { roles: ["VIEWER", "OPERATOR"] } }),
    );
    expect(dialog).not.toBeInTheDocument();
  });

  it("disables an active user", async () => {
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/admin/users/user-1/status" && options?.method === "PATCH") {
        return Promise.resolve({ ...USER, is_active: false });
      }
      if (path === "/api/admin/users") return Promise.resolve([USER]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPanel();

    await screen.findByText("Jordan DBA");
    await userEvent.click(screen.getByRole("button", { name: "Disable" }));

    expect(await screen.findByText("User status updated.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/admin/users/user-1/status",
      expect.objectContaining({ method: "PATCH", body: { is_active: false } }),
    );
  });

  it("re-enables a disabled user", async () => {
    const disabledUser = { ...USER, is_active: false };
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/admin/users/user-1/status" && options?.method === "PATCH") {
        return Promise.resolve({ ...disabledUser, is_active: true });
      }
      if (path === "/api/admin/users") return Promise.resolve([disabledUser]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPanel();

    await screen.findByText("Disabled");
    await userEvent.click(screen.getByRole("button", { name: "Enable" }));

    expect(await screen.findByText("User status updated.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/admin/users/user-1/status",
      expect.objectContaining({ method: "PATCH", body: { is_active: true } }),
    );
  });

  it("cancels creating a user without submitting", async () => {
    mockApiFetch.mockResolvedValue([]);
    renderPanel();

    await screen.findByText("No users yet");
    const callsBeforeCancel = mockApiFetch.mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: "Create User" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(dialog).not.toBeInTheDocument();
    expect(mockApiFetch.mock.calls.length).toBe(callsBeforeCancel);
  });
});
