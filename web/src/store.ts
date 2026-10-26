import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Lang } from "./i18n";

export interface Filters {
  q: string;
  when: string; // "all" | "tonight" | "weekend" | "week" | "month" | YYYY-MM-DD | YYYY-MM-DD~YYYY-MM-DD
  genres: string[];
  slots: string[];
  regions: string[]; // "tel_aviv" and/or regions of the country, any-of
  areas: string[]; // Tel Aviv neighbourhoods, when Tel Aviv is the only region
  city: string | null; // a city of the region, when one other region is the only one
  shops: string[]; // ticket sites (API ?source=)
  freeOnly: boolean;
  discountOnly: boolean;
  hideSoldOut: boolean;
  sort: string;
}

export const DEFAULT_FILTERS: Filters = {
  q: "", when: "all", genres: [], slots: [], regions: [], areas: [], city: null, shops: [], freeOnly: false, discountOnly: false, hideSoldOut: false, sort: "start_at",
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
  // The home page's "where to?" questions were answered (or skipped) in this visit: show the results.
  quizDone: boolean;
  setQuizDone: (quizDone: boolean) => void;
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
      quizDone: false,
      setQuizDone: (quizDone) => set({ quizDone }),
    }),
    { name: "partylinks", partialize: (s) => ({ lang: s.lang }) },
  ),
);
