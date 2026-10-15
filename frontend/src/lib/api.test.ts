import { afterEach, describe, expect, it, vi } from "vitest";

import { apiFetch, ApiError, errorMessage } from "./api";

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 401): Response {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

describe("errorMessage", () => {
  it("returns a string detail as-is", () => {
    expect(errorMessage(new ApiError(401, "Invalid email or password."))).toBe("Invalid email or password.");
  });

  it("joins pydantic-style validation error lists into one line", () => {
    const err = new ApiError(422, [
      { loc: ["body", "port"], msg: "Input should be less than or equal to 65535" },
      { loc: ["body", "name"], msg: "Field required" },
    ]);
    expect(errorMessage(err)).toContain("Input should be less than or equal to 65535");
    expect(errorMessage(err)).toContain("Field required");
  });

  it("falls back to a generic message for unknown errors", () => {
    expect(errorMessage("not an error")).toBe("Something went wrong.");
  });
});

describe("apiFetch 401 handling", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("retries once after a successful refresh on 401", async () => {
    let realEndpointCalls = 0;
    globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
      if (String(url).includes("/api/auth/refresh")) {
        return jsonResponse({ access_token: "tok2", expires_at: "2099-01-01T00:00:00Z" });
      }
      realEndpointCalls++;
      return realEndpointCalls === 1 ? jsonResponse({ detail: "expired" }, false) : jsonResponse({ ok: true });
    });

    const result = await apiFetch<{ ok: boolean }>("/api/databases");

    expect(result).toEqual({ ok: true });
    expect(realEndpointCalls).toBe(2);
  });

  it("two concurrent 401s share exactly one /api/auth/refresh call", async () => {
    // Enterprise Readiness Plan F.2: the de-dup guard now lives inside the exported
    // refreshAccessToken() itself, not just apiFetch's own inline check - this locks in that
    // apiFetch's 401-retry path still shares one in-flight refresh across concurrent callers.
    const fetchMock = vi.fn(async (url: RequestInfo | URL) => {
      if (String(url).includes("/api/auth/refresh")) {
        return jsonResponse({ access_token: "tok2", expires_at: "2099-01-01T00:00:00Z" });
      }
      return jsonResponse({ detail: "expired" }, false);
    });
    globalThis.fetch = fetchMock;

    await Promise.allSettled([apiFetch("/api/a"), apiFetch("/api/b")]);

    const refreshCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/auth/refresh"));
    expect(refreshCalls).toHaveLength(1);
  });
});
