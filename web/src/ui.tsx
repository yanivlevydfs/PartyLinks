import { Fragment, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import sourceNames from "./sourceNames.json";
import { Link, NavLink, Outlet, ScrollRestoration } from "react-router-dom";
import { useCategories, useEvents, useLandingInfo, type Params } from "./api";
import { CITIES, STRINGS, genreLabel, type Lang, type Strings } from "./i18n";
import { CookieConsent, useConsent } from "./consent";
import { InstallBanner } from "./Install";
import { INFO_LINKS } from "./pages/Info";
import { AdLabel, hit, SponsoredFrame, SponsoredStrip, useAdView } from "./ads/Sponsored";
import { forecastAt, Weather } from "./Weather";
import { PlanButton } from "./plans/PlanButton";
import { AccountSlot } from "./accounts/AccountSlot";
import { Bell } from "./alerts/Bell";
import { SaveButton } from "./saved/SaveButton";
import { SavedLink } from "./saved/SavedLink";
import { useMe } from "./accounts/useMe";
import { useStore } from "./store";
import type { EventPage, EventSummary } from "./types";

export const TZ = "Asia/Jerusalem";
export const today = () => new Date().toLocaleDateString("en-CA", { timeZone: TZ });
export const tomorrow = () => new Date(Date.parse(today() + "T12:00:00Z") + 864e5).toISOString().slice(0, 10);

export function useT(): { t: Strings; lang: Lang } {
  const lang = useStore((s) => s.lang);
  return { t: STRINGS[lang] as Strings, lang };
}

/** Label for an API enum value from one of the i18n maps, falling back to the raw value. */
export const label = (map: object, key: string | null | undefined) =>
  key ? ((map as Record<string, string>)[key] ?? key) : "";

/** Facets in the order of the names their chips show: א-ת / A-Z (each keeps its count). */
export const byName = <T extends { value: string }>(xs: readonly T[] | undefined, name: (v: string) => string, lang: string) =>
  [...(xs ?? [])].sort((a, b) => name(a.value).localeCompare(name(b.value), lang));

export function money(value: number, currency: string, locale: string) {
  return new Intl.NumberFormat(locale, { style: "currency", currency, maximumFractionDigits: 0 }).format(value);
}

export function fmtDate(iso: string | null, t: Strings, opts: Intl.DateTimeFormatOptions) {
  return iso ? new Date(iso).toLocaleString(t.locale, { timeZone: TZ, ...opts }) : "";
}

/** An event's start (or ``iso``) in the venue's own zone: abroad, its local time, not Israel's. A date
 *  alone (the seller gave no time) shows the day only, never a made-up 00:00. */
export function when(e: Pick<EventSummary, "start_at" | "time_known" | "time_zone" | "local_date">, t: Strings,
  opts: Intl.DateTimeFormatOptions, iso = e.start_at) {
  if (!iso) return "";
  if (e.time_known === false) {
    const { hour: _h, minute: _m, ...day } = opts;
    if (iso !== e.start_at || !e.local_date || !Object.keys(day).length) return "";
    return new Date(e.local_date + "T12:00:00Z").toLocaleString(t.locale, { ...day, timeZone: "UTC" });
  }
  return new Date(iso).toLocaleString(t.locale, { timeZone: e.time_zone ?? TZ, ...opts });
}

export function priceLabel(e: Pick<EventSummary, "entry_type" | "min_price" | "has_free_entry" | "currency">, t: Strings) {
  if (e.entry_type === "free" || e.min_price === 0) return t.free;
  if (e.min_price == null) return e.has_free_entry ? t.free : "";
  return t.from + money(e.min_price, e.currency, t.locale);
}

// Display names of the ticket sites we scrape (keys = EventSummary.source).
const SOURCE_NAMES: Record<string, string> = sourceNames; // web/src/sourceNames.json: the server reads it too (event artwork)
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
    if (title) document.title = `${title} · SPOTX`;
  }, [title]);
}

// --------------------------------------------------------------------------- layout
// The home page and its filter pages (/genre/x, /tonight...) are one page: picking a filter keeps the scroll.
const LANDING = /^\/(tonight|weekend|genre|city|region)?(\/|$)/;

export function Layout() {
  const { t, lang } = useT();
  const setLang = useStore((s) => s.setLang);
  const me = useMe().data;
  const cats = useCategories().data;
  const openConsent = useConsent((s) => s.openSettings);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = t.dir;
  }, [lang, t.dir]);

  return (
    <>
      {/* A new page starts at the top (an event opened from a scrolled list started near its bottom); back restores. */}
      <ScrollRestoration getKey={(loc) => (LANDING.test(loc.pathname) ? "landing" : loc.key)} />
      <CookieConsent />
      <InstallBanner />
      <header className="top">
        <div className="wrap top-row">
          <Link className="logo" to="/" aria-label="SPOTX">
            <Mark />
            <span className="word">SPOT<span>X</span></span>
          </Link>
          <nav className="nav">
            <NavLink to="/" end>{t.nav.parties}</NavLink>
            <NavLink to="/map">{t.nav.map}</NavLink>
            <NavLink to="/abroad">{t.nav.abroad}</NavLink>
            <NavLink to="/venues">{t.nav.venues}</NavLink>
            <NavLink to="/artists">{t.nav.artists}</NavLink>
            <NavLink to="/stores">{t.nav.stores}</NavLink>
            <NavLink to="/blog">{t.nav.blog}</NavLink>
          </nav>
          <button className={"lang" + (me ? " lang-signed-in" : "")} onClick={() => setLang(lang === "he" ? "en" : "he")} aria-label="Language">
            {lang === "he" ? "EN" : "עב"}
          </button>
          <Bell />
          <SavedLink />
          <AccountSlot />
        </div>
      </header>
      <SponsoredFrame>
      <Outlet />
      <SponsoredStrip />
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
        {lang === "he" ? "SPOTX · ישראל" : "SPOTX · Israel"}
        {INFO_LINKS.map((l) => <Fragment key={l.path}>{" · "}<Link to={l.path}>{l.label[lang]}</Link></Fragment>)}
        {" · "}<Link to="/accessibility">{t.accessibility}</Link>
        {" · "}<Link to="/terms">{t.terms}</Link>
        {" · "}<Link to="/privacy">{t.privacy}</Link>
        {" · "}<button className="linkish" onClick={openConsent}>{t.cookieSettings}</button>
        <p className="foot-disclaimer">{t.disclaimer}</p>
        <p className="foot-copy">© {new Date().getFullYear()} SPOTX · {t.rights} · v{__VERSION__}</p>
      </footer>
      </SponsoredFrame>
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
      <div className="hero-fx" aria-hidden />
      <div className="wrap">
        <h1 dir="auto">{title}</h1>
        {children}
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- bits
/** A drop-down in the site's own look (a native select opens the phone's system picker). */
export function Select({ value, options, onChange, label }: { value: string; options: [string, ReactNode][]; onChange: (v: string) => void; label: string }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    box.current?.querySelector<HTMLElement>("[aria-selected=true]")?.focus();
    const away = (e: PointerEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    addEventListener("pointerdown", away);
    return () => removeEventListener("pointerdown", away);
  }, [open]);
  const pick = (v: string) => { onChange(v); setOpen(false); box.current?.querySelector<HTMLElement>(".dd-btn")?.focus(); };
  const keys = (e: KeyboardEvent) => {
    if (e.key === "Escape" && open) { e.stopPropagation(); setOpen(false); box.current?.querySelector<HTMLElement>(".dd-btn")?.focus(); }
    if (open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      const items = [...box.current!.querySelectorAll<HTMLElement>("[role=option]")];
      const i = items.indexOf(document.activeElement as HTMLElement);
      items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
    }
  };
  return (
    <div className={"dd" + (open ? " open" : "")} ref={box} onKeyDown={keys}>
      <button type="button" className="dd-btn" aria-haspopup="listbox" aria-expanded={open} aria-label={label} onClick={() => setOpen(!open)}>
        <span>{options.find(([v]) => v === value)?.[1] ?? options[0]?.[1]}</span><span className="caret" aria-hidden>▾</span>
      </button>
      {open && (
        <div className="dd-list" role="listbox" aria-label={label}>
          {options.map(([v, name]) => (
            <button type="button" key={v} role="option" aria-selected={v === value} onClick={() => pick(v)}>{name}</button>
          ))}
        </div>
      )}
    </div>
  );
}

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

/** Our stored artwork (``/images/123``) resized to ``width`` as WebP (640 cards, 1280 event page);
 *  the sellers' own image URLs pass through. og:image keeps the original (server side). */
export function sized<T extends string | null | undefined>(src: T, width: 640 | 1280): T | string {
  // Stored pictures and drawn cards (events with no picture) both come in 640 and 1280 WebP.
  return src && /^\/(images|artwork)\/\d+$/.test(src) ? `${src}/${width}.webp` : src;
}

/** Without artwork: ``text`` (the event title) set large on the gradient, else a note. */
export function Img({ src, alt, className, text, eager }: { src: string | null | undefined; alt: string; className: string; text?: string; eager?: boolean }) {
  const [broken, setBroken] = useState(false);
  if (!src || broken)
    return text ? (
      <div className={className + " noimg text"} aria-hidden><span dir="auto">{text}</span></div>
    ) : (
      <div className={className + " noimg"} aria-hidden>♫</div>
    );
  return <img className={className} src={src} alt={alt} loading={eager ? "eager" : "lazy"} referrerPolicy="no-referrer" onError={() => setBroken(true)} />;
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
  const ad = useAdView<HTMLAnchorElement>(e.sponsored, true, "results");
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
    <div className={"card-wrap" + (e.sponsored ? " ad" : "")}>
    <Link className="card" to={eventPath(e)} ref={ad} onClick={() => e.sponsored && hit(e.sponsored, "click", "results")}>
      <div className="card-img">
        <Img src={sized(e.cover_url ?? e.image_url, 640)} alt={e.title} className="cover" text={e.title} />
        <div className="date-badge">
          <b>{when(e, t, { day: "numeric" })}</b>
          <span>{when(e, t, { month: "short" })}</span>
          <Weather lat={e.latitude} lng={e.longitude} city={e.city} start={forecastAt(e)} zone={e.time_zone} />
        </div>
        {/* A paid card says so; that outranks new / updated. */}
        {e.sponsored ? <AdLabel />
          : fresh && <span className={`fresh ${fresh}`}>{fresh === "new" ? t.isNew : t.updatedBadge}</span>}
        {e.is_sold_out ? (
          <span className="flag sold">{t.soldOut}</span>
        ) : (
          price && <span className={"flag" + (price === t.free ? " free" : "")}>{price}</span>
        )}
        <Sources e={e} className="on-img" />
      </div>
      <div className="card-body">
        <h3 dir="auto">{e.title}</h3>
        <p className="meta">{when(e, t, { weekday: "short", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" })}</p>
        {(e.venue_name || e.area) && (
          <p className="meta">📍 {[e.venue_name, e.area && label(t.areas, e.area)].filter(Boolean).join(" · ")}</p>
        )}
        {e.lineup.length > 0 && <p className="meta">🎧 {e.lineup.slice(0, 4).join(" · ")}</p>}
        <div className="tags">
          {e.free_sold_out && !e.is_sold_out && !(e.group_has_free_entry ?? e.has_free_entry) && (
            <span className="tag sold">{t.freeSoldOut}</span>
          )}
          {e.has_discount && !e.is_sold_out && <span className="tag deal">{t.discountOnly}</span>}
          {e.genres.slice(0, 3).map((g) => <span key={g} className="tag">{genreLabel(lang, g)}</span>)}
          {!!e.min_age && <span className="tag age">{t.age(e.min_age)}</span>}
        </div>
      </div>
    </Link>
    <SaveButton eventId={e.id} />
    {e.start_at && <AddToCalendar e={e} start={e.start_at} compact />}
    <ShareButton path={eventPath(e)} title={e.title} />
    <PlanButton eventId={e.id} compact />
    </div>
  );
}

/** Ref for a popover: a press outside it or Escape calls ``close`` while it is open. */
function useDismiss<T extends HTMLElement>(open: boolean, close: () => void) {
  const ref = useRef<T>(null);
  useEffect(() => {
    if (!open) return;
    const handler = (ev: Event) => {
      if (ev instanceof KeyboardEvent ? ev.key === "Escape" : !ref.current?.contains(ev.target as Node)) close();
    };
    document.addEventListener("pointerdown", handler);
    document.addEventListener("keydown", handler);
    return () => {
      document.removeEventListener("pointerdown", handler);
      document.removeEventListener("keydown", handler);
    };
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  return ref;
}

/** Google and Outlook take the event in a link; Apple and every other calendar import an .ics file. */
export function AddToCalendar({ e, start, compact }: { e: EventSummary; start: string; compact?: boolean }) {
  const { t } = useT();
  const from = new Date(start);
  // ponytail: no end time listed = 3 hours, a guess the user can edit in their calendar.
  const to = e.end_at ? new Date(e.end_at) : new Date(from.getTime() + 3 * 3600000);
  const utc = (d: Date) => d.toISOString().replace(/[-:]|\.\d{3}/g, "");
  // A date alone (no time from the seller): an all-day entry on that day.
  const day = e.time_known === false && e.local_date;
  const next = day && new Date(Date.parse(day + "T12:00:00Z") + 864e5).toISOString().slice(0, 10);
  const url = window.location.origin + eventPath(e);
  const where = [e.venue_name, e.address, e.city].filter(Boolean).join(", ");
  const q = (o: Record<string, string>) => new URLSearchParams(o).toString();
  const google = "https://calendar.google.com/calendar/render?" + q({ action: "TEMPLATE", text: e.title, location: where, details: url,
    dates: day && next ? `${day.replace(/-/g, "")}/${next.replace(/-/g, "")}` : `${utc(from)}/${utc(to)}` });
  const outlook = "https://outlook.live.com/calendar/0/action/compose?" + q({ path: "/calendar/action/compose", rru: "addevent", subject: e.title, location: where, body: url,
    ...(day && next ? { startdt: day, enddt: next, allday: "true" } : { startdt: from.toISOString(), enddt: to.toISOString() }) });
  const esc = (s: string) => s.replace(/[\\;,]/g, "\\$&").replace(/\r?\n/g, "\\n");
  const ics = [
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//PartyLinks//EN", "BEGIN:VEVENT", `UID:${e.id}@partylinks`, `DTSTAMP:${utc(new Date())}`,
    ...(day && next ? [`DTSTART;VALUE=DATE:${day.replace(/-/g, "")}`, `DTEND;VALUE=DATE:${next.replace(/-/g, "")}`] : [`DTSTART:${utc(from)}`, `DTEND:${utc(to)}`]),
    `SUMMARY:${esc(e.title)}`, where && `LOCATION:${esc(where)}`, `URL:${url}`, `DESCRIPTION:${esc(url)}`,
    "END:VEVENT", "END:VCALENDAR",
  ].filter(Boolean).join("\r\n");
  const [open, setOpen] = useState(false);
  const ref = useDismiss<HTMLDetailsElement>(open, () => setOpen(false));
  return (
    // Closes on a press outside, Escape, or once a calendar is picked.
    <details ref={ref} open={open} onToggle={(ev) => setOpen(ev.currentTarget.open)} className={"cal" + (compact ? " on-card" : "")}>
      <summary aria-label={t.addToCal} title={t.addToCal}>📅{!compact && " " + t.addToCal}</summary>
      <div className="cal-menu" onClick={() => setOpen(false)}>
        <a href={google} target="_blank" rel="noopener noreferrer">Google ↗</a>
        <a href={outlook} target="_blank" rel="noopener noreferrer">Outlook ↗</a>
        <a href={"data:text/calendar;charset=utf-8," + encodeURIComponent(ics)} download={`${e.slug || e.id}.ics`}>{t.calOther}</a>
      </div>
    </details>
  );
}

/** One compact share button: the phone's own share sheet where there is one, else the share links in a popover. */
export function ShareButton({ path, title }: { path: string; title: string }) {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const ref = useDismiss<HTMLDivElement>(open, () => setOpen(false));
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
      {/* Everything else (Telegram, Instagram, copy link…) through the device's own share sheet. */}
      {typeof navigator.share === "function" && (
        <button className="share-btn" onClick={() => navigator.share({ title, url: window.location.origin + path }).catch(() => {})} aria-label={t.share} title={t.share}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <circle cx="18" cy="5" r="2.5" /><circle cx="6" cy="12" r="2.5" /><circle cx="18" cy="19" r="2.5" /><path d="m8.2 10.8 7.6-4.4M8.2 13.2l7.6 4.4" />
          </svg>
        </button>
      )}
    </div>
  );
}

/** Card-shaped placeholders while a list loads. */
export function CardSkeletons({ n }: { n: number }) {
  return (
    <>
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="card sk-card" aria-hidden>
          <div className="card-img" />
          <div className="card-body">
            <span className="sk sk-line" />
            <span className="sk sk-line short" />
            <span className="sk sk-line short" />
          </div>
        </div>
      ))}
    </>
  );
}

/** An email address that is never in the page as text: GET /site sends it reversed and base64-encoded,
 * and only a person's click decodes it and links it (harvesters read the HTML, the JSON and mailto links). */
export function HiddenEmail({ code, subject }: { code: string | undefined; subject?: string }) {
  const { lang } = useT();
  const [email, setEmail] = useState<string | null>(null);
  if (!code) return null;
  if (!email)
    return (
      <button type="button" className="link-btn" onClick={() => setEmail([...atob(code)].reverse().join(""))}>
        {lang === "he" ? "הצגת כתובת הדוא\"ל" : "Show email address"}
      </button>
    );
  return <a href={`mailto:${email}${subject ? `?subject=${encodeURIComponent(subject)}` : ""}`} dir="ltr">{email}</a>;
}

/** The brand mark: a gradient map pin with a glowing spot, standing on a ring. Same drawing as favicon.svg
 * and the boot screen in index.html. Gradient ids are per instance (several marks share a page). */
export function Mark() {
  const id = useId();
  return (
    <svg className="mark" viewBox="12 5 40 56" aria-hidden="true">
      <defs>
        <linearGradient id={`${id}g`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#ff2e93" /><stop offset=".55" stopColor="#9d4dff" /><stop offset="1" stopColor="#19e3ff" /></linearGradient>
        <linearGradient id={`${id}s`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#fff" stopOpacity=".45" /><stop offset=".5" stopColor="#fff" stopOpacity="0" /></linearGradient>
        <radialGradient id={`${id}d`} cx=".4" cy=".35" r=".7"><stop offset="0" stopColor="#fff" /><stop offset=".6" stopColor="#ffd0e8" /><stop offset="1" stopColor="#ff2e93" /></radialGradient>
      </defs>
      <ellipse className="ring" cx="32" cy="56" rx="12" ry="3.4" stroke={`url(#${id}g)`} />
      <g className="pin">
        <path d="M32 56C32 56 14 39 14 25.5a18 18 0 0 1 36 0C50 39 32 56 32 56Z" fill={`url(#${id}g)`} />
        <path d="M32 56C32 56 14 39 14 25.5a18 18 0 0 1 36 0C50 39 32 56 32 56Z" fill={`url(#${id}s)`} />
        <circle cx="32" cy="25.5" r="9" fill="#0b0616" />
        <circle className="dot" cx="32" cy="25.5" r="4.6" fill={`url(#${id}d)`} />
      </g>
    </svg>
  );
}

/** The brand loader: the mark bouncing on its ring over the SPOTX wordmark. `small` (mark only) sits inline in a button or bar. */
export function Loader({ small }: { small?: boolean }) {
  const { t } = useT();
  return (
    <span className={small ? "loader small" : "loader"} role="status" aria-label={t.loading}>
      <Mark />
      {!small && <b className="word">SPOT<span>X</span></b>}
    </span>
  );
}

/** The list while it loads: its count line (blank, so the cards do not move down when it fills) and card skeletons. */
export function GridLoading() {
  const { t } = useT();
  return <><p className="count" aria-hidden>&nbsp;</p><div className="grid" aria-busy="true" aria-label={t.loading}><CardSkeletons n={8} /></div></>;
}

/** Paged event grid for any /events filter set; matching paid placements first, marked. */
export function EventGrid({ params, empty }: { params: Params; empty?: (page?: EventPage) => ReactNode }) {
  const { t } = useT();
  const q = useEvents({ ...params, sponsored: "true" });
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  const total = q.data?.pages[0]?.total ?? 0;

  if (q.isError) return <p className="msg">{t.error}</p>;
  if (q.isPending) return <GridLoading />;
  return (
    <>
      <p className="count">{t.results(total)}</p>
      {items.length === 0 && (empty?.(q.data?.pages[0]) ?? <p className="msg">{t.empty}</p>)}
      <div className={"grid" + (q.isPlaceholderData ? " stale" : "")} aria-busy={q.isPlaceholderData}>{items.map((e) => <Card key={e.id} e={e} />)}</div>
      {q.hasNextPage && (
        <div className="more">
          <button onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}>
            {q.isFetchingNextPage ? <Loader small /> : t.loadMore}
          </button>
        </div>
      )}
    </>
  );
}

/** Links to the page's neighbours (a city's dates, genres, venues and nearby cities...), from the server. */
export function Related({ path }: { path: string }) {
  const { lang } = useT();
  const links = useLandingInfo(path, lang).data?.links ?? [];
  if (!links.length) return null;
  return (
    <nav className="related" aria-label={lang === "he" ? "עוד באזור" : "Explore more"}>
      <h2>{lang === "he" ? "עוד באזור" : "Explore more"}</h2>
      <div className="chips">{links.map((l) => <Link key={l.path} className="chip" to={l.path}>{l.label}</Link>)}</div>
    </nav>
  );
}
