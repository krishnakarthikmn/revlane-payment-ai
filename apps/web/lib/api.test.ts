import { describe, expect, it, vi } from "vitest";

import { API_URL, fetchHealth, type Health } from "./api";

const healthy: Health = {
  status: "ok",
  service: "revlane-api",
  version: "0.1.0",
  database: "ok",
  llm_mode: "stub",
};

function respond(status: number, body: unknown): typeof fetch {
  return vi.fn(async () => Response.json(body, { status })) as typeof fetch;
}

describe("fetchHealth", () => {
  it("calls the health route under /api/v1", async () => {
    const fetcher = respond(200, healthy);

    await fetchHealth(fetcher);

    expect(fetcher).toHaveBeenCalledWith(
      `${API_URL}/health`,
      expect.anything(),
    );
    expect(API_URL.endsWith("/api/v1")).toBe(true);
  });

  it("returns the API's health response", async () => {
    expect(await fetchHealth(respond(200, healthy))).toEqual({
      state: "up",
      health: healthy,
    });
  });

  it("reads the message from the error envelope", async () => {
    const fetcher = respond(503, {
      error: { code: "internal_error", message: "Starting up" },
    });

    expect(await fetchHealth(fetcher)).toEqual({
      state: "down",
      message: "Starting up",
    });
  });

  it("reports an unreachable API instead of throwing", async () => {
    const fetcher = vi.fn(async () => {
      throw new TypeError("fetch failed");
    }) as typeof fetch;

    expect(await fetchHealth(fetcher)).toEqual({
      state: "down",
      message: `No answer from ${API_URL}`,
    });
  });
});
