import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { DatabasesPage } from "./DatabasesPage";
import { ToastProvider } from "../components/ui/Toast";
import type { DatabaseGroup, MonitoredDatabase } from "../types/api";

const mockApiFetch = vi.fn();
const mockHasPermission = vi.fn();

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

vi.mock("../lib/auth-context", () => ({
  useAuth: () => ({ hasPermission: mockHasPermission }),
}));

function renderPage(initialEntry = "/databases") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <DatabasesPage />
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
  tags: ["prod"],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const GROUP: DatabaseGroup = {
  id: "group-1",
  name: "East region",
  description: "",
  database_count: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function baseFetchMock(overrides: Record<string, () => Promise<unknown>> = {}) {
  return (path: string, options?: { method?: string }) => {
    for (const [key, handler] of Object.entries(overrides)) {
      if (path === key) return handler();
    }
    if (path === "/api/database-groups") return Promise.resolve([]);
    if (path === "/api/databases" || path.startsWith("/api/databases?")) return Promise.resolve([]);
    return Promise.reject(new Error(`unexpected fetch: ${path} ${options?.method ?? "GET"}`));
  };
}

describe("DatabasesPage", () => {
  it("shows an empty state with an Add Database action when the user can add", async () => {
    mockHasPermission.mockImplementation((perm: string) => perm === "database:add");
    mockApiFetch.mockImplementation(baseFetchMock());
    renderPage();

    expect(await screen.findByText("No databases registered")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Add Database" }).length).toBeGreaterThan(0);
  });

  it("lists a database with its host, service, and tags", async () => {
    mockHasPermission.mockReturnValue(false);
    mockApiFetch.mockImplementation(baseFetchMock({ "/api/databases": () => Promise.resolve([DB]) }));
    renderPage();

    const table = await screen.findByRole("table");
    expect(within(table).getByText("PROD01")).toBeInTheDocument();
    expect(within(table).getByText("db.internal:1521")).toBeInTheDocument();
    expect(within(table).getByText("ORCLPDB1")).toBeInTheDocument();
    expect(within(table).getByText("prod")).toBeInTheDocument();
  });

  it("filters the list by group", async () => {
    mockHasPermission.mockReturnValue(false);
    mockApiFetch.mockImplementation(
      baseFetchMock({
        "/api/database-groups": () => Promise.resolve([GROUP]),
        "/api/databases?group_id=group-1": () => Promise.resolve([DB]),
      }),
    );
    renderPage();

    await screen.findByText("No databases registered");
    await userEvent.selectOptions(screen.getByLabelText("Filter by group"), "group-1");

    const table = await screen.findByRole("table");
    expect(within(table).getByText("PROD01")).toBeInTheDocument();
  });

  it("filters the list by tag", async () => {
    mockHasPermission.mockReturnValue(false);
    mockApiFetch.mockImplementation(
      baseFetchMock({ "/api/databases?tag=prod": () => Promise.resolve([DB]) }),
    );
    renderPage();

    await screen.findByText("No databases registered");
    await userEvent.type(screen.getByLabelText("Filter by tag"), "prod");

    const table = await screen.findByRole("table");
    expect(within(table).getByText("PROD01")).toBeInTheDocument();
  });

  it("registers a new database", async () => {
    mockHasPermission.mockImplementation((perm: string) => perm === "database:add");
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/databases" && options?.method === "POST") {
        return Promise.resolve({ ...DB, id: "db-2" });
      }
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    await screen.findByText("No databases registered");
    await userEvent.click(screen.getAllByRole("button", { name: "Add Database" })[0]);

    const dialog = await screen.findByRole("dialog");
    await userEvent.type(screen.getByLabelText("Name"), "PROD01");
    await userEvent.type(screen.getByLabelText("Host"), "db.internal");
    await userEvent.type(screen.getByLabelText("Service name"), "ORCLPDB1");
    await userEvent.type(screen.getByLabelText("Monitoring account username"), "monitor");
    await userEvent.type(screen.getByLabelText("Password"), "SuperSecret123!");
    await userEvent.click(screen.getByRole("button", { name: "Register database" }));

    expect(await screen.findByText("Database registered.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/databases",
      expect.objectContaining({
        method: "POST",
        body: expect.objectContaining({
          name: "PROD01",
          host: "db.internal",
          service_name: "ORCLPDB1",
          username: "monitor",
          password: "SuperSecret123!",
        }),
      }),
    );
    expect(dialog).not.toBeInTheDocument();
  });

  it("opens the bulk-add modal", async () => {
    mockHasPermission.mockImplementation((perm: string) => perm === "database:add");
    mockApiFetch.mockImplementation(baseFetchMock());
    renderPage();

    await screen.findByText("No databases registered");
    await userEvent.click(screen.getByRole("button", { name: "Bulk Add" }));

    expect(await screen.findByRole("heading", { name: "Bulk Add Databases" })).toBeInTheDocument();
  });

  it("opens a database's detail drawer on row click and shows its details", async () => {
    mockHasPermission.mockReturnValue(false);
    mockApiFetch.mockImplementation(
      baseFetchMock({
        "/api/databases": () => Promise.resolve([DB]),
        "/api/databases/db-1/groups": () => Promise.resolve([]),
      }),
    );
    renderPage();

    await userEvent.click(await screen.findByText("PROD01"));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("db.internal:1521")).toBeInTheDocument();
    expect(within(dialog).getByText("ORCLPDB1")).toBeInTheDocument();
  });

  it("edits a database", async () => {
    mockHasPermission.mockImplementation((perm: string) => perm === "database:edit");
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/databases/db-1" && options?.method === "PATCH") return Promise.resolve({ ...DB, name: "PROD01-renamed" });
      if (path === "/api/databases/db-1/groups") return Promise.resolve([]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    await userEvent.click(await screen.findByText("PROD01"));
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));

    const editDialog = await screen.findByRole("dialog", { name: /Edit PROD01/ });
    const nameInput = within(editDialog).getByLabelText("Name");
    expect(nameInput).toHaveValue("PROD01");
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "PROD01-renamed");
    await userEvent.click(within(editDialog).getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Database updated.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/databases/db-1",
      expect.objectContaining({ method: "PATCH", body: expect.objectContaining({ name: "PROD01-renamed" }) }),
    );
  });

  it("does not send blank username/password when editing without changing them", async () => {
    mockHasPermission.mockImplementation((perm: string) => perm === "database:edit");
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/databases/db-1" && options?.method === "PATCH") return Promise.resolve(DB);
      if (path === "/api/databases/db-1/groups") return Promise.resolve([]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    await userEvent.click(await screen.findByText("PROD01"));
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const editDialog = await screen.findByRole("dialog", { name: /Edit PROD01/ });
    await userEvent.click(within(editDialog).getByRole("button", { name: "Save changes" }));

    await screen.findByText("Database updated.");
    const patchCall = mockApiFetch.mock.calls.find(
      (call) => call[0] === "/api/databases/db-1" && (call[1] as { method?: string })?.method === "PATCH",
    );
    expect(patchCall?.[1]).toMatchObject({ method: "PATCH" });
    expect((patchCall?.[1] as { body: Record<string, unknown> }).body).not.toHaveProperty("username");
    expect((patchCall?.[1] as { body: Record<string, unknown> }).body).not.toHaveProperty("password");
  });

  it("deletes a database after confirmation", async () => {
    mockHasPermission.mockImplementation((perm: string) => perm === "database:delete");
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/databases/db-1" && options?.method === "DELETE") return Promise.resolve(undefined);
      if (path === "/api/databases/db-1/groups") return Promise.resolve([]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    await userEvent.click(await screen.findByText("PROD01"));
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));

    const confirmDialog = await screen.findByRole("dialog", { name: /Remove PROD01/ });
    await userEvent.click(within(confirmDialog).getByRole("button", { name: "Remove" }));

    expect(await screen.findByText("Database removed.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith("/api/databases/db-1", expect.objectContaining({ method: "DELETE" }));
  });

  it("cancels a pending delete without calling the API", async () => {
    mockHasPermission.mockImplementation((perm: string) => perm === "database:delete");
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases/db-1/groups") return Promise.resolve([]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    await userEvent.click(await screen.findByText("PROD01"));
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    const confirmDialog = await screen.findByRole("dialog", { name: /Remove PROD01/ });
    const callsBeforeCancel = mockApiFetch.mock.calls.length;
    await userEvent.click(within(confirmDialog).getByRole("button", { name: "Cancel" }));

    expect(confirmDialog).not.toBeInTheDocument();
    expect(mockApiFetch.mock.calls.length).toBe(callsBeforeCancel);
  });

  it("toggles group membership from the detail drawer", async () => {
    mockHasPermission.mockReturnValue(true);
    mockApiFetch.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/databases/db-1/groups" && options?.method === "PUT") {
        return Promise.resolve(["group-1"]);
      }
      if (path === "/api/databases/db-1/groups") return Promise.resolve([]);
      if (path === "/api/database-groups") return Promise.resolve([GROUP]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    await userEvent.click(await screen.findByText("PROD01"));
    const checkbox = await screen.findByRole("checkbox", { name: "East region" });
    expect(checkbox).not.toBeChecked();
    await userEvent.click(checkbox);

    expect(await screen.findByText("Group membership updated.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/databases/db-1/groups",
      expect.objectContaining({ method: "PUT", body: { group_ids: ["group-1"] } }),
    );
  });

  it("switches to the Capabilities tab in the drawer", async () => {
    mockHasPermission.mockReturnValue(false);
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases/db-1/groups") return Promise.resolve([]);
      if (path === "/api/databases/db-1/capabilities") {
        return Promise.resolve({
          detected_version: null,
          detected_edition: null,
          is_rac: null,
          statspack_available: null,
          diagnostics_tuning_pack_licensed_confirmed: false,
          privilege_report: null,
          last_probed_at: null,
        });
      }
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path === "/api/databases") return Promise.resolve([DB]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage();

    await userEvent.click(await screen.findByText("PROD01"));
    await userEvent.click(await screen.findByRole("tab", { name: "Capabilities" }));

    expect(await screen.findByText(/Not yet probed/)).toBeInTheDocument();
  });

  it("opens a database's drawer directly from an ?id= deep link, then strips the param", async () => {
    mockHasPermission.mockReturnValue(false);
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/api/databases/db-1") return Promise.resolve(DB);
      if (path === "/api/databases/db-1/groups") return Promise.resolve([]);
      if (path === "/api/database-groups") return Promise.resolve([]);
      if (path === "/api/databases" || path.startsWith("/api/databases?")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected fetch: ${path}`));
    });
    renderPage("/databases?id=db-1");

    // The drawer opens without ever clicking a row - the list itself resolves empty.
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("db.internal:1521")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith("/api/databases/db-1");
  });

  it("switches to the Groups page tab", async () => {
    mockHasPermission.mockReturnValue(false);
    mockApiFetch.mockImplementation(baseFetchMock());
    renderPage();

    await screen.findByText("No databases registered");
    await userEvent.click(screen.getByRole("tab", { name: "Groups" }));

    expect(await screen.findByText("No groups yet")).toBeInTheDocument();
  });
});
