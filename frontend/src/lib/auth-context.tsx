import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { apiFetch, refreshAccessToken, setAccessToken } from "./api";
import type { AccessTokenResponse, CurrentUser } from "../types/api";

interface AuthContextValue {
  user: CurrentUser | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  hasPermission: (code: string) => boolean;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchMe = useCallback(async () => {
    const me = await apiFetch<CurrentUser>("/api/auth/me");
    setUser(me);
    return me;
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Shared with apiFetch's own 401-retry path (Enterprise Readiness Plan F.2) - two
        // near-simultaneous mounts (two tabs, or a fast reload) share one in-flight refresh
        // instead of each racing an independent request against the same single-use,
        // rotating refresh token.
        const token = await refreshAccessToken();
        if (cancelled) return;
        if (!token) {
          setUser(null);
          return;
        }
        const me = await fetchMe();
        if (cancelled) return;
        setUser(me);
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchMe]);

  const login = useCallback(
    async (email: string, password: string) => {
      const tokens = await apiFetch<AccessTokenResponse>("/api/auth/login", {
        method: "POST",
        body: { email, password },
        skipAuthRetry: true,
      });
      setAccessToken(tokens.access_token);
      await fetchMe();
    },
    [fetchMe],
  );

  const logout = useCallback(async () => {
    try {
      await apiFetch("/api/auth/logout", { method: "POST", skipAuthRetry: true });
    } finally {
      setAccessToken(null);
      setUser(null);
    }
  }, []);

  const hasPermission = useCallback((code: string) => user?.permissions.includes(code) ?? false, [user]);

  const value = useMemo(
    () => ({ user, isLoading, login, logout, hasPermission }),
    [user, isLoading, login, logout, hasPermission],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
