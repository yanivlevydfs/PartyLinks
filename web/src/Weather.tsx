import { useQuery } from "@tanstack/react-query";
import { useT } from "./ui";

/**
 * The forecast at an event's start: an animated Meteocons icon and the temperature, from the
 * server's /weather (MET Norway, ~10 days ahead). Every event within ~10 km shares one request.
 * Nothing shows for past events, events too far ahead, or places without coordinates.
 */

// [time (UTC ISO), MET symbol ("rainshowers_day"), °C, mm, hours the slot covers]
type Slot = [string, string, number | null, number | null, number];

// Only the sky icons, each its own file (not inlined into the bundle), fetched when shown.
const ICONS = import.meta.glob(
  "/node_modules/@bybas/weather-icons/production/fill/all/{clear,cloudy,drizzle,fog,partly,rain,sleet,snow,thunderstorms}*.svg",
  { query: "?no-inline", import: "default", eager: true },
) as Record<string, string>;
const iconUrl = (name: string) => ICONS[`/node_modules/@bybas/weather-icons/production/fill/all/${name}.svg`];

const WORDS = {
  he: { clear: "בהיר", partly: "מעונן חלקית", cloudy: "מעונן", fog: "ערפל", drizzle: "טפטוף", rain: "גשם",
        showers: "ממטרים", thunder: "סופות רעמים", snow: "שלג", sleet: "שלג וגשם", rainMm: "מ״מ גשם", forecast: "תחזית" },
  en: { clear: "Clear", partly: "Partly cloudy", cloudy: "Cloudy", fog: "Fog", drizzle: "Drizzle", rain: "Rain",
        showers: "Showers", thunder: "Thunderstorms", snow: "Snow", sleet: "Sleet", rainMm: "mm rain", forecast: "Forecast" },
} as const; // prettier-ignore
type Word = Exclude<keyof (typeof WORDS)["en"], "rainMm" | "forecast">;

/** MET symbol -> (Meteocons icon, description). Unsuffixed symbols take day or night from the hour. */
export function iconFor(symbol: string, night: boolean): [string, Word] {
  const [base, suffix] = symbol.split("_");
  const dn = suffix ? (suffix === "day" ? "day" : "night") : night ? "night" : "day";
  const showers = base.includes("showers");
  const precip = (kind: string, word: Word): [string, Word] =>
    [showers ? `partly-cloudy-${dn}-${kind}` : kind, showers && word === "rain" ? "showers" : word];
  if (base.includes("thunder")) {
    const kind = base.includes("snow") || base.includes("sleet") ? "-snow" : base.includes("rain") ? "-rain" : "";
    return [`thunderstorms-${dn}${kind}`, "thunder"];
  }
  if (base.includes("sleet")) return precip("sleet", "sleet");
  if (base.includes("snow")) return precip("snow", "snow");
  if (base === "lightrain" || base === "lightrainshowers") return precip("drizzle", "drizzle");
  if (base.includes("rain")) return precip("rain", "rain");
  if (base === "fog") return [`fog-${dn}`, "fog"];
  if (base === "cloudy") return ["cloudy", "cloudy"];
  if (base === "clearsky") return [`clear-${dn}`, "clear"];
  return [`partly-cloudy-${dn}`, "partly"]; // fair, partlycloudy
}

/** The slot whose window holds `start` (an event already under way takes the first slot). */
export function slotAt(slots: Slot[], start: number): Slot | undefined {
  if (slots.length && start < Date.parse(slots[0][0]) && start > Date.now() - 6 * 3600e3) return slots[0];
  return slots.find(([time, , , , hours]) => {
    const from = Date.parse(time);
    return start >= from && start < from + hours * 3600e3;
  });
}

/** When an event's forecast is read: its start; a date alone (no time from the seller), that day's noon
 *  (its stored start is midnight, hours "ago" on the day itself, and would show no forecast).
 *  ponytail: noon UTC, close to the venue's midday from Europe to Asia; the zone's noon if America needs it. */
export const forecastAt = (e: { start_at: string | null; time_known?: boolean; local_date: string | null }) =>
  e.time_known === false && e.local_date ? `${e.local_date}T12:00:00Z` : e.start_at;

export function Weather({ lat, lng, city, start, zone, full = false }: {
  lat: number | null;
  lng: number | null;
  city?: string | null; // no exact point: the city centre's forecast
  start: string | null;
  zone?: string | null; // a venue abroad: day / night by its own clock
  full?: boolean;
}) {
  const { lang } = useT();
  const when = start ? Date.parse(start) : NaN;
  const near = Number.isFinite(when) && when > Date.now() - 6 * 3600e3 && when < Date.now() + 11 * 864e5;
  const cell = lat != null && lng != null ? `lat=${lat.toFixed(1)}&lng=${lng.toFixed(1)}` : city ? `city=${encodeURIComponent(city)}` : null;
  const { data, isLoading } = useQuery({
    queryKey: ["weather", cell],
    queryFn: async () => {
      const res = await fetch(`/weather?${cell}`);
      if (!res.ok) throw new Error(`${res.status} /weather`);
      return ((await res.json()) as { slots: Slot[] }).slots;
    },
    enabled: !!cell && near,
    staleTime: 15 * 60e3,
    retry: false,
  });
  if (isLoading && !full) return <span className="wx-loading" aria-hidden />;
  const slot = data && slotAt(data, when);
  if (!slot) return null;
  const [, symbol, temp, rain] = slot;
  const hour = Number(new Date(when).toLocaleString("en-GB", { timeZone: zone ?? "Asia/Jerusalem", hour: "numeric", hourCycle: "h23" }));
  const [icon, word] = iconFor(symbol, hour < 6 || hour >= 18);
  const w = WORDS[lang];
  const text = `${w[word]}${temp != null ? ` · ${Math.round(temp)}°` : ""}`;
  const img = <img src={iconUrl(icon)} alt={w[word]} loading="lazy" />;
  const deg = temp != null && <b>{Math.round(temp)}°</b>;
  // Full: a line of the event page's facts list; otherwise a pill on the card image.
  return full ? (
    <li className="wx wx-full" title={`${w.forecast}: ${text}`}>
      <i>{img}</i>
      {deg}
      <span>{w[word]}{rain ? ` · ${rain} ${w.rainMm}` : ""}</span>
    </li>
  ) : (
    <span className="wx" title={`${w.forecast}: ${text}`}>{img}{deg}</span>
  );
}
