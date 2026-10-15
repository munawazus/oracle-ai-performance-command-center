import type { AccessTokenResponse } from "../types/api";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown, message?: string) {
    super(message ?? `Request failed with status ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

async function doRefresh(): Promise<string | null> {
  const res = await fetch(`${API_BASE}/api/auth/refresh`, { method: "POST", credentials: "include" });
  if (!res.ok) {
    accessToken = null;
    return null;
  }
  const data = (await res.json()) as AccessTokenResponse;
  accessToken = data.access_token;
  return accessToken;
}

let refreshInFlight: Promise<string | null> | null = null;

/** At most one real /api/auth/refresh request is ever in flight at a time - every caller
 * (this module's own 401-retry path below, and AuthProvider's mount-time refresh) shares the
 * same in-flight promise instead of each racing an independent request against the same
 * single-use, rotating refresh token (Enterprise Readiness Plan F.2). */
export function refreshAccessToken(): Promise<string | null> {
  if (!refreshInFlight) {
    refreshInFlight = doRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE" | "PUT";
  body?: unknown;
  skipAuthRetry?: boolean;
}

function extractDetail(data: unknown): unknown {
  if (data && typeof data === "object" && "detail" in data) {
    return (data as { detail: unknown }).detail;
  }
  return data;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const doFetch = (): Promise<Response> => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
    return fetch(`${API_BASE}${path}`, {
      method: options.method ?? "GET",
      headers,
      credentials: "include",
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
  };

  let res = await doFetch();

  if (res.status === 401 && !options.skipAuthRetry && path !== "/api/auth/refresh") {
    const newToken = await refreshAccessToken();
    if (newToken) {
      res = await doFetch();
    }
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const text = await res.text();
  let data: unknown;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
    const detail = extractDetail(data);
    throw new ApiError(res.status, detail, typeof detail === "string" ? detail : undefined);
  }

  return data as T;
}

/** Fetches a binary endpoint (e.g. a report export) and saves it via the browser's normal
 * download flow. Reuses the same bearer token as apiFetch, but not its JSON parsing/refresh
 * logic - a 401 here just surfaces as an ApiError, since export links are short-lived UI
 * actions, not something worth an auth-refresh retry for. */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const headers: Record<string, string> = {};
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const res = await fetch(`${API_BASE}${path}`, { headers, credentials: "include" });
  if (!res.ok) {
    const text = await res.text();
    let detail: unknown = text;
    try {
      detail = extractDetail(JSON.parse(text));
    } catch {
      /* not JSON - use raw text as the detail */
    }
    throw new ApiError(res.status, detail, typeof detail === "string" ? detail : undefined);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** Turns an ApiError's `detail` (a string, or a list of pydantic validation errors) into one readable line. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === "string") return err.detail;
    if (Array.isArray(err.detail)) {
      return err.detail
        .map((e) => {
          if (e && typeof e === "object" && "msg" in e) return String((e as { msg: unknown }).msg);
          return String(e);
        })
        .join(" ");
    }
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}
