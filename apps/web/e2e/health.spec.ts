import { expect, test } from "@playwright/test";

// Milestone 0 acceptance: the web page shows the API's health response.
test("the status page shows the API's health response", async ({ page }) => {
  await page.goto("/status");

  await expect(page.getByRole("heading", { name: "Revlane" })).toBeVisible();

  const health = page.getByRole("region", { name: "API health" });
  await expect(health.getByTestId("health-pill")).toHaveText(
    /Running|Degraded/,
  );
  await expect(health.getByText("revlane-api")).toBeVisible();
  await expect(health.getByText("stub")).toBeVisible();
});

test("the page says so when the API cannot be reached", async ({ page }) => {
  await page.route("**/api/v1/health", (route) => route.abort());

  await page.goto("/status");

  await expect(page.getByTestId("health-pill")).toHaveText("Unreachable");
});
