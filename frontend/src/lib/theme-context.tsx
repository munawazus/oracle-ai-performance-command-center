import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { useIsDarkMode } from "./useIsDarkMode";

export type ThemeMode = "light" | "dark" | "system";

const STORAGE_KEY = "theme";

interface ThemeContextValue {
  mode: ThemeMode;
  resolvedIsDark: boolean;
  setMode: (mode: ThemeMode) => void;
}

const DEFAULT_VALUE: ThemeContextValue = {
  mode: "system",
  resolvedIsDark: false,
  setMode: () => {},
};

/** Non-throwing default, same reasoning as InvestigationContext (UI.9): TrendChart will call
 * useTheme() and is rendered inside existing tests that don't wrap a provider. */
const ThemeContext = createContext<ThemeContextValue>(DEFAULT_VALUE);

function readStoredMode(): ThemeMode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
  } catch {
    return "system";
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const osPrefersDark = useIsDarkMode();
  const [mode, setModeState] = useState<ThemeMode>(readStoredMode);

  const resolvedIsDark = mode === "dark" ? true : mode === "light" ? false : osPrefersDark;

  useEffect(() => {
    document.documentElement.classList.toggle("dark", resolvedIsDark);
    document.documentElement.style.colorScheme = resolvedIsDark ? "dark" : "light";
  }, [resolvedIsDark]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* privacy mode or similar - the choice just won't persist across reloads */
    }
  }, []);

  const value = useMemo(() => ({ mode, resolvedIsDark, setMode }), [mode, resolvedIsDark, setMode]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}
