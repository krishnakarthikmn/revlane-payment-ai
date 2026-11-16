export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

export type Health = {
  status: "ok" | "degraded";
  service: string;
  version: string;
  database: "ok" | "unavailable";
  llm_mode: "stub" | "live";
};

export type HealthResult =
  | { state: "up"; health: Health }
  | { state: "down"; message: string };

type ErrorBody = { error?: { code?: string; message?: string } };

/** Calls the API's health route. Never throws: an unreachable API is a result. */
export async function fetchHealth(
  fetcher: typeof fetch = fetch,
): Promise<HealthResult> {
  try {
    const response = await fetcher(`${API_URL}/health`, { cache: "no-store" });
    // The health check is public; every other call sends the session cookie.
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as ErrorBody;
      return {
        state: "down",
        message: body.error?.message ?? `The API answered ${response.status}`,
      };
    }
    return { state: "up", health: (await response.json()) as Health };
  } catch {
    return { state: "down", message: `No answer from ${API_URL}` };
  }
}
