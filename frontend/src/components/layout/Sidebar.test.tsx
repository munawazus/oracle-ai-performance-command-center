import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { Sidebar } from "./Sidebar";

const mockHasPermission = vi.fn();

vi.mock("../../lib/auth-context", () => ({
  useAuth: () => ({ hasPermission: mockHasPermission }),
}));

describe("Sidebar", () => {
  it("always shows nav items with no permission requirement", () => {
    mockHasPermission.mockReturnValue(false);
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Administration" })).toBeInTheDocument();
  });

  it("hides permission-gated items the user lacks", () => {
    mockHasPermission.mockReturnValue(false);
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("link", { name: "Databases" })).not.toBeInTheDocument();
  });

  it("shows permission-gated items the user holds", () => {
    mockHasPermission.mockReturnValue(true);
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: "Databases" })).toBeInTheDocument();
  });

  it("renders the three section headers in order, with Administration last and ungrouped", () => {
    mockHasPermission.mockReturnValue(true);
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );
    const headings = screen.getAllByText(/^(Monitoring|Intelligence|Response)$/);
    expect(headings.map((h) => h.textContent)).toEqual(["Monitoring", "Intelligence", "Response"]);

    const nav = screen.getByRole("navigation", { name: "Primary" });
    const allText = nav.textContent ?? "";
    expect(allText.indexOf("Administration")).toBeGreaterThan(allText.indexOf("Response"));
  });
});

// The not-yet-built "Soon" badge behavior is generic (keyed off item.isPlaceholder, not any
// specific tab's name) - covered in its own file (Sidebar.placeholder.test.tsx) against a
// synthetic NAV_ITEMS fixture, so this file stays coupled to the real nav.ts and never breaks
// again just because a previously-unbuilt tab (e.g. Activity) gets built.
