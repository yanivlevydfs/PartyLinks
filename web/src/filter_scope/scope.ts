// Which filter state the home filter (Filters.tsx) reads and writes: the shared home one by default,
// or a page's own (an artist's or a venue's events), narrowed by that page's base query.
import { createContext, useContext } from "react";
import type { Params } from "../api";
import { useStore, type Filters } from "../store";

export type Scope = { f: Filters; set: (patch: Partial<Filters>) => void; base: Params; local: boolean };

export const ScopeContext = createContext<Scope | null>(null);

export function useFilters(): Scope {
  const scope = useContext(ScopeContext);
  const f = useStore((s) => s.filters);
  const set = useStore((s) => s.setFilters);
  return scope ?? { f, set, base: {}, local: false };
}
