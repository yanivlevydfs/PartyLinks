import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Lang } from "./i18n";

export interface Filters {
  q: string;
  when: string; // "all" | "tonight" | "weekend" | YYYY-MM-DD
  genres: string[];
  slot: string | null;
  areas: string[];
  freeOnly: boolean;
  hideSoldOut: boolean;
  sort: string;
}

const DEFAULT_FILTERS: Filters = {
  q: "", when: "all", genres: [], slot: null, areas: [], freeOnly: false, hideSoldOut: false, sort: "start_at",
};

interface State {
  lang: Lang;
  setLang: (lang: Lang) => void;
  // Home-page filters live here so they survive visiting an event and coming back.
  filters: Filters;
  setFilter: <K extends keyof Filters>(key: K, value: Filters[K]) => void;
}

export const useStore = create<State>()(
  persist(
    (set) => ({
      lang: "he",
      setLang: (lang) => set({ lang }),
      filters: DEFAULT_FILTERS,
      setFilter: (key, value) => set((s) => ({ filters: { ...s.filters, [key]: value } })),
    }),
    { name: "partylinks", partialize: (s) => ({ lang: s.lang }) },
  ),
);
