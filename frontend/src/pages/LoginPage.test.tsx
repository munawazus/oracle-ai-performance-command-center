import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginPage } from "./LoginPage";

const mockLogin = vi.fn();
const mockAuthState: { user: null; login: typeof mockLogin } = { user: null, login: mockLogin };

vi.mock("../lib/auth-context", () => ({
  useAuth: () => mockAuthState,
}));

describe("LoginPage", () => {
  beforeEach(() => {
    mockLogin.mockReset();
  });

  it("submits the entered credentials", async () => {
    mockLogin.mockResolvedValueOnce(undefined);
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    await userEvent.type(screen.getByLabelText("Email"), "dba@example.org");
    await userEvent.type(screen.getByLabelText("Password"), "correct-password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(mockLogin).toHaveBeenCalledWith("dba@example.org", "correct-password");
  });

  it("shows the server's error message on failed login", async () => {
    mockLogin.mockRejectedValueOnce(new Error("Invalid email or password."));
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    await userEvent.type(screen.getByLabelText("Email"), "dba@example.org");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
  });
});
