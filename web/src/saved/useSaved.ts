// Saved events, one hook for both states. Signed out: the ids live on this device (localStorage
// "spotx-saved"). Signed in: the server, toggled optimistically. On sign-in the device's list is merged
// into the account once, then cleared here.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { create } from "zustand";
import { persist } from "zustand/middleware";
import { send, useMe } from "../accounts/useMe";
import type { EventSummary } from "../types";

const MAX_LOCAL = 500;

interface LocalSaved {
  ids: string[];
  toggle: (id: string) => void;
  clear: () => void;
}

export const useLocalSaved = create<LocalSaved>()(
  persist(
    (set) => ({
      ids: [],
      toggle: (id) => set((s) => ({ ids: s.ids.includes(id) ? s.ids.filter((x) => x !== id) : [id, ...s.ids].slice(0, MAX_LOCAL) })),
      clear: () => set({ ids: [] }),
    }),
    { name: "spotx-saved" },
  ),
);

export interface SavedList {
  ids: string[];
  items: EventSummary[];
}

/** The signed-in user's saved events (null while signed out). */
export function useServerSaved() {
  const me = useMe().data;
  return useQuery<SavedList>({
    queryKey: ["saved", me?.id],
    queryFn: () => fetch("/api/me/saved", { credentials: "same-origin" }).then((r) => r.json()),
    enabled: !!me,
    staleTime: 60_000,
  });
}

/** Once signed in, the hearts kept on this device join the account. Mounted once (the header's SavedLink),
 *  not in useSaved: every heart on the page calls that, and each would send its own merge. */
export function useMergeOnSignIn() {
  const me = useMe().data;
  const qc = useQueryClient();
  const ids = useLocalSaved((s) => s.ids);
  const clear = useLocalSaved((s) => s.clear);
  const busy = useRef(false);
  useEffect(() => {
    if (!me || !ids.length || busy.current) return;
    busy.current = true;
    send<SavedList>("/api/me/saved/merge", "POST", { ids })
      .then((list) => { qc.setQueryData(["saved", me.id], list); clear(); })
      .catch(() => {}) // tried again on the next page
      .finally(() => { busy.current = false; });
  }, [me?.id, ids.length]);
}

export function useSaved() {
  const me = useMe().data;
  const qc = useQueryClient();
  const local = useLocalSaved();
  const server = useServerSaved();
  const ids = me ? server.data?.ids ?? [] : local.ids;

  const toggle = async (id: string) => {
    if (!me) return local.toggle(id);
    const key = ["saved", me.id];
    const before = qc.getQueryData<SavedList>(key);
    const on = !!before?.ids.includes(id);
    qc.setQueryData<SavedList>(key, (cur) => ({
      ids: on ? (cur?.ids ?? []).filter((x) => x !== id) : [id, ...(cur?.ids ?? [])],
      items: cur?.items ?? [],
    }));
    try {
      await send(`/api/me/saved/${encodeURIComponent(id)}`, on ? "DELETE" : "PUT");
    } catch {
      qc.setQueryData(key, before); // put the heart back as it was
    }
    qc.invalidateQueries({ queryKey: key });
  };

  return { ids, count: ids.length, isSaved: (id: string) => ids.includes(id), toggle };
}
