// The admin's pin endpoints (/admin/api/home-pins): signed in with Google, or the console's token.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { EventSummary } from "../types";

export type Picked = Pick<EventSummary, "id" | "slug" | "title" | "start_at" | "venue_name"> & { cover_url?: string | null; image_url?: string | null };
export interface Pin {
  event_id: string;
  rails: string[];
  until: string | null;
  note: string | null;
  title: string | null;
  slug: string | null;
  start_at: string | null;
  venue_name: string | null;
  cover_url: string | null;
  appears: string[];
}

const API = "/admin/api/home-pins";

export async function api<T = unknown>(path: string, method = "GET", body?: unknown): Promise<T> {
  const token = (() => { try { return sessionStorage.getItem("partylinks-admin-token") ?? ""; } catch { return ""; } })();
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      "X-Requested-With": "spotx",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail?.toString() ?? String(res.status));
  return res.status === 204 ? (null as T) : res.json();
}

const id = (eventId: string) => `${API}/${encodeURIComponent(eventId)}`;
export const usePins = () => useQuery({ queryKey: ["home-pins"], queryFn: () => api<Pin[]>(API) });
export const useEventPin = (eventId: string) =>
  useQuery({ queryKey: ["home-pins", eventId], queryFn: () => api<{ pin: Pin | null; natural: string[] }>(`${API}/event/${encodeURIComponent(eventId)}`) });

/** Pin / edit, unpin, reorder: each refreshes every pin query. */
export function usePinChange() {
  const qc = useQueryClient();
  const onSuccess = () => qc.invalidateQueries({ queryKey: ["home-pins"] });
  return {
    save: useMutation({ mutationFn: ({ eventId, ...body }: { eventId: string; rails: string[]; until: string | null; note: string | null }) => api(id(eventId), "PUT", body), onSuccess }),
    unpin: useMutation({ mutationFn: (eventId: string) => api(id(eventId), "DELETE"), onSuccess }),
    order: useMutation({ mutationFn: (ids: string[]) => api(`${API}/order`, "POST", ids), onSuccess }),
  };
}
