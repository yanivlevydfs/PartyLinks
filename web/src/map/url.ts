// The map's place in the address bar, so a link shows the same view: ?at=lat,lng,zoom&spot=lat,lng
// plus the filters a friend needs to see the same pins (when, categories, free only).
import type { Filters } from "../store";

export interface MapLink { at: [number, number, number] | null; spot: string | null; filters: Partial<Filters> }

export function readLink(search = location.search): MapLink {
  const p = new URLSearchParams(search);
  const at = (p.get("at") ?? "").split(",").map(Number);
  const filters: Partial<Filters> = {};
  if (p.get("when")) filters.when = p.get("when")!;
  if (p.getAll("genre").length) filters.genres = p.getAll("genre");
  if (p.get("free") === "1") filters.freeOnly = true;
  return {
    at: at.length === 3 && at.every(Number.isFinite) ? [at[0], at[1], at[2]] : null,
    spot: /^-?\d+\.\d{4},-?\d+\.\d{4}$/.test(p.get("spot") ?? "") ? p.get("spot") : null,
    filters,
  };
}

/** Path and query of the current view (the share link; also kept in the address bar while browsing). */
export function linkPath(center: { lat: number; lng: number }, zoom: number, spot: string | null, f: Filters): string {
  const p = new URLSearchParams();
  p.set("at", `${center.lat.toFixed(4)},${center.lng.toFixed(4)},${Math.round(zoom)}`);
  if (spot) p.set("spot", spot);
  if (f.when !== "all") p.set("when", f.when);
  for (const g of f.genres) p.append("genre", g);
  if (f.freeOnly) p.set("free", "1");
  return `${location.pathname}?${p}`;
}
