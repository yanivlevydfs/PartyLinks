import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useCategories, useEvents, type Params } from "./api";
import { CITIES, STRINGS, genreLabel, type Lang, type Strings } from "./i18n";
import { CookieConsent, useConsent } from "./consent";
import { useStore } from "./store";
import type { EventSummary } from "./types";

export const TZ = "Asia/Jerusalem";
export const today = () => new Date().toLocaleDateString("en-CA", { timeZone: TZ });

export function useT(): { t: Strings; lang: Lang } {
  const lang = useStore((s) => s.lang);
  return { t: STRINGS[lang] as Strings, lang };
}

/** Label for an API enum value from one of the i18n maps, falling back to the raw value. */
export const label = (map: object, key: string | null | undefined) =>
  key ? ((map as Record<string, string>)[key] ?? key) : "";

export function money(value: number, currency: string, locale: string) {
  return new Intl.NumberFormat(locale, { style: "currency", currency, maximumFractionDigits: 0 }).format(value);
}

export function fmtDate(iso: string | null, t: Strings, opts: Intl.DateTimeFormatOptions) {
  return iso ? new Date(iso).toLocaleString(t.locale, { timeZone: TZ, ...opts }) : "";
}

export function priceLabel(e: Pick<EventSummary, "entry_type" | "min_price" | "has_free_entry" | "currency">, t: Strings) {
  if (e.entry_type === "free" || e.min_price === 0) return t.free;
  if (e.min_price == null) return e.has_free_entry ? t.free : "";
  return t.from + money(e.min_price, e.currency, t.locale);
}

// Display names of the ticket sites we scrape (keys = EventSummary.source).
const SOURCE_NAMES: Record<string, string> = {
  goout: "Go-Out", eventer: "Eventer", zygo: "Zygo", tickchak: "Tickchak",
  selector: "Selector", barby: "Barby", grayclub: "Gray Club", chillz: "Chillz",
  mevalim: "Mevalim", hulyo: "Hulyo",
};
export const sourceName = (source: string) => SOURCE_NAMES[source] ?? source;

/** Ticket-site tags: every store selling the event (this listing's first). */
export function Sources({ e, className = "" }: { e: { source: string; sources?: string[] }; className?: string }) {
  const list = e.sources?.length ? e.sources : [e.source];
  return (
    <span className={"sources " + className}>
      {list.map((s) => <span key={s} className="src">{sourceName(s)}</span>)}
    </span>
  );
}

export const venuePath = (name: string) => `/venue/${encodeURIComponent(name)}`;
export const artistPath = (name: string) => `/artist/${encodeURIComponent(name)}`;
// Pretty slug URLs (the API resolves either slug or id); same paths the server renders for crawlers.
export const eventPath = (e: { id: string; slug: string | null }) => `/event/${encodeURIComponent(e.slug ?? e.id)}`;

/** Browser tab title for the current page. */
export function useTitle(title: string | undefined) {
  useEffect(() => {
    if (title) document.title = `${title} · Pulse`;
  }, [title]);
}

// --------------------------------------------------------------------------- layout
export function Layout() {
  const { t, lang } = useT();
  const setLang = useStore((s) => s.setLang);
  const cats = useCategories().data;
  const openConsent = useConsent((s) => s.openSettings);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = t.dir;
  }, [lang, t.dir]);

  return (
    <>
      <CookieConsent />
      <header className="top">
        <div className="wrap top-row">
          <Link className="logo" to="/">{lang === "he" ? <>דו<span>פק</span></> : <>Pul<span>se</span></>}</Link>
          <nav className="nav">
            <NavLink to="/" end>{t.nav.parties}</NavLink>
            <NavLink to="/map">{t.nav.map}</NavLink>
            <NavLink to="/venues">{t.nav.venues}</NavLink>
            <NavLink to="/artists">{t.nav.artists}</NavLink>
            <NavLink to="/blog">{t.nav.blog}</NavLink>
          </nav>
          <button className="lang" onClick={() => setLang(lang === "he" ? "en" : "he")} aria-label="Language">
            {lang === "he" ? "EN" : "עב"}
          </button>
        </div>
      </header>
      <Outlet />
      <footer className="foot wrap">
        <nav className="foot-links" aria-label={t.genres}>
          <Link to="/tonight">{t.tonight}</Link>
          <Link to="/weekend">{t.weekend}</Link>
          <Link to="/blog">{t.nav.blog}</Link>
          {Object.entries(CITIES).filter(([, c]) => (cats?.cities.find((x) => x.value === c.he)?.events ?? 0) >= 3).map(([key, c]) => (
            <Link key={key} to={`/city/${key}`}>{c[lang]}</Link>
          ))}
          {cats?.genres.filter((g) => g.events >= 3).slice(0, 12).map((g) => (
            <Link key={g.value} to={`/genre/${g.value}`}>{genreLabel(lang, g.value)}</Link>
          ))}
        </nav>
        {lang === "he" ? "דופק · ישראל" : "Pulse · Israel"} · <Link to="/accessibility">{t.accessibility}</Link>
      </footer>
        {" · "}<Link to="/privacy">{t.privacy}</Link>
        {" · "}<button className="linkish" onClick={openConsent}>{t.cookieSettings}</button>
    </>
  );
}

export function Hero({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <section className="hero">
      <div className="wrap">
        <h1 dir="auto">{title}</h1>
        {children}
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- bits
export function Chip({ on, onClick, children, className }: { on: boolean; onClick: () => void; children: ReactNode; className?: string }) {
  const ref = useRef<HTMLButtonElement>(null);
  // In a scrolling chip row (mobile), bring a preselected chip (/genre/hip_hop) into view.
  useEffect(() => {
    if (on) ref.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <button ref={ref} className={"chip" + (className ? " " + className : "") + (on ? " on" : "")} aria-pressed={on} onClick={onClick}>
      {children}
    </button>
  );
}

export function Img({ src, alt, className }: { src: string | null | undefined; alt: string; className: string }) {
  const [broken, setBroken] = useState(false);
  if (!src || broken) return <div className={className + " noimg"} aria-hidden>♫</div>;
  return <img className={className} src={src} alt={alt} loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)} />;
}

const DAY_MS = 24 * 3600 * 1000;

/** "new" = first seen in the last day; "updated" = tickets / prices changed in the last day. */
export function freshness(e: EventSummary): "new" | "updated" | null {
  const now = Date.now();
  const first = new Date(e.first_seen_at).getTime();
  if (now - first < DAY_MS) return "new";
  const changed = e.offers_changed_at ? new Date(e.offers_changed_at).getTime() : 0;
  return changed - first > 60_000 && now - changed < DAY_MS ? "updated" : null;
}

export function Card({ e }: { e: EventSummary }) {
  const { t, lang } = useT();
  const fresh = freshness(e);
  // Cards show the best offer across duplicate listings, matching the free-entry filter.
  const price = priceLabel(
    {
      ...e,
      min_price: e.group_min_price ?? e.min_price,
      has_free_entry: e.group_has_free_entry ?? e.has_free_entry,
      entry_type: e.group_entry_type ?? e.entry_type,
    },
    t,
  );
  return (
    <Link className="card" to={eventPath(e)}>
      <div className="card-img">
        <Img src={e.cover_url ?? e.image_url} alt={e.title} className="cover" />
        <div className="date-badge">
          <b>{fmtDate(e.start_at, t, { day: "numeric" })}</b>
          <span>{fmtDate(e.start_at, t, { month: "short" })}</span>
        </div>
        {fresh && <span className={`fresh ${fresh}`}>{fresh === "new" ? t.isNew : t.updatedBadge}</span>}
        {e.is_sold_out ? (
          <span className="flag sold">{t.soldOut}</span>
        ) : (
          price && <span className={"flag" + (price === t.free ? " free" : "")}>{price}</span>
        )}
        <Sources e={e} className="on-img" />
      </div>
      <div className="card-body">
        <h3 dir="auto">{e.title}</h3>
        <p className="meta">{fmtDate(e.start_at, t, { weekday: "long", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" })}</p>
        {(e.venue_name || e.area) && (
          <p className="meta">📍 {[e.venue_name, e.area && label(t.areas, e.area)].filter(Boolean).join(" · ")}</p>
        )}
        {e.lineup.length > 0 && <p className="meta">🎧 {e.lineup.slice(0, 4).join(" · ")}</p>}
        <div className="tags">
          {e.free_sold_out && !e.is_sold_out && !(e.group_has_free_entry ?? e.has_free_entry) && (
            <span className="tag sold">{t.freeSoldOut}</span>
          )}
          {e.genres.slice(0, 3).map((g) => <span key={g} className="tag">{genreLabel(lang, g)}</span>)}
          {!!e.min_age && <span className="tag age">{t.age(e.min_age)}</span>}
        </div>
      </div>
    </Link>
  );
}

/** Paged event grid for any /events filter set. */
export function EventGrid({ params }: { params: Params }) {
  const { t } = useT();
  const q = useEvents(params);
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  const total = q.data?.pages[0]?.total ?? 0;

  if (q.isError) return <p className="msg">{t.error}</p>;
  if (q.isPending) return <p className="msg">{t.loading}</p>;
  return (
    <>
      <p className="count">{t.results(total)}</p>
      {items.length === 0 && <p className="msg">{t.empty}</p>}
      <div className="grid">{items.map((e) => <Card key={e.id} e={e} />)}</div>
      {q.hasNextPage && (
        <div className="more">
          <button onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}>
            {q.isFetchingNextPage ? t.loading : t.loadMore}
          </button>
        </div>
      )}
    </>
  );
}
