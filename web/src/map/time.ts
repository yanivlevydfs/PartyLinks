// Time helpers for the map: "on now" pins and the evening's hour bar.
import type { MapEvent } from "../types";

/** Started and not over yet (an event with a date only is never "now"). */
export function isLive(e: Pick<MapEvent, "start_at" | "end_at" | "time_known">, now = Date.now()): boolean {
  return e.time_known !== false && !!e.start_at && !!e.end_at && Date.parse(e.start_at) <= now && now < Date.parse(e.end_at);
}

const fmt = new Map<string, Intl.DateTimeFormat>();

/** Start hour 0-23 on the venue's clock (Israel's when no zone); null for a date-only event. */
export function startHour(e: Pick<MapEvent, "start_at" | "time_known" | "time_zone">): number | null {
  if (e.time_known === false || !e.start_at) return null;
  const zone = e.time_zone ?? "Asia/Jerusalem";
  let f = fmt.get(zone);
  if (!f) fmt.set(zone, (f = new Intl.DateTimeFormat("en-GB", { timeZone: zone, hour: "numeric", hourCycle: "h23" })));
  return Number(f.format(Date.parse(e.start_at)));
}

/** The hour bar's stops: two-hour windows from 18:00 to 04:00. */
export const HOURS = [18, 19, 20, 21, 22, 23, 0, 1, 2]; // the last window, 02:00-04:00, ends the night

/** Starts in the two hours from ``h`` (22 takes 22:00-23:59; 23 takes 23:00-00:59). */
export function inWindow(hour: number | null, h: number): boolean {
  return hour != null && (hour === h || hour === (h + 1) % 24);
}
