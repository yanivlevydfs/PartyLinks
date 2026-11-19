// How an event's page reads: a clean title, a one-line lede, a short address, "today" / "tomorrow", and its FAQ.
import type { Lang, Strings } from "../i18n";
import { genreLabel } from "../i18n";
import type { EventDetail } from "../types";
import { priceLabel, sourceName, when } from "../ui";

// A title segment that is only a date or a weekday: "20.11", "20/11/26", "יום שישי", "Friday".
const DATE_PART = /^(\d{1,2}[./]\d{1,2}([./]\d{2,4})?|יום\s+\S+|(mon|tues|wednes|thurs|fri|satur|sun)day)$/i;

/** "ROOM 48 | 20.11 | ADAM TEN" -> "ADAM TEN · ROOM 48": the date left out (the page shows it), the venue last. */
export function cleanTitle(title: string, venue: string | null): string {
  if (!title.includes("|")) return title;
  const parts = title.split("|").map((p) => p.trim()).filter((p) => p && !DATE_PART.test(p));
  if (!parts.length) return title;
  const isVenue = (p: string) => !!venue && p.toLowerCase() === venue.toLowerCase();
  return [...parts.filter((p) => !isVenue(p)), ...parts.filter(isVenue)].join(" · ");
}

const HEBREW = /[֐-׿]/;

/** The street part only: no postcode, country or city (shown beside it). On a Hebrew page, a Latin address
 * (a map's "King George St 48, Tel Aviv-Yafo, 6433701, Israel") is left out when the venue names the place. */
export function shortAddress(e: Pick<EventDetail, "address" | "city" | "venue_name">, lang: Lang): string | null {
  if (!e.address) return null;
  const parts = e.address.split(",").map((p) => p.trim())
    .filter((p) => p && !/^\d{5,7}$/.test(p) && !/^(israel|ישראל)$/i.test(p) && p !== e.city && !/^tel aviv(-yafo)?$/i.test(p));
  const out = parts.join(", ");
  if (!out || (lang === "he" && e.venue_name && !HEBREW.test(out))) return null;
  return out;
}

const israelDay = (d: Date, zone: string) => d.toLocaleDateString("en-CA", { timeZone: zone }); // YYYY-MM-DD

/** "Tonight" / "Today" / "Tomorrow" for an event in the next two days, else null. */
export function relativeDay(e: Pick<EventDetail, "local_date" | "time_zone" | "time_slot">, t: Strings): string | null {
  if (!e.local_date) return null;
  const zone = e.time_zone ?? "Asia/Jerusalem";
  const now = new Date();
  if (e.local_date === israelDay(now, zone)) return e.time_slot === "evening" || e.time_slot === "night" ? t.tonight : t.dayToday;
  if (e.local_date === israelDay(new Date(now.getTime() + 864e5), zone)) return t.tomorrow;
  return null;
}

/** The description's first sentence (up to ~200 characters), for a lede under the title. */
export function lede(e: Pick<EventDetail, "description_layout">): string | null {
  const first = e.description_layout?.blocks.find((b) => b.kind === "p")?.text.replace(/\*\*/g, "").trim();
  if (!first) return null;
  const sentence = first.match(/^.{20,200}?[.!?](\s|$)/)?.[0].trim() ?? first;
  return sentence.length > 200 ? sentence.slice(0, 197).replace(/\s+\S*$/, "") + "…" : sentence;
}

/** Questions a visitor asks, answered from the event's own fields (the server sends the same as FAQPage, partylinks.seo). */
export function eventFaq(e: EventDetail, t: Strings, lang: Lang): [string, string][] {
  const he = lang === "he";
  const where = [e.venue_name, shortAddress(e, lang), e.city].filter(Boolean).join(", ");
  const date = when(e, t, { weekday: "long", day: "numeric", month: "long" });
  const time = e.time_known !== false ? when(e, t, { hour: "2-digit", minute: "2-digit" }) : "";
  const price = priceLabel({ ...e, min_price: e.group_min_price ?? e.min_price, entry_type: e.group_entry_type ?? e.entry_type }, t, e.tickets);
  const sites = [...new Set([e.source, ...(e.alternatives ?? []).filter((a) => !a.resale).map((a) => a.source)].map(sourceName))].join(", ");
  const genres = e.genres.slice(0, 3).map((g) => genreLabel(lang, g)).join(", ");
  const faq: [string, string][] = [
    [he ? "איפה מתקיים האירוע?" : "Where is the event?", where],
    [he ? "מתי מתקיים האירוע?" : "When is the event?", [date, time && (he ? `בשעה ${time}` : `at ${time}`)].filter(Boolean).join(" ")],
    [he ? `כמה עולה כרטיס?` : `How much is a ticket?`, price],
    [he ? `איפה קונים כרטיסים?` : `Where do I buy tickets?`, sites && (he ? `באתרי הכרטיסים: ${sites}. הקישורים בראש הדף.` : `On the ticket sites: ${sites}. The links are at the top of the page.`)],
    [he ? `איזה סגנון מוזיקה?` : `What music?`, genres],
    [he ? `מי מופיע?` : `Who plays?`, e.lineup.slice(0, 6).join(", ")],
    [he ? `מגיל כמה הכניסה?` : `What is the minimum age?`, e.min_age ? (he ? `מגיל ${e.min_age}` : `${e.min_age}+`) : ""],
  ];
  return faq.filter(([, a]) => a);
}

export function EventFaq({ items, title }: { items: [string, string][]; title: string }) {
  if (!items.length) return null;
  return (
    <section className="e-faq">
      <h4>{title}</h4>
      {items.map(([q, a]) => (
        <details key={q}>
          <summary dir="auto">{q}</summary>
          <p dir="auto">{a}</p>
        </details>
      ))}
    </section>
  );
}
