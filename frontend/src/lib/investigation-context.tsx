import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

interface InvestigationContextValue {
  isOpen: boolean;
  toggle: () => void;
  close: () => void;
  currentDatabaseId: string | null;
  currentDatabaseName: string | null;
  setCurrentDatabase: (id: string, name: string) => void;
}

const DEFAULT_VALUE: InvestigationContextValue = {
  isOpen: false,
  toggle: () => {},
  close: () => {},
  currentDatabaseId: null,
  currentDatabaseName: null,
  setCurrentDatabase: () => {},
};

/** Unlike AuthContext/ToastContext, this has a real (no-op) default instead of `undefined` +
 * throw-if-missing - DatabaseSelector calls useInvestigation() and is rendered inside every
 * existing page's tests, none of which wrap a provider. A non-throwing default means those
 * tests keep passing unchanged; only tests that actually exercise this feature need the real
 * InvestigationProvider. */
const InvestigationContext = createContext<InvestigationContextValue>(DEFAULT_VALUE);

export function InvestigationProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const [currentDatabaseId, setCurrentDatabaseId] = useState<string | null>(null);
  const [currentDatabaseName, setCurrentDatabaseName] = useState<string | null>(null);

  const toggle = useCallback(() => setIsOpen((prev) => !prev), []);
  const close = useCallback(() => setIsOpen(false), []);
  const setCurrentDatabase = useCallback((id: string, name: string) => {
    setCurrentDatabaseId(id);
    setCurrentDatabaseName(name);
  }, []);

  const value = useMemo(
    () => ({ isOpen, toggle, close, currentDatabaseId, currentDatabaseName, setCurrentDatabase }),
    [isOpen, toggle, close, currentDatabaseId, currentDatabaseName, setCurrentDatabase],
  );

  return <InvestigationContext.Provider value={value}>{children}</InvestigationContext.Provider>;
}

export function useInvestigation(): InvestigationContextValue {
  return useContext(InvestigationContext);
}
