import { defineConfig, devices } from "@playwright/test";

/**
 * Requires the full stack running first (docker compose up, then `python -m app.seed`
 * against it to create the bootstrap admin account) - this suite does not start any
 * services itself. See docs/RUNBOOK.md.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: 1,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
