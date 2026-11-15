// Small geography helpers for the map page.
import { today, tomorrow } from "../ui";

export type LatLng = [number, number];

/** Great-circle distance in km. */
export function km([a, b]: LatLng, [c, d]: LatLng): number {
  const r = Math.PI / 180;
  const x = Math.sin(((c - a) * r) / 2) ** 2 + Math.cos(a * r) * Math.cos(c * r) * Math.sin(((d - b) * r) / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(x));
}

export const wazeUrl = ([lat, lng]: LatLng) => `https://waze.com/ul?ll=${lat},${lng}&navigate=yes`;
export const googleUrl = ([lat, lng]: LatLng) => `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`;

export type Day = "all" | "today" | "tomorrow" | "weekend";

/** Thursday evening to Saturday, Israel's weekend (dates as YYYY-MM-DD in Israel's zone). */
function weekendDates(): Set<string> {
  const base = Date.parse(today() + "T12:00:00Z");
  const dow = new Date(base).getUTCDay(); // 0 Sunday
  const out = new Set<string>();
  for (let i = 0; i < 7; i++) {
    const d = (dow + i) % 7;
    if (d === 4 || d === 5 || d === 6) out.add(new Date(base + i * 864e5).toISOString().slice(0, 10));
    if (d === 6) break;
  }
  return out;
}

export function dayTest(day: Day): (localDate: string | null) => boolean {
  if (day === "all") return () => true;
  if (day === "today") { const d = today(); return (x) => x === d; }
  if (day === "tomorrow") { const d = tomorrow(); return (x) => x === d; }
  const w = weekendDates();
  return (x) => !!x && w.has(x);
}
