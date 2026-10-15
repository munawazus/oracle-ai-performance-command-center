import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ProtectedRoute, RequirePermission } from "./ProtectedRoute";

const mockAuthState: { user: unknown; isLoading: boolean; hasPermission: (code: string) => boolean } = {
  user: null,
  isLoading: false,
  hasPermission: () => false,
};

vi.mock("../../lib/auth-context", () => ({
  useAuth: () => mockAuthState,
}));

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<div>Login screen</div>} />
        <Route element={<ProtectedRoute />}>
          <Route path="/" element={<div>Overview screen</div>} />
          <Route
            path="/databases"
            element={
              <RequirePermission permission="database:view">
                <div>Databases screen</div>
              </RequirePermission>
            }
          />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProtectedRoute", () => {
  it("shows a loading state while the session is being resolved", () => {
    mockAuthState.isLoading = true;
    mockAuthState.user = null;
    renderAt("/");
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("redirects unauthenticated visitors to /login", () => {
    mockAuthState.isLoading = false;
    mockAuthState.user = null;
    renderAt("/databases");
    expect(screen.getByText("Login screen")).toBeInTheDocument();
  });

  it("renders the page for an authenticated user", () => {
    mockAuthState.isLoading = false;
    mockAuthState.user = { id: "u1" };
    renderAt("/");
    expect(screen.getByText("Overview screen")).toBeInTheDocument();
  });

  it("shows a forbidden message when the user lacks the required permission", () => {
    mockAuthState.isLoading = false;
    mockAuthState.user = { id: "u1" };
    mockAuthState.hasPermission = () => false;
    renderAt("/databases");
    expect(screen.getByText(/don't have access to this page/i)).toBeInTheDocument();
    expect(screen.queryByText("Databases screen")).not.toBeInTheDocument();
  });

  it("renders the page when the user holds the required permission", () => {
    mockAuthState.isLoading = false;
    mockAuthState.user = { id: "u1" };
    mockAuthState.hasPermission = (code) => code === "database:view";
    renderAt("/databases");
    expect(screen.getByText("Databases screen")).toBeInTheDocument();
  });
});
