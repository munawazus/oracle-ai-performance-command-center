import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { Sidebar } from "./Sidebar";

const mockHasPermission = vi.fn();

vi.mock("../../lib/auth-context", () => ({
  useAuth: () => ({ hasPermission: mockHasPermission }),
}));

// A synthetic fixture, not the real nav.ts - this test verifies Sidebar's generic
// item.isPlaceholder -> "Soon" badge behavior, decoupled from which real tab (if any) happens
// to be unbuilt at any given time (Sidebar.test.tsx exercises the real nav.ts and must never
// break again just because a previously-placeholder tab, like Activity, gets built).
vi.mock("../../lib/nav", () => ({
  NAV_ITEMS: [
    { label: "Overview", path: "/", group: "Monitoring" },
    { label: "Not Yet Built", path: "/not-yet-built", isPlaceholder: true, group: "Monitoring" },
  ],
}));

describe("Sidebar placeholder badge", () => {
  it("tags a not-yet-built placeholder link but not a real page like Overview", () => {
    mockHasPermission.mockReturnValue(true);
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );
    const placeholderLink = screen.getByRole("link", { name: /Not Yet Built/ });
    expect(within(placeholderLink).getByText("Soon")).toBeInTheDocument();
    expect(within(placeholderLink).getByTitle("Not available yet")).toBeInTheDocument();

    const overviewLink = screen.getByRole("link", { name: "Overview" });
    expect(within(overviewLink).queryByText("Soon")).not.toBeInTheDocument();
  });
});
