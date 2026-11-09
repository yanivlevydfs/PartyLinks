// A place's page: what is known beyond a listing's fields (GET /places/info/<id>, partylinks.placeinfo):
// hours by weekday, rating, booking, links, amenities. PlaceLine is the same on a card, one line.
// One request for the page.
import { useQuery } from "@tanstack/react-query";
import { useT } from "../ui";
import type { EventSummary } from "../types";
import "./placeinfo.css";

interface Details {
  hours?: Record<string, string[]>;
  hours_from?: "site" | "happytlv" | "ontopo";
  deal_hours?: Record<string, string[]>;
  deal_note?: string;
  phone?: string;
  website?: string;
  instagram?: string;
  rating?: number;
  reviews?: number;
  price_level?: number;
  maps_url?: string;
  menu_url?: string;
  hood?: string;
  cuisine?: string[];
  amenities?: string[];
  tags?: string[];
  ontopo_url?: string;
  booking_url?: string;
}

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const TEXT = {
  he: {
    days: ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"],
    hours: "שעות פתיחה", dealHours: "שעות הדיל", closed: "סגור", call: "התקשרו", book: "הזמנת מקום ב-Ontopo", bookOther: "הזמנת מקום", site: "אתר", menu: "תפריט",
    bookable: "הזמנה ב-Ontopo", about: "על המקום", reviews: "ביקורות", google: "בגוגל",
    from: { site: "לפי אתר המקום", happytlv: "לפי גוגל, דרך HappyTLV", ontopo: "לפי Ontopo" },
    check: "בחגים השעות משתנות. כדאי לוודא לפני שיוצאים.",
  },
  en: {
    days: ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
    hours: "Opening hours", dealHours: "Deal hours", closed: "Closed", call: "Call", book: "Book on Ontopo", bookOther: "Book a table", site: "Website", menu: "Menu",
    bookable: "Book on Ontopo", about: "About", reviews: "reviews", google: "on Google",
    from: { site: "From the place's website", happytlv: "Google's, via HappyTLV", ontopo: "From Ontopo" },
    check: "Hours change on holidays. Worth checking before you go.",
  },
};
// What HappyTLV writes in Hebrew, for the English page. Anything else shows as it came.
const ENGLISH: Record<string, string> = {
  "ישיבה בחוץ": "Outdoor seating", "כניסה נגישה": "Accessible entrance", "כלבים מותרים": "Dogs allowed", "מתאים לילדים": "Good for kids",
  "שירותים": "Restrooms", "מוזיקה חיה": "Live music", "חברים": "Friends", "דייט": "Date", "משפחה": "Family", "קוקטיילים": "Cocktails",
  "בר יין": "Wine bar", "קבוצה גדולה": "Large group", "אינטימי": "Intimate", "אוכל איטלקי": "Italian food", "אוכל אסייתי": "Asian food",
  "מרחב מוגן": "Shelter", "בירה": "Beer",
};

function Week({ title, hours, days, closed }: { title: string; hours: Record<string, string[]>; days: string[]; closed: string }) {
  const today = new Date().getDay();
  return (
    <div>
      <h4>{title}</h4>
      <table className="pi-week">
        <tbody>
          {DAYS.map((d, i) => (
            <tr key={d} className={i === today ? "today" : undefined}>
              <th scope="row">{days[i]}</th>
              <td dir="ltr">{hours[d]?.map((s) => s.replace("-", "–")).join(", ") ?? closed}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PlaceInfo({ id }: { id: string }) {
  const { lang } = useT();
  const p = TEXT[lang];
  const { data: d } = useQuery({
    queryKey: ["places", "info", id, lang],
    queryFn: async () => {
      const res = await fetch(`/places/info/${encodeURIComponent(id)}?lang=${lang}`);
      if (!res.ok) throw new Error(`${res.status} /places/info`);
      return (await res.json()) as Details;
    },
    staleTime: 600_000,
  });
  if (!d) return null;
  const word = (w: string) => (lang === "en" && ENGLISH[w]) || w;
  const chips = [...(d.cuisine ?? []), ...(d.tags ?? []), ...(d.amenities ?? [])];
  const external = { target: "_blank", rel: "noopener noreferrer" };
  return (
    <section className="place-info">
      {!!d.rating && (
        <p className="pi-rating">
          <b>★ {d.rating}</b>{d.reviews ? ` (${d.reviews.toLocaleString()} ${p.reviews})` : ""} {p.google}
          {d.hood ? ` · ${d.hood}` : ""}{d.price_level ? ` · ${"₪".repeat(d.price_level)}` : ""}
        </p>
      )}
      <div className="pi-actions">
        {d.ontopo_url && <a className="buy" href={d.ontopo_url} {...external}>📅 {p.book} ↗</a>}
        {!d.ontopo_url && d.booking_url && <a className="buy" href={d.booking_url} {...external}>📅 {p.bookOther} ↗</a>}
        {d.phone && <a href={`tel:${d.phone.replace(/[^\d+]/g, "")}`}>📞 {p.call}</a>}
        {d.website && <a href={d.website} {...external}>🌐 {p.site} ↗</a>}
        {d.menu_url && <a href={d.menu_url} {...external}>🍽️ {p.menu} ↗</a>}
        {d.instagram && <a href={d.instagram} {...external}>Instagram ↗</a>}
      </div>
      {d.deal_note && <p className="pi-note" dir="auto">💡 {d.deal_note}</p>}
      <div className="pi-weeks">
        {d.deal_hours && <Week title={p.dealHours} hours={d.deal_hours} days={p.days} closed={p.closed} />}
        {d.hours && <Week title={p.hours} hours={d.hours} days={p.days} closed={p.closed} />}
      </div>
      {d.hours && <p className="pi-from">{d.hours_from && p.from[d.hours_from]}. {p.check}</p>}
      {!!chips.length && <div className="tags">{chips.map((c) => <span key={c} className="tag" dir="auto">{word(c)}</span>)}</div>}
    </section>
  );
}

/** On a place's card: "★ 4.4 (3,184) · יפו · 📅 Ontopo". */
export function PlaceLine({ e }: { e: EventSummary }) {
  const { lang } = useT();
  const parts = [
    e.rating && `★ ${e.rating}${e.reviews ? ` (${e.reviews.toLocaleString()})` : ""}`,
    e.hood,
    e.bookable && `📅 ${TEXT[lang].bookable}`,
  ].filter(Boolean);
  return parts.length ? <p className="meta" dir="auto">{parts.join(" · ")}</p> : null;
}
