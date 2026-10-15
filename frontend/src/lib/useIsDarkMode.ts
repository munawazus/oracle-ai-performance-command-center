import { useEffect, useState } from "react";

const QUERY = "(prefers-color-scheme: dark)";

/** Recharts colors are SVG stroke/fill props, not classNames - `dark:` utility classes can't
 * reach them, so chart-color choices need the OS preference as plain JS state instead. */
export function useIsDarkMode(): boolean {
  const [isDark, setIsDark] = useState(() => window.matchMedia(QUERY).matches);

  useEffect(() => {
    const mql = window.matchMedia(QUERY);
    const onChange = (e: MediaQueryListEvent | { matches: boolean }) => setIsDark(e.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return isDark;
}
