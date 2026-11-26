// The strip under a city page's title (/city/<key>[/...], /abroad/<country>/<city>[/...]): the next days'
// forecast at the city's centre, quick links to its tonight / weekend / genre pages, and sharing the page.
// Everything comes from /landing-info (center, zone, links): the same the server renders.
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";
import { useLandingInfo } from "../api";
import { SiteShare } from "../share/SiteShare";
import { useT } from "../ui";
import { iconFor, iconUrl, loadWeather, weatherKey, WX_WORDS, type Slot } from "../Weather";
import "./city.css";

const SITE = "https://spotx.fun";
const DAYS = 5;

const TEXT = {
  he: { forecast: (city?: string) => (city ? `מזג האוויר ב${city}` : "מזג האוויר"), today: "היום", explore: "בעיר" },
  en: { forecast: (city?: string) => (city ? `Weather in ${city}` : "Weather"), today: "Today", explore: "In the city" },
};

/** The city's root page: /city/bat-yam/tonight -> /city/bat-yam, /abroad/france/paris/techno -> /abroad/france/paris. */
export function cityRoot(path: string): string | null {
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "city" && parts.length >= 2) return `/city/${parts[1]}`;
  if (parts[0] === "abroad" && parts.length >= 3) return `/abroad/${parts[1]}/${parts[2]}`;
  return null;
}

interface Day { date: string; symbol: string; hi: number | null; lo: number | null; rain: number }

/** Slots -> one line per local day: its high and low, the rain, and the sky at about 14:00. */
export function days(slots: Slot[], zone: string, n = DAYS): Day[] {
  const local = (iso: string) => {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23" })
      .formatToParts(new Date(iso));
    const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
    return { date: `${get("year")}-${get("month")}-${get("day")}`, hour: Number(get("hour")) };
  };
  const out = new Map<string, Day & { noon: number }>();
  for (const [time, symbol, temp, mm] of slots) {
    const { date, hour } = local(time);
    const d = out.get(date) ?? { date, symbol, hi: null, lo: null, rain: 0, noon: 99 };
    if (temp != null) {
      d.hi = d.hi == null ? temp : Math.max(d.hi, temp);
      d.lo = d.lo == null ? temp : Math.min(d.lo, temp);
    }
    d.rain += mm ?? 0;
    if (Math.abs(hour - 14) < d.noon) { d.noon = Math.abs(hour - 14); d.symbol = symbol; }
    out.set(date, d);
  }
  return [...out.values()].slice(0, n).map(({ noon: _, ...d }) => d);
}

function Forecast({ center, zone, city }: { center: [number, number]; zone: string; city?: string }) {
  const { lang, t } = useT();
  const p = TEXT[lang];
  const cell = weatherKey(center[0], center[1])!;
  const { data } = useQuery({
    queryKey: ["weather", cell], // the same cell (and cache, and batched request) as the cards' forecast pills
    queryFn: () => loadWeather(cell),
    staleTime: 15 * 60e3,
    retry: false,
  });
  const list = data ? days(data, zone) : [];
  if (!list.length) return null;
  const today = days([[new Date().toISOString(), "", null, null, 1]], zone)[0]?.date;
  const w = WX_WORDS[lang];
  return (
    <section className="city-wx" aria-label={p.forecast(city)}>
      <h2 className="city-bar-h">{p.forecast(city)}</h2>
      <ol>
        {list.map((d) => {
          const [icon, word] = iconFor(d.symbol, false);
          const name = d.date === today ? p.today
            : new Date(`${d.date}T12:00:00Z`).toLocaleDateString(t.locale, { weekday: "short", timeZone: "UTC" });
          return (
            <li key={d.date} title={`${name}: ${w[word]}${d.rain >= 0.5 ? ` · ${Math.round(d.rain)} ${w.rainMm}` : ""}`}>
              <span className="city-wx-day">{name}</span>
              <img src={iconUrl(icon)} alt={w[word]} loading="lazy" width={34} height={34} />
              <span className="city-wx-t">
                {d.hi != null && <b>{Math.round(d.hi)}°</b>}
                {d.lo != null && d.lo !== d.hi && <small>{Math.round(d.lo)}°</small>}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function CityBar() {
  const { lang } = useT();
  const { pathname } = useLocation();
  const info = useLandingInfo(pathname, lang).data;
  const root = cityRoot(pathname);
  if (!root || !info?.title) return null;
  // Its own pages: all events, tonight, this weekend, its genres (the server's related links, in order).
  const quick = info.links.filter((l) => (l.path === root || l.path.startsWith(root + "/")) && l.path !== pathname).slice(0, 8);
  const url = `${SITE}${pathname}${lang === "en" ? "?lang=en" : ""}`;
  return (
    <div className="city-bar">
      {info.center && <Forecast center={info.center} zone={info.zone ?? "Asia/Jerusalem"} city={info.city?.[lang]} />}
      {quick.length > 0 && (
        <nav className="city-quick" aria-label={TEXT[lang].explore}>
          {quick.map((l) => <Link key={l.path} className="chip" to={l.path}>{l.label}</Link>)}
        </nav>
      )}
      <SiteShare page={{ url, title: info.title }} />
    </div>
  );
}
