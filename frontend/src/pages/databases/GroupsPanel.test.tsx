import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { GroupsPanel } from "./GroupsPanel";
import { ToastProvider } from "../../components/ui/Toast";
import type { DatabaseGroup } from "../../types/api";

const mockApiFetch = vi.fn();

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

function renderPanel(canManage = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <GroupsPanel canManage={canManage} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const GROUP: DatabaseGroup = {
  id: "grp-1",
  name: "Production",
  description: "Prod fleet",
  database_count: 3,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("GroupsPanel", () => {
  it("shows an empty state when no groups exist", async () => {
    mockApiFetch.mockResolvedValue([]);
    renderPanel();

    expect(await screen.findByText("No groups yet")).toBeInTheDocument();
  });

  it("lists a group with its database count", async () => {
    mockApiFetch.mockResolvedValue([GROUP]);
    renderPanel();

    expect(await screen.findByText("Production")).toBeInTheDocument();
    expect(screen.getByText("Prod fleet")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("hides edit/delete controls for a user without manage permission", async () => {
    mockApiFetch.mockResolvedValue([GROUP]);
    renderPanel(false);

    await screen.findByText("Production");
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New Group" })).not.toBeInTheDocument();
  });

  it("creates a new group", async () => {
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/database-groups" && options?.method === "POST") {
        return Promise.resolve({ ...GROUP, id: "grp-2", name: "Staging", description: "", database_count: 0 });
      }
      if (path === "/api/database-groups") return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPanel();

    await screen.findByText("No groups yet");
    await userEvent.click(screen.getAllByRole("button", { name: "New Group" })[0]);
    await userEvent.type(screen.getByLabelText("Name"), "Staging");
    await userEvent.click(screen.getByRole("button", { name: "Create group" }));

    expect(await screen.findByText("Group created.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/database-groups",
      expect.objectContaining({ method: "POST", body: { name: "Staging", description: "" } }),
    );
  });

  it("deletes a group after confirmation", async () => {
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/database-groups/grp-1" && options?.method === "DELETE") return Promise.resolve(undefined);
      if (path === "/api/database-groups") return Promise.resolve([GROUP]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPanel();

    await screen.findByText("Production");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    expect(await screen.findByText("Group deleted.")).toBeInTheDocument();
  });
});
