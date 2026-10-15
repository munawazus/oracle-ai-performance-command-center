import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "./auth-context";

const ME_RESPONSE = {
  id: "u1",
  email: "admin@example.com",
  display_name: "Admin",
  roles: ["ADMIN"],
  permissions: ["database:view"],
};

function Probe() {
  const { user, isLoading } = useAuth();
  return <div data-testid="probe">{isLoading ? "loading" : (user?.email ?? "anonymous")}</div>;
}

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 401): Response {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

describe("AuthProvider", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("logs the user in from a valid refresh cookie on mount", async () => {
    globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
      if (String(url).includes("/api/auth/refresh")) {
        return jsonResponse({ access_token: "tok1", expires_at: "2099-01-01T00:00:00Z" });
      }
      return jsonResponse(ME_RESPONSE);
    });

    const { getByTestId } = render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    await waitFor(() => expect(getByTestId("probe").textContent).toBe("admin@example.com"));
  });

  it("stays logged out when there is no valid refresh cookie", async () => {
    globalThis.fetch = vi.fn(async () => jsonResponse({ detail: "No active session." }, false));

    const { getByTestId } = render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    await waitFor(() => expect(getByTestId("probe").textContent).toBe("anonymous"));
  });

  it("two near-simultaneous AuthProviders share one /api/auth/refresh call, not two", async () => {
    // Regression test for Enterprise Readiness Plan F.2: two tabs (or a fast reload) each
    // mounting AuthProvider around the same time must not each fire an independent refresh
    // against the same single-use, rotating refresh token - they must share the same
    // in-flight request instead.
    const fetchMock = vi.fn(async (url: RequestInfo | URL) => {
      if (String(url).includes("/api/auth/refresh")) {
        return jsonResponse({ access_token: "tok1", expires_at: "2099-01-01T00:00:00Z" });
      }
      return jsonResponse(ME_RESPONSE);
    });
    globalThis.fetch = fetchMock;

    const { getAllByTestId } = render(
      <>
        <AuthProvider>
          <Probe />
        </AuthProvider>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </>,
    );

    await waitFor(() => {
      const probes = getAllByTestId("probe");
      expect(probes).toHaveLength(2);
      for (const probe of probes) expect(probe.textContent).toBe("admin@example.com");
    });

    const refreshCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/auth/refresh"));
    expect(refreshCalls).toHaveLength(1);
  });
});
