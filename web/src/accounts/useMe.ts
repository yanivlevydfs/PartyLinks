// The signed-in user (null when signed out) and our own writes to /api/me*.
import { useQuery, useQueryClient } from "@tanstack/react-query";

export interface Me {
  id: string;
  name: string;
  email: string;
  picture: string | null;
  locale: "he" | "en";
  role: string;
  digest_opt_in: boolean;
  gender: "male" | "female" | "none" | null;
}

/** A write from our pages: the header the server checks for (a cross-site form can't set it). */
export async function send<T = unknown>(url: string, method: string, body?: unknown): Promise<T | null> {
  const res = await fetch(url, {
    method,
    credentials: "same-origin",
    headers: { "X-Requested-With": "spotx", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(String(res.status));
  return res.status === 204 ? null : res.json();
}

/** Google sign-in is configured on the server (GET /site): otherwise no account UI at all. */
export function useAccountsOn() {
  return useQuery({ queryKey: ["site"], queryFn: () => fetch("/site").then((r) => r.json()) }).data?.accounts === true;
}

export function useMe() {
  const on = useAccountsOn();
  return useQuery<Me | null>({
    queryKey: ["me"],
    queryFn: async () => {
      const res = await fetch("/api/me", { credentials: "same-origin" });
      return res.ok ? res.json() : null;
    },
    enabled: on,
    staleTime: 5 * 60_000,
  });
}

/** Where "Continue with Google" goes: back to this page afterwards. */
export const signInUrl = (next = location.pathname + location.search) => `/auth/google/login?next=${encodeURIComponent(next)}`;

export function useSignOut() {
  const qc = useQueryClient();
  return async () => {
    await send("/api/auth/logout", "POST");
    qc.setQueryData(["me"], null);
    await qc.invalidateQueries();
  };
}
