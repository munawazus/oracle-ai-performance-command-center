import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { UserMenu } from "./UserMenu";
import { ThemeProvider } from "../../lib/theme-context";
import type { CurrentUser } from "../../types/api";

const mockLogout = vi.fn();
const mockUser: CurrentUser = {
  id: "user-1",
  email: "admin@ocpcc.dev",
  display_name: "Platform Administrator",
  roles: ["ADMIN"],
  permissions: [],
};

vi.mock("../../lib/auth-context", () => ({
  useAuth: () => ({ user: mockUser, logout: mockLogout }),
}));

function renderMenu() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <UserMenu />
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("UserMenu theme control", () => {
  it("marks System as the checked option by default", async () => {
    renderMenu();
    await userEvent.click(screen.getByRole("button", { name: "User menu" }));

    expect(screen.getByRole("menuitemradio", { name: "System" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("menuitemradio", { name: "Light" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("menuitemradio", { name: "Dark" })).toHaveAttribute("aria-checked", "false");
  });

  it("switches the checked option when Dark is clicked, and keeps the menu open", async () => {
    renderMenu();
    await userEvent.click(screen.getByRole("button", { name: "User menu" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "Dark" }));

    expect(screen.getByRole("menuitemradio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("menuitemradio", { name: "System" })).toHaveAttribute("aria-checked", "false");
    // Still open - the menu content (email) is still visible after a theme click.
    expect(screen.getByText("admin@ocpcc.dev")).toBeInTheDocument();
  });

  it("still signs out correctly alongside the new theme control", async () => {
    renderMenu();
    await userEvent.click(screen.getByRole("button", { name: "User menu" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));

    expect(mockLogout).toHaveBeenCalled();
  });
});
