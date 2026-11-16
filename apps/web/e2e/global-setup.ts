import { mkdirSync } from "node:fs";
import { dirname } from "node:path";

import { request } from "@playwright/test";

import { API_URL, DEMO_EMAIL, DEMO_PASSWORD, SIGNED_IN, dev } from "./backend";

/**
 * Before the console tests: the synthetic merchant, loaded and audited, and
 * Priya signed in. Her session is saved so each test starts signed in; the
 * sign-in page has tests of its own.
 */
export default async function globalSetup(): Promise<void> {
  dev("seed");
  const api = await request.newContext();
  const response = await api.post(`${API_URL}/auth/login`, {
    data: { email: DEMO_EMAIL, password: DEMO_PASSWORD },
  });
  if (!response.ok()) {
    throw new Error(`Could not sign in for the browser tests: ${response.status()}`);
  }
  mkdirSync(dirname(SIGNED_IN), { recursive: true });
  await api.storageState({ path: SIGNED_IN });
  await api.dispose();
}
