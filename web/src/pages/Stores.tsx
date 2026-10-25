// Every ticket site / box office the events come from, with how many upcoming events each sells (GET /ticket-sites).
// Crawlers without JavaScript get a summary from seo.py (INFO_PAGES).
import type { CSSProperties } from "react";
import { useNavigate } from "react-router-dom";
import { type TicketSite, useCategories, useTicketSites } from "../api";
import type { Lang } from "../i18n";
import { DEFAULT_FILTERS, useStore } from "../store";
import { Hero, Loader, sourceName, useT, useTitle } from "../ui";

type Kind = "tickets" | "venue" | "culture" | "cinema" | "abroad" | "guide" | "city";
const KINDS: Record<Kind, Record<Lang, string>> = {
  tickets: { he: "כרטיסים", en: "Tickets" },
  venue: { he: "מועדון", en: "Club" },
  culture: { he: "תרבות ובמה", en: "Stage & culture" },
  cinema: { he: "קולנוע", en: "Cinema" },
  abroad: { he: "חו״ל", en: "Abroad" },
  guide: { he: "מדריך", en: "Guide" },
  city: { he: "רשות מקומית", en: "Municipality" },
};

// Store home page and kind; a source missing here still shows, without a link.
const STORES: Record<string, [string | null, Kind]> = {
  goout: ["https://www.go-out.co", "tickets"], eventer: ["https://www.eventer.co.il", "tickets"],
  zygo: ["https://zygo.co.il", "tickets"], tickchak: ["https://live.tickchak.co.il", "tickets"],
  selector: ["https://selector.org.il", "tickets"], chillz: ["https://chillz.com", "tickets"],
  mevalim: ["https://www.mevalim.co.il", "tickets"], megatickets: ["https://www.megatickets.co.il", "tickets"],
  eventbuzz: ["https://eventbuzz.co.il", "tickets"], popit: ["https://popit.events", "tickets"],
  leaan: ["https://www.leaan.co.il", "tickets"], ticketmaster: ["https://www.ticketmaster.co.il", "tickets"],
  kupatbravo: ["https://kupatbravo.co.il", "tickets"], tiktik: ["https://www.tiktik-online.co.il", "tickets"],
  eventim: ["https://www.eventim.co.il", "tickets"], smarticket: ["https://smarticket.co.il", "tickets"],
  pres: ["https://pres.global", "tickets"], drop: ["https://tdrop.net", "tickets"], vibez: ["https://web.vibez.io", "tickets"],
  shotgun: ["https://shotgun.live", "tickets"], luma: ["https://luma.com", "tickets"], meetup: ["https://www.meetup.com", "tickets"],
  arenas: ["https://live.tickchak.co.il", "tickets"], barak: ["https://www.barak-tickets.co.il", "tickets"],
  comy: ["https://comy.co.il", "tickets"],
  barby: ["https://barby.co.il", "venue"], grayclub: ["https://grayclub.co.il", "venue"], epgb: ["https://epgb.co.il", "venue"],
  teder: ["https://www.teder.fm", "venue"], gagarin: ["https://gagarin.co.il", "venue"], zappa: ["https://www.zappa-club.co.il", "venue"],
  kikarhamusica: ["https://kikar-hamusica.com/shows/he", "venue"],
  yellowsubmarine: ["https://yellowsubmarine.org.il", "venue"], halutz: ["https://www.halutz.co.il", "venue"],
  haezor: ["https://haezor.com", "venue"], levontin7: ["https://levontin7.com", "venue"], babebar: ["https://babebar.co.il", "venue"],
  bargiyora: ["https://bar-giyora.co.il", "venue"],
  makore: ["https://www.makore.co.il", "culture"], jerusalemtheatre: ["https://www.jerusalem-theatre.co.il", "culture"],
  beershevatheatre: ["https://b7t.co.il", "culture"], hatarbut: ["https://www.hatarbut.co.il", "culture"],
  habama: ["https://www.habama.co.il", "culture"], friendshist: ["https://friends-hist.co.il", "culture"],
  hamenagen: ["https://hamenagen.net", "culture"], poalimatarim: ["https://poalimatarim.com", "culture"],
  geektime: ["https://www.geektime.co.il/event/", "culture"], peoplecomputers: ["https://events.co.il", "culture"],
  planet: ["https://www.planetcinema.co.il", "cinema"], ravhen: ["https://www.rav-hen.co.il", "cinema"],
  cinemacity: ["https://www.cinema-city.co.il", "cinema"], hotcinema: ["https://www.hotcinema.co.il", "cinema"],
  lev: ["https://www.lev.co.il", "cinema"], cinematheque_tlv: ["https://www.cinema.co.il", "cinema"],
  cinematheque_jlm: ["https://www.jer-cin.org.il", "cinema"], movieland: ["https://movieland.co.il", "cinema"],
  hulyo: ["https://www.hulyo.co.il", "abroad"], kishreyteufa: ["https://www.kishrey-teufa.co.il", "abroad"],
  ticketplace: ["https://ticketplace.co.il", "abroad"], livevents: ["https://livevents.co.il", "abroad"],
  issta: ["https://www.issta.co.il", "abroad"], megaevents: ["https://www.mega-events.co.il", "abroad"],
  viagogo: ["https://www.viagogo.com", "abroad"], xplorer: ["https://www.xplorer.co.il", "abroad"],
  livetickets: ["https://www.livetickets.co.il", "abroad"], tickethouse: ["https://www.ticket-house.com", "abroad"],
  secrettelaviv: ["https://www.secrettelaviv.com", "guide"], beatradar: ["https://beatradar.co.il", "guide"],
  happytlv: ["https://www.happytlv.com", "guide"], tiuli: ["https://www.tiuli.com", "guide"],
  festivalim: ["https://www.festivalim.co.il", "guide"], ogo: ["https://www.ogo.co.il", "guide"], muzi: ["https://muzi.co.il", "guide"],
  airdrop: ["https://airdrop.co.il", "guide"],
  telaviv: ["https://www.tel-aviv.gov.il", "city"], haifamuni: ["https://www.haifa.muni.il", "city"],
  ramatganmuni: ["https://www.ramat-gan.muni.il", "city"], holon: ["https://www.holon.muni.il", "city"],
  munievents: [null, "city"], matnasnet: [null, "city"], onecity: [null, "city"],
};

const TEXT = {
  he: {
    title: "חנויות הכרטיסים",
    intro: "פולס לא מוכר כרטיסים. כל אירוע באתר מגיע מאחד האתרים האלה, ולחיצה על \"כרטיסים\" מעבירה ישר אליהם. תודה לכולם.",
    total: (events: string, stores: number) => `${events} אירועים קרובים מ-${stores} מקורות`,
    events: "אירועים קרובים",
    site: "לאתר",
    browse: "לאירועים שלהם",
    updated: "עודכן",
  },
  en: {
    title: "Ticket stores",
    intro: "Pulse doesn't sell tickets. Every event here comes from one of these sites, and \"Tickets\" takes you straight to them. Thank you all.",
    total: (events: string, stores: number) => `${events} upcoming events from ${stores} sources`,
    events: "upcoming events",
    site: "Website",
    browse: "Their events",
    updated: "Updated",
  },
};

function ago(iso: string, locale: string): string {
  const minutes = Math.round((Date.parse(iso) - Date.now()) / 60_000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (Math.abs(minutes) < 60) return rtf.format(minutes, "minute");
  if (Math.abs(minutes) < 48 * 60) return rtf.format(Math.round(minutes / 60), "hour");
  return rtf.format(Math.round(minutes / 1440), "day");
}

// The same store always gets the same neon hue for its monogram.
const hue = (name: string) => [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const domain = (url: string) => new URL(url).hostname.replace(/^(www|live|web)\./, "");

export default function StoresPage() {
  const { t: strings, lang } = useT();
  const t = TEXT[lang];
  useTitle(t.title);
  const sites = useTicketSites();
  const cats = useCategories();
  // Stores with events in Israel can be browsed on the home page (its ?source= filter).
  const browsable = new Set(cats.data?.sources.map((s) => s.value));
  // /ticket-sites down (a server without it, a restart): the home filter's counts, Israel only.
  const fallback: TicketSite[] | undefined = sites.isError ? cats.data?.sources.map((s) => ({ name: s.value, events: s.events, updated_at: null })) : undefined;
  const stores = sites.data?.stores ?? fallback;
  const failed = sites.isError && cats.isError;
  const setFilters = useStore((s) => s.setFilters);
  const setQuizDone = useStore((s) => s.setQuizDone);
  const navigate = useNavigate();
  const browse = (name: string) => {
    setFilters({ ...DEFAULT_FILTERS, shops: [name] });
    setQuizDone(true);
    navigate("/");
  };
  const max = stores?.[0]?.events ?? 1;
  const total = stores?.reduce((n, s) => n + s.events, 0) ?? 0;

  return (
    <>
      <Hero title={t.title}>
        <p className="landing-intro">{t.intro}</p>
        {stores && <p className="stores-total">{t.total(total.toLocaleString(strings.locale), stores.length)}</p>}
      </Hero>
      <main className="wrap">
        {!stores && (failed ? <p className="msg">{strings.error}</p> : <Loader />)}
        <ol className="stores">
          {stores?.map((s, i) => {
            const [url, kind] = STORES[s.name] ?? [null, "tickets"];
            return (
              <li key={s.name} className="store" style={{ "--hue": hue(s.name), animationDelay: `${Math.min(i, 20) * 30}ms` } as CSSProperties}>
                <span className="store-rank">{i + 1}</span>
                <span className="store-mono" aria-hidden>{sourceName(s.name).replace(/^the /i, "").charAt(0).toUpperCase()}</span>
                <div className="store-head">
                  <b dir="ltr">{sourceName(s.name)}</b>
                  <small>{KINDS[kind][lang]}{url && <> · <span dir="ltr">{domain(url)}</span></>}</small>
                </div>
                <div className="store-count">
                  <b>{s.events.toLocaleString(strings.locale)}</b>
                  <small>{t.events}</small>
                </div>
                <span className="store-bar"><span style={{ width: `${Math.max(3, (s.events / max) * 100)}%` }} /></span>
                <div className="store-foot">
                  {s.updated_at && <small>{t.updated} {ago(s.updated_at, strings.locale)}</small>}
                  {browsable.has(s.name) && <button className="linkish" onClick={() => browse(s.name)}>{t.browse}</button>}
                  {url && <a href={url} target="_blank" rel="noopener noreferrer">{t.site} ↗</a>}
                </div>
              </li>
            );
          })}
        </ol>
      </main>
    </>
  );
}
