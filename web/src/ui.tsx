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
  mevalim: "Mevalim", hulyo: "Hulyo", gagarin: "Gagarin", megatickets: "Megatickets", eventbuzz: "Eventbuzz",
  popit: "Popit", epgb: "Radio EPGB", leaan: "Leaan", kupatbravo: "Kupat Bravo", comy: "Comy", eventim: "Eventim",
  ticketmaster: "Ticketmaster", pres: "PRES", smarticket: "Smarticket", secrettelaviv: "Secret Tel Aviv",
  tiktik: "TikTik", teder: "Teder", makore: "Makore", jerusalemtheatre: "Jerusalem Theatre", drop: "Drop",
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
          <Link className="logo" to="/">{lang === "he" ? <>פול<span>ס</span></> : <>Pul<span>se</span></>}</Link>
          <nav className="nav">
            <NavLink to="/" end>{t.nav.parties}</NavLink>
            <NavLink to="/map">{t.nav.map}</NavLink>
            <NavLink to="/abroad">{t.nav.abroad}</NavLink>
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
        {lang === "he" ? "פולס · ישראל" : "Pulse · Israel"} · <Link to="/accessibility">{t.accessibility}</Link>
        {" · "}<Link to="/terms">{t.terms}</Link>
        {" · "}<Link to="/privacy">{t.privacy}</Link>
        {" · "}<button className="linkish" onClick={openConsent}>{t.cookieSettings}</button>
        <p className="foot-disclaimer">{t.disclaimer}</p>
      </footer>
      {/* Phones: the main sections as a thumb-reach tab bar instead of a header row that scrolls off. */}
      <nav className="tabbar" aria-label={t.nav.parties}>
        {TABS.map(([to, key, icon]) => (
          <NavLink key={to} to={to} end={to === "/"}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{icon}</svg>
            <span>{t.nav[key]}</span>
          </NavLink>
        ))}
      </nav>
    </>
  );
}

const TABS: [string, keyof Strings["nav"], ReactNode][] = [
  ["/", "parties", <><path d="M4 9.5 12 4l8 5.5V20H4z" /><path d="M9.5 20v-6h5v6" /></>],
  ["/map", "map", <><path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z" /><circle cx="12" cy="9.5" r="2.5" /></>],
  ["/venues", "venues", <><path d="M3 20h18M5 20V9l7-5 7 5v11" /><path d="M10 20v-5h4v5" /></>],
  ["/artists", "artists", <><path d="M4 14v-2a8 8 0 0 1 16 0v2" /><rect x="3" y="14" width="4" height="6" rx="1.5" /><rect x="17" y="14" width="4" height="6" rx="1.5" /></>],
  ["/abroad", "abroad", <><path d="m2.5 13.5 19-7-4 13-5.5-4-3 3v-5z" /><path d="m12 15.5 9.5-9" /></>],
  ["/blog", "blog", <><path d="M5 4h11l3 3v13H5z" /><path d="M9 10h6M9 14h6M9 18h4" /></>],
];

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
    // The share links sit beside the card's link (links cannot nest), drawn inside its frame.
    <div className="card-wrap">
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
        <p className="meta">{fmtDate(e.start_at, t, { weekday: "short", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" })}</p>
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
    <ShareButton path={eventPath(e)} title={e.title} />
    </div>
  );
}

/** One compact share button: the phone's own share sheet where there is one, else the share links in a popover. */
export function ShareButton({ path, title }: { path: string; title: string }) {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (ev: Event) => {
      if (ev instanceof KeyboardEvent ? ev.key === "Escape" : !ref.current?.contains(ev.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);
  const click = () => {
    const url = window.location.origin + path;
    if (navigator.share && matchMedia("(pointer: coarse)").matches) navigator.share({ title, url }).catch(() => {});
    else setOpen(!open);
  };
  return (
    <div className="share-one" ref={ref}>
      <button className="share-btn" onClick={click} aria-label={t.share} title={t.share} aria-expanded={open}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <circle cx="18" cy="5" r="2.5" /><circle cx="6" cy="12" r="2.5" /><circle cx="18" cy="19" r="2.5" /><path d="m8.2 10.8 7.6-4.4M8.2 13.2l7.6 4.4" />
        </svg>
      </button>
      {open && <div className="share-pop"><Share path={path} title={title} /></div>}
    </div>
  );
}

const SHARE_ICONS: Record<string, ReactNode> = {
  whatsapp: (
    <>
      <path d="M12 2.5a9.5 9.5 0 0 0-8.2 14.3L2.5 21.5l4.8-1.3A9.5 9.5 0 1 0 12 2.5z" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M8.6 7.4c.2-.4.5-.5.8-.5h.6c.2 0 .4.1.5.4l.8 1.9c.1.2 0 .5-.1.7l-.6.7c-.1.2-.2.4 0 .6.6 1 1.4 1.8 2.4 2.4.2.1.5.1.6 0l.7-.8c.2-.2.4-.2.7-.1l1.8.9c.3.1.4.3.4.5v.6c0 .4-.2.9-.7 1.2-.6.4-1.5.6-2.6.2-1.9-.7-3.7-2.4-4.7-4.2-.6-1.1-.8-2.2-.6-3.1z" />
    </>
  ),
  facebook: <path d="M13.5 22v-8h2.7l.4-3.2h-3.1V8.8c0-.9.3-1.6 1.6-1.6h1.7V4.4c-.3 0-1.3-.1-2.5-.1-2.5 0-4.1 1.5-4.1 4.2v2.3H7.4V14h2.8v8z" />,
  x: <path d="M17.8 3h3.1l-6.8 7.7L22 21h-6.2l-4.9-6.4L5.3 21H2.2l7.2-8.3L1.8 3h6.4l4.4 5.8zm-1.1 16.2h1.7L7.4 4.7H5.6z" />,
};

/** WhatsApp / Facebook / X share links for a page of this site. */
export function Share({ path, title }: { path: string; title: string }) {
  const { t } = useT();
  const url = encodeURIComponent(window.location.origin + path);
  const text = encodeURIComponent(title);
  const targets = [
    ["whatsapp", "WhatsApp", `https://wa.me/?text=${text}%20${url}`],
    ["facebook", "Facebook", `https://www.facebook.com/sharer/sharer.php?u=${url}`],
    ["x", "X", `https://x.com/intent/post?text=${text}&url=${url}`],
  ];
  return (
    <div className="share">
      {targets.map(([key, name, href]) => (
        <a key={key} className={"share-btn " + key} href={href} target="_blank" rel="noopener noreferrer" aria-label={t.shareOn(name)} title={t.shareOn(name)}>
          <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden>{SHARE_ICONS[key]}</svg>
        </a>
      ))}
    </div>
  );
}

/** Paged event grid for any /events filter set. */
export function EventGrid({ params, empty }: { params: Params; empty?: ReactNode }) {
  const { t } = useT();
  const q = useEvents(params);
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  const total = q.data?.pages[0]?.total ?? 0;

  if (q.isError) return <p className="msg">{t.error}</p>;
  if (q.isPending) return <p className="msg">{t.loading}</p>;
  return (
    <>
      <p className="count">{t.results(total)}</p>
      {items.length === 0 && (empty ?? <p className="msg">{t.empty}</p>)}
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
