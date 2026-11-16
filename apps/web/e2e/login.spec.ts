import { expect, test } from "@playwright/test";

import { DEMO_EMAIL, DEMO_PASSWORD } from "./backend";

// These tests start with nobody signed in.
test.use({ storageState: { cookies: [], origins: [] } });

test("the console sends anyone not signed in to the sign-in page", async ({ page }) => {
  for (const path of ["/", "/agents", "/approvals", "/connections", "/contracts"]) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/login$/);
  }
  await expect(page.getByRole("heading", { name: "Sign in to Revlane" })).toBeVisible();
  // Nothing of the merchant's is on the page.
  await expect(page.getByText("Acme Travel")).toHaveCount(0);
});

test("a wrong password is refused without saying which part was wrong", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator("form").getByRole("alert")).toHaveText("Enter your email and password.");

  await page.getByLabel("Email").fill(DEMO_EMAIL);
  await page.getByLabel("Password").fill("not-her-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.locator("form").getByRole("alert")).toHaveText(
    "That email and password do not match an account.",
  );
  await expect(page).toHaveURL(/\/login$/);
});

test("signing in opens the console, and signing out closes it again", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(DEMO_EMAIL);
  await page.getByLabel("Password").fill(DEMO_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByRole("heading", { name: "Good morning, Priya" })).toBeVisible();
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByText("Priya Menon")).toBeVisible();
  await expect(nav.getByText("Acme Travel · Admin")).toBeVisible();
  // Signed in, the sign-in page has nothing to offer.
  await page.goto("/login");
  await expect(page).toHaveURL(/\/$/);

  await nav.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/agents");
  await expect(page).toHaveURL(/\/login$/);
});
