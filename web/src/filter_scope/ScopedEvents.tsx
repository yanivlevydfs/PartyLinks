// An artist's or a venue's events with the full home filter (dates, area, near me, genres, price, sort...),
// counted within that page only. Its own state: the home page's filter is left as it was.
import { useState } from "react";
import type { Params } from "../api";
import { filterParams, Filters, Relax } from "../Filters";
import { DEFAULT_FILTERS, type Filters as FilterState } from "../store";
import { EventGrid } from "../ui";
import { ScopeContext } from "./scope";

export function ScopedEvents({ base }: { base: Params }) {
  const [f, setF] = useState<FilterState>(DEFAULT_FILTERS);
  const set = (patch: Partial<FilterState>) => setF((x) => ({ ...x, ...patch }));
  return (
    <ScopeContext.Provider value={{ f, set, base, local: true }}>
      <Filters />
      <EventGrid params={{ ...base, ...filterParams(f) }} empty={() => <Relax />} />
    </ScopeContext.Provider>
  );
}
