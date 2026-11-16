import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

// The browser tests have their own API, web server and database, so they never
// touch what `make dev` is using.
export const API_PORT = 8001;
export const WEB_PORT = 3001;
// The same host name as the console, so the session cookie is sent to both.
export const API_URL = `http://localhost:${API_PORT}/api/v1`;
export const WEB_URL = `http://localhost:${WEB_PORT}`;

// The demo tenant's admin, as `make demo` creates her. Values for these tests only.
export const DEMO_EMAIL = "priya.menon@acmetravel.example";
export const DEMO_PASSWORD = "e2e-only-demo-password";
/** Where the signed-in browser state is kept between tests. */
export const SIGNED_IN = "e2e/.auth/priya.json";

/** The API's environment: a database of its own, the stub model, runs carried out in-process. */
export const API_ENV: Record<string, string> = {
  DATABASE_URL:
    process.env.E2E_DATABASE_URL ??
    "postgresql+psycopg://revlane:revlane@localhost:5432/revlane_e2e",
  TOKEN_HASH_ROOT_KEY: "e2e-only-token-root",
  VAULT_KEY: "e2e-only-vault-key",
  LLM_STUB: "true",
  AGENT_RUNS_INLINE: "true",
  WEB_ORIGINS: JSON.stringify([WEB_URL, `http://127.0.0.1:${WEB_PORT}`]),
  STORAGE_PATH: "./data/objects-e2e",
  SESSION_SECRET: "e2e-only-session-secret",
  DEMO_USER_PASSWORD: DEMO_PASSWORD,
  METRICS_TOKEN: "e2e-only-metrics-token",
};

/** Runs `python -m revlane.dev <args>` against the browser tests' database. */
export function dev(...args: string[]): string {
  const [program, ...rest] = (process.env.E2E_PYTHON ?? "uv run python").split(" ");
  return execFileSync(program ?? "uv", [...rest, "-m", "revlane.dev", ...args], {
    cwd: resolve(__dirname, "../../api"),
    env: { ...process.env, ...API_ENV },
    encoding: "utf-8",
    stdio: ["ignore", "pipe", "pipe"],
  });
}
