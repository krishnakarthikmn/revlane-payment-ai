import { describe, expect, it, vi } from "vitest";

import { API_URL } from "./api";
import { fetchMe, roleLabel, signIn, signOut, type Me } from "./auth";

const priya: Me = {
  id: "u1",
  tenant_id: "t1",
  email: "priya@example.com",
  name: "Priya Menon",
  role: "admin",
};

function respond(status: number, body: unknown): typeof fetch {
  return vi.fn(async () =>
    status === 204 ? new Response(null, { status }) : Response.json(body, { status }),
  ) as typeof fetch;
}

describe("sign-in", () => {
  it("posts the email and password and sends the session cookie from then on", async () => {
    const fetcher = respond(200, priya);

    expect(await signIn("  priya@example.com ", "a-long-password", fetcher)).toEqual({
      ok: true,
      value: priya,
    });
    expect(fetcher).toHaveBeenCalledWith(
      `${API_URL}/auth/login`,
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({ email: "priya@example.com", password: "a-long-password" }),
      }),
    );
  });

  it("shows the API's own words when the email and password do not match", async () => {
    const refused = respond(401, {
      error: { code: "unauthenticated", message: "That email and password do not match an account." },
    });

    expect(await signIn("priya@example.com", "wrong", refused)).toEqual({
      ok: false,
      message: "That email and password do not match an account.",
    });
  });

  it("asks who is signed in, and treats nobody as an answer", async () => {
    const nobody = respond(401, { error: { message: "Sign in to continue." } });

    expect(await fetchMe(respond(200, priya))).toEqual({ ok: true, value: priya });
    expect(await fetchMe(nobody)).toEqual({ ok: false, message: "Sign in to continue." });
  });

  it("signs out", async () => {
    const fetcher = respond(204, null);

    expect(await signOut(fetcher)).toEqual({ ok: true, value: null });
    expect(fetcher).toHaveBeenCalledWith(
      `${API_URL}/auth/logout`,
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
  });

  it("names each role in a word", () => {
    expect([roleLabel("viewer"), roleLabel("approver"), roleLabel("admin")]).toEqual([
      "Viewer",
      "Approver",
      "Admin",
    ]);
  });
});
