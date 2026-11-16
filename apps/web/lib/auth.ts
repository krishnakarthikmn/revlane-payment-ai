import { call, json, type Result } from "./live";

/** The person signed in, as the API describes them. */
export type Me = {
  id: string;
  tenant_id: string;
  email: string;
  name: string;
  role: "viewer" | "approver" | "admin";
};

const ROLE_WORDS: Record<Me["role"], string> = {
  viewer: "Viewer",
  approver: "Approver",
  admin: "Admin",
};

export function roleLabel(role: Me["role"]): string {
  return ROLE_WORDS[role];
}

/** Who is signed in. Not signed in is a result, not an error. */
export const fetchMe = (fetcher?: typeof fetch): Promise<Result<Me>> =>
  call<Me>("/auth/me", undefined, fetcher);

/** Signs in. The session lives in a cookie the page's scripts cannot read. */
export const signIn = (
  email: string,
  password: string,
  fetcher?: typeof fetch,
): Promise<Result<Me>> =>
  call<Me>("/auth/login", json("POST", { email: email.trim(), password }), fetcher);

export const signOut = (fetcher?: typeof fetch): Promise<Result<null>> =>
  call<null>("/auth/logout", { method: "POST" }, fetcher);
