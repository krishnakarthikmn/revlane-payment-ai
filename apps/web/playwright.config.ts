import { defineConfig, devices } from "@playwright/test";

import { API_ENV, API_PORT, API_URL, SIGNED_IN, WEB_PORT, WEB_URL } from "./e2e/backend";

const reuse = !process.env.CI;

// E2E_API_COMMAND lets a machine without `uv` on the path start the API its own way.
const apiCommand =
  process.env.E2E_API_COMMAND ??
  `uv run uvicorn revlane.main:app --host 127.0.0.1 --port ${API_PORT}`;

const chrome = {
  ...devices["Desktop Chrome"],
  viewport: { width: 1440, height: 1000 },
  // E2E_BROWSER_CHANNEL=chrome uses the installed Google Chrome instead
  // of Playwright's bundled Chromium.
  channel: process.env.E2E_BROWSER_CHANNEL || undefined,
};

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? "github" : "list",
  // Loads the synthetic merchant and runs the audit before any test.
  globalSetup: "./e2e/global-setup.ts",
  // Every test starts signed in as the demo admin, unless it says otherwise.
  use: { baseURL: WEB_URL, trace: "retain-on-failure", storageState: SIGNED_IN },
  projects: [
    // The console on an audited merchant. These tests only read real data.
    { name: "console", testIgnore: /flow\.spec\.ts/, use: chrome },
    // The whole journey from empty to recovered. It empties the tenant first,
    // so it runs alone, after everything else.
    { name: "flow", testMatch: /flow\.spec\.ts/, dependencies: ["console"], use: chrome },
  ],
  webServer: [
    {
      command: apiCommand,
      cwd: "../api",
      url: `${API_URL}/health`,
      env: API_ENV,
      reuseExistingServer: reuse,
      timeout: 60_000,
    },
    {
      command: `npm run dev -- --port ${WEB_PORT}`,
      url: WEB_URL,
      env: { NEXT_PUBLIC_API_URL: API_URL, NEXT_DIST_DIR: ".next-e2e" },
      reuseExistingServer: reuse,
      timeout: 120_000,
    },
  ],
});
