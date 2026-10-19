import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useEvents, type Params } from "./api";
import { STRINGS, genreLabel, type Lang, type Strings } from "./i18n";
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

export function priceLabel(e: EventSummary, t: Strings) {
  if (e.entry_type === "free" || e.min_price === 0) return t.free;
  if (e.min_price == null) return e.has_free_entry ? t.free : "";
  return t.from + money(e.min_price, e.currency, t.locale);
}

export const venuePath = (name: string) => `/venue/${encodeURIComponent(name)}`;
export const artistPath = (name: string) => `/artist/${encodeURIComponent(name)}`;
// Pretty slug URLs (the API resolves either slug or id); same paths the server renders for crawlers.
export const eventPath = (e: { id: string; slug: string | null }) => `/event/${encodeURIComponent(e.slug ?? e.id)}`;

/** Browser tab title for the current page. */
export function useTitle(title: string | undefined) {
  useEffect(() => {
    if (title) document.title = `${title} · PartyLinks`;
  }, [title]);
}

// --------------------------------------------------------------------------- layout
export function Layout() {
  const { t, lang } = useT();
  const setLang = useStore((s) => s.setLang);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = t.dir;
  }, [lang, t.dir]);

  return (
    <>
      <header className="top">
        <div className="wrap top-row">
          <Link className="logo" to="/">Party<span>Links</span></Link>
          <nav className="nav">
            <NavLink to="/" end>{t.nav.parties}</NavLink>
            <NavLink to="/map">{t.nav.map}</NavLink>
            <NavLink to="/venues">{t.nav.venues}</NavLink>
            <NavLink to="/artists">{t.nav.artists}</NavLink>
          </nav>
          <button className="lang" onClick={() => setLang(lang === "he" ? "en" : "he")} aria-label="Language">
            {lang === "he" ? "EN" : "עב"}
          </button>
        </div>
      </header>
      <Outlet />
      <footer className="foot wrap">PartyLinks · Tel Aviv</footer>
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
export function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button className={"chip" + (on ? " on" : "")} aria-pressed={on} onClick={onClick}>
      {children}
    </button>
  );
}

export function Img({ src, alt, className }: { src: string | null | undefined; alt: string; className: string }) {
  const [broken, setBroken] = useState(false);
  if (!src || broken) return <div className={className + " noimg"} aria-hidden>♫</div>;
  return <img className={className} src={src} alt={alt} loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)} />;
}

export function Card({ e }: { e: EventSummary }) {
  const { t, lang } = useT();
  const price = priceLabel(e, t);
  return (
    <Link className="card" to={eventPath(e)}>
      <div className="card-img">
        <Img src={e.cover_url ?? e.image_url} alt={e.title} className="cover" />
        <div className="date-badge">
          <b>{fmtDate(e.start_at, t, { day: "numeric" })}</b>
          <span>{fmtDate(e.start_at, t, { month: "short" })}</span>
        </div>
        {e.is_sold_out ? (
          <span className="flag sold">{t.soldOut}</span>
        ) : (
          price && <span className={"flag" + (price === t.free ? " free" : "")}>{price}</span>
        )}
      </div>
      <div className="card-body">
        <h3 dir="auto">{e.title}</h3>
        <p className="meta">{fmtDate(e.start_at, t, { weekday: "long", hour: "2-digit", minute: "2-digit" })}</p>
        {(e.venue_name || e.area) && (
          <p className="meta">📍 {[e.venue_name, e.area && label(t.areas, e.area)].filter(Boolean).join(" · ")}</p>
        )}
        {e.lineup.length > 0 && <p className="meta">🎧 {e.lineup.slice(0, 4).join(" · ")}</p>}
        <div className="tags">
          {e.genres.slice(0, 3).map((g) => <span key={g} className="tag">{genreLabel(lang, g)}</span>)}
          {e.min_age && <span className="tag age">{t.age(e.min_age)}</span>}
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
