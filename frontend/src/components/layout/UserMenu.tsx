import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "../../lib/auth-context";
import { useTheme, type ThemeMode } from "../../lib/theme-context";

const THEME_OPTIONS: { value: ThemeMode; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];

export function UserMenu() {
  const { user, logout } = useAuth();
  const { mode, setMode } = useTheme();
  const [isOpen, setIsOpen] = useState(false);
  const navigate = useNavigate();

  if (!user) return null;

  const handleLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setIsOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-label="User menu"
        className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
      >
        <span
          aria-hidden="true"
          className="flex h-7 w-7 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700 dark:bg-brand-900/40 dark:text-brand-300"
        >
          {user.display_name.slice(0, 1).toUpperCase()}
        </span>
        <span className="text-slate-700 dark:text-slate-300">{user.display_name}</span>
      </button>
      {isOpen && (
        <div
          role="menu"
          className="absolute right-0 z-20 mt-1 w-56 rounded-md border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-800 dark:bg-slate-900"
        >
          <div className="border-b border-slate-100 px-3 py-2 dark:border-slate-800">
            <p className="text-sm font-medium text-slate-800 dark:text-slate-200">{user.email}</p>
            <p className="text-xs text-slate-500 dark:text-slate-400">{user.roles.join(", ")}</p>
          </div>
          <div className="border-b border-slate-100 px-3 py-2 dark:border-slate-800">
            <p className="mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">Theme</p>
            <div role="radiogroup" aria-label="Theme" className="flex gap-1 rounded-md bg-slate-100 p-0.5 dark:bg-slate-800">
              {THEME_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  role="menuitemradio"
                  aria-checked={mode === opt.value}
                  onClick={() => setMode(opt.value)}
                  className={`flex-1 rounded px-2 py-1 text-xs font-medium transition-colors ${
                    mode === opt.value
                      ? "bg-white text-slate-900 shadow-sm dark:bg-slate-700 dark:text-slate-100"
                      : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={handleLogout}
            className="block w-full px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
