import { expect, test } from "@playwright/test";

// Requires the full stack up (docker compose) and a seeded account:
//   E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD (defaults match .env.example's ADMIN_BOOTSTRAP_* + seed script)
const EMAIL = process.env.E2E_ADMIN_EMAIL ?? "admin@example.com";
const PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "change_me_dev_only";

test("login, navigate the authenticated shell, and log out", async ({ page }) => {
  await page.goto("/login");

  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByRole("link", { name: "Overview" })).toBeVisible();
  await expect(page).toHaveURL("/");

  // exact: true - the Overview page's "no databases registered yet" empty state renders
  // its own "Go to Databases ->" link, whose accessible name is a substring match against
  // "Databases" under Playwright's default (non-exact) matching. That ambiguity is timing-
  // dependent on when the empty-state card finishes rendering, which is why this was flaky
  // under parallel workers instead of reliably failing - see docs/ENTERPRISE_READINESS_PLAN.md
  // Phase A.3.
  await page.getByRole("link", { name: "Databases", exact: true }).click();
  await expect(page).toHaveURL(/\/databases$/);
  await expect(page.getByRole("heading", { name: "Databases", exact: false })).toBeVisible();

  await page.getByRole("link", { name: "Administration" }).click();
  await expect(page).toHaveURL(/\/administration$/);

  await page.getByRole("button", { name: "User menu" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});

test("an unknown route redirects to login when signed out", async ({ page }) => {
  await page.goto("/databases");
  await expect(page).toHaveURL(/\/login$/);
});
