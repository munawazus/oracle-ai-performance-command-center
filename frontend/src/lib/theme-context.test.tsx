import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider, useTheme } from "./theme-context";

function mockMatchMedia(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches,
      media: "(prefers-color-scheme: dark)",
      addEventListener: () => {},
      removeEventListener: () => {},
    })),
  );
}

function Probe() {
  const { mode, resolvedIsDark, setMode } = useTheme();
  return (
    <div>
      <p>mode: {mode}</p>
      <p>resolvedIsDark: {String(resolvedIsDark)}</p>
      <button onClick={() => setMode("light")}>light</button>
      <button onClick={() => setMode("dark")}>dark</button>
      <button onClick={() => setMode("system")}>system</button>
    </div>
  );
}

describe("useTheme", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.documentElement.classList.remove("dark");
  });

  it("returns a safe default (no throw) when called with no provider present", () => {
    mockMatchMedia(false);
    expect(() => render(<Probe />)).not.toThrow();
    expect(screen.getByText("mode: system")).toBeInTheDocument();
  });

  it("defaults to system mode and tracks the OS preference when nothing is stored", () => {
    mockMatchMedia(true);
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );

    expect(screen.getByText("mode: system")).toBeInTheDocument();
    expect(screen.getByText("resolvedIsDark: true")).toBeInTheDocument();
  });

  it("forces resolvedIsDark regardless of OS preference once dark or light is explicitly chosen", async () => {
    mockMatchMedia(false); // OS says light
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );

    await userEvent.click(screen.getByText("dark"));
    expect(screen.getByText("mode: dark")).toBeInTheDocument();
    expect(screen.getByText("resolvedIsDark: true")).toBeInTheDocument();
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    await userEvent.click(screen.getByText("light"));
    expect(screen.getByText("resolvedIsDark: false")).toBeInTheDocument();
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("persists the chosen mode to localStorage and reads it back on a fresh mount", async () => {
    mockMatchMedia(false);
    const { unmount } = render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    await userEvent.click(screen.getByText("dark"));
    expect(localStorage.getItem("theme")).toBe("dark");
    unmount();

    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByText("mode: dark")).toBeInTheDocument();
    expect(screen.getByText("resolvedIsDark: true")).toBeInTheDocument();
  });
});
