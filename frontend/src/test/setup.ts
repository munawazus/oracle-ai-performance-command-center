import "@testing-library/jest-dom/vitest";

// jsdom doesn't implement matchMedia at all - default every test to "light mode, no match"
// so components using useIsDarkMode() don't crash in tests that aren't specifically about
// dark mode. Tests that do care (e.g. useIsDarkMode.test.ts) override this locally.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }) as unknown as MediaQueryList;
}
