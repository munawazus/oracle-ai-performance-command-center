import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AdministrationPage } from "./AdministrationPage";
import { ToastProvider } from "../components/ui/Toast";

const mockApiFetch = vi.fn();
const mockHasPermission = vi.fn();

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

vi.mock("../lib/auth-context", () => ({
  useAuth: () => ({ hasPermission: mockHasPermission }),
}));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <AdministrationPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("AdministrationPage", () => {
  it("shows a fallback when the user has none of the admin permissions", () => {
    mockHasPermission.mockReturnValue(false);
    renderPage();

    expect(screen.getByText("Nothing to administer")).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("only shows the Users tab for a user with just admin:users", async () => {
    mockHasPermission.mockImplementation((perm: string) => perm === "admin:users");
    mockApiFetch.mockResolvedValue([]);
    renderPage();

    expect(screen.getByRole("tab", { name: "Users" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Notifications" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Integrations" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Audit Log" })).not.toBeInTheDocument();
    expect(await screen.findByText("No users yet")).toBeInTheDocument();
  });

  it("shows all four tabs for a user with every admin permission, defaulting to the first", async () => {
    mockHasPermission.mockReturnValue(true);
    mockApiFetch.mockResolvedValue([]);
    renderPage();

    expect(screen.getByRole("tab", { name: "Users" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Notifications" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Integrations" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Audit Log" })).toBeInTheDocument();
    expect(await screen.findByText("No users yet")).toBeInTheDocument();
  });

  it("switches panels when a different tab is clicked", async () => {
    mockHasPermission.mockReturnValue(true);
    mockApiFetch.mockResolvedValue([]);
    renderPage();

    await screen.findByText("No users yet");
    await userEvent.click(screen.getByRole("tab", { name: "Audit Log" }));

    expect(await screen.findByText("No matching audit events")).toBeInTheDocument();
    expect(screen.queryByText("No users yet")).not.toBeInTheDocument();
  });
});
