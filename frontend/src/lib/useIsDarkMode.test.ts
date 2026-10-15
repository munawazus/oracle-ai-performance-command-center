import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useIsDarkMode } from "./useIsDarkMode";

function mockMatchMedia(initialMatches: boolean) {
  let changeHandler: ((e: { matches: boolean }) => void) | null = null;
  const mql = {
    matches: initialMatches,
    media: "(prefers-color-scheme: dark)",
    addEventListener: (_event: string, handler: (e: { matches: boolean }) => void) => {
      changeHandler = handler;
    },
    removeEventListener: () => {
      changeHandler = null;
    },
  };
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => mql),
  );
  return {
    fireChange: (matches: boolean) => {
      mql.matches = matches;
      changeHandler?.({ matches });
    },
  };
}

describe("useIsDarkMode", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns false when the OS preference is light", () => {
    mockMatchMedia(false);
    const { result } = renderHook(() => useIsDarkMode());
    expect(result.current).toBe(false);
  });

  it("returns true when the OS preference is dark", () => {
    mockMatchMedia(true);
    const { result } = renderHook(() => useIsDarkMode());
    expect(result.current).toBe(true);
  });

  it("updates when the media query change event fires", () => {
    const { fireChange } = mockMatchMedia(false);
    const { result } = renderHook(() => useIsDarkMode());
    expect(result.current).toBe(false);

    act(() => fireChange(true));
    expect(result.current).toBe(true);

    act(() => fireChange(false));
    expect(result.current).toBe(false);
  });
});
