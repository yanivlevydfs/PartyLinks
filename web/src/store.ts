import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Lang } from "./i18n";

export interface Filters {
  q: string;
  when: string; // "all" | "tonight" | "weekend" | YYYY-MM-DD
  genres: string[];
  slot: string | null;
  region: string | null; // "tel_aviv" or a region of the country
  areas: string[]; // Tel Aviv neighbourhoods, when region is tel_aviv
  city: string | null; // a city of the region, when region is another one
  shops: string[]; // ticket sites (API ?source=)
  freeOnly: boolean;
  hideSoldOut: boolean;
  sort: string;
}

export const DEFAULT_FILTERS: Filters = {
  q: "", when: "all", genres: [], slot: null, region: null, areas: [], city: null, shops: [], freeOnly: false, hideSoldOut: false, sort: "start_at",
};

interface State {
  lang: Lang;
  setLang: (lang: Lang) => void;
  // Home-page filters live here so they survive visiting an event and coming back.
  filters: Filters;
  setFilters: (patch: Partial<Filters>) => void;
  // Set by a landing page (/city/x, /genre/x...): its preset is dropped when the plain home page opens.
  preset: boolean;
  setPreset: (preset: boolean) => void;
}

export const useStore = create<State>()(
  persist(
    (set) => ({
      lang: "he",
      setLang: (lang) => set({ lang }),
      filters: DEFAULT_FILTERS,
      setFilters: (patch) => set((s) => ({ filters: { ...s.filters, ...patch } })),
      preset: false,
      setPreset: (preset) => set({ preset }),
    }),
    { name: "partylinks", partialize: (s) => ({ lang: s.lang }) },
  ),
);
