// Every ticket site / box office the events come from, with how many upcoming events each sells (GET /ticket-sites).
// Crawlers without JavaScript get a summary from seo.py (INFO_PAGES).
import { type CSSProperties, type ReactNode, useDeferredValue, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { type BoxOffice, type TicketSite, useCategories, useLoadShare, useTicketSites } from "../api";
import type { Lang } from "../i18n";
import { DEFAULT_FILTERS, useStore } from "../store";
import { Hero, Loader, sourceName, useT, useTitle } from "../ui";
import { outbound } from "../utm";

// In screen order: the sections the stores are grouped in, with their headings.
type Kind = "tickets" | "culture" | "venue" | "cinema" | "guide" | "city" | "abroad";
const KINDS: Record<Kind, Record<Lang, string>> = {
  tickets: { he: "אתרי כרטיסים", en: "Ticket sites" },
  culture: { he: "תרבות ובמה", en: "Stage & culture" },
  venue: { he: "מועדונים והופעות", en: "Clubs & venues" },
  cinema: { he: "קולנוע", en: "Cinema" },
  guide: { he: "מדריכים ולוחות אירועים", en: "Guides & listings" },
  city: { he: "רשויות מקומיות", en: "Municipalities" },
  abroad: { he: "חו״ל", en: "Abroad" },
};
const KIND_ORDER = Object.keys(KINDS) as Kind[];
const CITY_CHIPS = 12; // the cities with the most box-office events get a chip

// Store home page and kind; a source missing here still shows, without a link.
const STORES: Record<string, [string | null, Kind]> = {
  goout: ["https://www.go-out.co", "tickets"], eventer: ["https://www.eventer.co.il", "tickets"],
  zygo: ["https://zygo.co.il", "tickets"], goshow: ["https://www.goshow.co.il", "tickets"], tzkrti: ["https://tzkrti.com", "tickets"], fever: ["https://feverup.com/en/tel-aviv-israel", "tickets"], towerofdavid: ["https://www.tod.org.il", "tickets"], tamuseum: ["https://www.tamuseum.org.il", "tickets"], tickchak: ["https://live.tickchak.co.il", "tickets"],
  selector: ["https://selector.org.il", "tickets"], chillz: ["https://chillz.com", "tickets"],
  mevalim: ["https://www.mevalim.co.il", "tickets"], megatickets: ["https://www.megatickets.co.il", "tickets"], iwant: ["https://iwant.co.il", "tickets"], music80s: ["https://80smusic.co.il/pages/parties", "guide"],
  eventbuzz: ["https://eventbuzz.co.il", "tickets"], popit: ["https://popit.events", "tickets"],
  leaan: ["https://www.leaan.co.il", "tickets"], ticketmaster: ["https://www.ticketmaster.co.il", "tickets"],
  kupatbravo: ["https://kupatbravo.co.il", "tickets"], tiktik: ["https://www.tiktik-online.co.il", "tickets"],
  eventim: ["https://www.eventim.co.il", "tickets"], smarticket: ["https://smarticket.co.il", "tickets"],
  pres: ["https://pres.global", "tickets"], pres_il: ["https://pres.global", "tickets"], smarticket_il: ["https://www.smarticket.co.il", "tickets"], drop: ["https://tdrop.net", "tickets"], vibez: ["https://web.vibez.io", "tickets"],
  shotgun: ["https://shotgun.live", "tickets"], luma: ["https://luma.com", "tickets"], meetup: ["https://www.meetup.com", "tickets"],
  arenas: ["https://live.tickchak.co.il", "tickets"], barak: ["https://www.barak-tickets.co.il", "tickets"],
  ticket4u: ["https://www.ticket4u.co.il", "tickets"], havanaclub: ["https://www.havanaclub.co.il", "venue"],
  parties247: ["https://www.parties247.co.il", "guide"], newacropolis: ["https://www.newacropolis.org.il", "culture"],
  beithagefen: ["https://www.beit-hagefen.com", "culture"], hamesila: ["https://hamesila.org.il", "venue"],
  ascolot: ["https://extra-academic.openu.ac.il/sites/ascolot/", "culture"],
  jerusalemarts: ["https://jerusalemarts.co.il", "culture"], glatticket: ["https://home.glatticket.co.il", "tickets"],
  bimot: ["https://bimotglobal.co.il", "culture"],
  israelshow: ["https://israelshow.co.il", "tickets"],
  cultureuniverse: ["https://www.cu-cultureuniverse.co.il", "tickets"],
  kzattarbut: ["https://www.kzat-tarbut.com", "guide"],
  fournature: ["https://www.4nature.org.il", "culture"],
  eshkolot: ["https://www.eshkolot-il.com", "culture"],
  kolsadna: ["https://kolsadna.co.il", "guide"],
  salsavida: ["https://www.salsavida.com/guides/israel/", "guide"], arthurmurray: ["https://www.arthurmurray.co.il/the-salon", "venue"],
  yadvashem: ["https://online-reservations.yadvashem.org/he/home", "culture"],
  ganeyyehoshua: ["https://park.co.il/events/", "venue"],
  standupfactory: ["https://www.standupfactory.co.il", "venue"],
  tarbut: ["https://www.tarbut.events/events-index/", "guide"],
  ticketsi: ["https://www.ticketsi.co.il", "tickets"],
  tickster: ["https://www.tickster.co.il", "tickets"],
  cluberim: ["https://cluberim.co.il", "guide"],
  hechtmuseum: ["https://mushecht.haifa.ac.il/%d7%90%d7%99%d7%a8%d7%95%d7%a2%d7%99%d7%9d/", "venue"],
  agnonhouse: ["https://agnonhouse.org.il/events/", "venue"],
  kfarsabamuseum: ["https://kfar-saba-museum.org/yoman-eruim/", "venue"],
  shimur: ["https://shimur.org/events/", "venue"],
  mjc: ["https://mjc-jerusalem.com/events", "venue"],
  motj: ["https://motj.org.il/events/", "venue"],
  buki: ["https://buki.co.il/%d7%a1%d7%99%d7%95%d7%a8%d7%99-%d7%94%d7%9c%d7%99%d7%9c%d7%94-%d7%a9%d7%9c-%d7%91%d7%95%d7%a7%d7%99-%d7%a0%d7%90%d7%94/", "guide"],
  ybz: ["https://ybz.org.il/%d7%aa%d7%a8%d7%91%d7%95%d7%aa/%d7%9c%d7%95%d7%97-%d7%90%d7%99%d7%a8%d7%95%d7%a2%d7%99%d7%9d/", "venue"],
  rabincenter: ["https://www.rabincenter.org.il/%d7%90%d7%99%d7%a8%d7%95%d7%a2%d7%99%d7%9d-%d7%95%d7%aa%d7%a2%d7%a8%d7%95%d7%9b%d7%95%d7%aa/", "venue"],
  comy: ["https://comy.co.il", "tickets"], concertevents: ["https://concert-events.com", "guide"], hangar11: ["https://hangar11.co.il", "venue"], eventbrite: ["https://www.eventbrite.com", "tickets"],
  posh: ["https://posh.vip", "abroad"], showshappening: ["https://www.showshappening.com", "abroad"], ticketingo: ["https://ticketingo.co.il/concerts", "abroad"],
  eventimlight: ["https://www.eventim-light.com", "abroad"],
  barby: ["https://barby.co.il", "venue"], grayclub: ["https://grayclub.co.il", "venue"], epgb: ["https://epgb.co.il", "venue"],
  teder: ["https://www.teder.fm", "venue"], gagarin: ["https://gagarin.co.il", "venue"], zappa: ["https://www.zappa-club.co.il", "venue"],
  kikarhamusica: ["https://kikar-hamusica.com/shows/he", "venue"],
  yellowsubmarine: ["https://yellowsubmarine.org.il", "venue"], halutz: ["https://www.halutz.co.il", "venue"],
  haezor: ["https://haezor.com", "venue"], levontin7: ["https://levontin7.com", "venue"], reading3: ["https://www.reading3.co.il", "venue"], babebar: ["https://babebar.co.il", "venue"],
  bargiyora: ["https://bar-giyora.co.il", "venue"],
  basalon: ["https://basalon.co.il", "tickets"],
  radical: ["https://radical.org.il/calendar/", "culture"],
  makore: ["https://www.makore.co.il", "culture"], jaffatheatre: ["https://www.jaffatheatre.org.il", "culture"], jerusalemtheatre: ["https://www.jerusalem-theatre.co.il", "culture"],
  tzavta: ["https://www.tzavta.co.il/shows", "venue"],
  beershevatheatre: ["https://b7t.co.il", "culture"], nikonitai: ["https://nikonitai.smarticket.co.il", "venue"], hatarbut: ["https://www.hatarbut.co.il", "culture"], zoatlv: ["https://zoatlv.co.il", "culture"],
  habama: ["https://www.habama.co.il", "culture"], friendshist: ["https://friends-hist.co.il", "culture"],
  hamenagen: ["https://hamenagen.net", "culture"], israelmusicfest: ["https://israelmusicfest.co.il", "culture"], feelbeit: ["https://feelbeit.com", "venue"], ozen: ["https://ozentelaviv.com", "venue"], tomix: ["https://www.to-mix.co.il", "tickets"], tlvjazzfest: ["https://tlvjazzfest26.com", "culture"], poalimatarim: ["https://poalimatarim.com", "culture"],
  geektime: ["https://www.geektime.co.il/event/", "culture"], peoplecomputers: ["https://events.co.il", "culture"], parks: ["https://www.parks.org.il/activities/", "culture"],
  planet: ["https://www.planetcinema.co.il", "cinema"], ravhen: ["https://www.rav-hen.co.il", "cinema"],
  cinemacity: ["https://www.cinema-city.co.il", "cinema"], hotcinema: ["https://www.hotcinema.co.il", "cinema"],
  lev: ["https://www.lev.co.il", "cinema"], cinematheque_tlv: ["https://www.cinema.co.il", "cinema"],
  cinematheque_jlm: ["https://www.jer-cin.org.il", "cinema"], movieland: ["https://movieland.co.il", "cinema"],
  hulyo: ["https://www.hulyo.co.il", "abroad"], kishreyteufa: ["https://www.kishrey-teufa.co.il", "abroad"],
  ticketplace: ["https://ticketplace.co.il", "abroad"], livevents: ["https://livevents.co.il", "abroad"],
  issta: ["https://www.issta.co.il", "abroad"], megaevents: ["https://www.mega-events.co.il", "abroad"],
  viagogo: ["https://www.viagogo.com", "abroad"], swaptix: ["https://swaptix.co.il", "tickets"], fomo: ["https://www.fomo-events.co/he", "tickets"], bonire: ["https://bonire.live/he", "guide"], stayhappening: ["https://stayhappening.com/tel+aviv", "guide"], happeningnext: ["https://happeningnext.com/tel+aviv", "guide"], xplorer: ["https://www.xplorer.co.il", "abroad"],
  livetickets: ["https://www.livetickets.co.il", "abroad"], tickethouse: ["https://www.ticket-house.com", "abroad"],
  secrettelaviv: ["https://www.secrettelaviv.com", "guide"], secrettelavivapp: ["https://secrettelaviv.app", "guide"], klika: ["https://klika.live", "tickets"], gototlv: ["https://gototlv.co.il", "guide"], inperson: ["https://inperson.social/events", "guide"], nbn: ["https://www.nbn.org.il/all-events/", "guide"],
  beatradar: ["https://beatradar.co.il", "guide"],
  happytlv: ["https://www.happytlv.com", "guide"], tiuli: ["https://www.tiuli.com", "guide"], meety: ["https://meety.co.il", "guide"],
  funzing: ["https://il.funzing.com", "tickets"], easytix: ["https://easytix.co.il", "tickets"], ticketeams: ["https://ticketeams.co.il", "abroad"],
  festivalim: ["https://www.festivalim.co.il", "guide"], ogo: ["https://www.ogo.co.il", "guide"], ogo_us: ["https://us.ogo.events", "abroad"], tickettailor: ["https://www.tickettailor.com", "tickets"], ogo_de: ["https://de.ogo.events", "abroad"], muzi: ["https://muzi.co.il", "guide"], mugics: ["https://mugics.com", "guide"], efokulam: ["https://efokulam.co.il", "guide"], liveisrael: ["https://liveisrael.co", "guide"],
  plusparty: ["https://plusparty.co.il", "guide"], standupist: ["https://standupist.co.il", "tickets"],
  dungeon: ["https://dungeon.co.il", "venue"], agol: ["https://agol-join.vercel.app", "venue"], airdrop: ["https://airdrop.co.il", "guide"], hypd: ["https://hypd.co.il", "guide"], getin: ["https://get-in.com", "abroad"], takeabreak: ["https://takebreaktravel.com", "abroad"], dice: ["https://dice.fm", "abroad"], xceed: ["https://xceed.me", "abroad"], skiddle: ["https://www.skiddle.com", "abroad"], yotzim: ["https://yotzim.com", "guide"],
  telaviv: ["https://www.tel-aviv.gov.il", "city"], haifamuni: ["https://www.haifa.muni.il", "city"],
  ramatganmuni: ["https://www.ramat-gan.muni.il", "city"], petahtikvamuni: ["https://www.petah-tikva.muni.il", "city"], haderamuni: ["https://www.hadera.muni.il", "city"], ironit: ["https://www.ironit.org.il", "city"],jerusalemmuni: ["https://www.jerusalem.muni.il", "city"], sharepointmuni: ["https://www.rishonlezion.muni.il", "city"], holon: ["https://www.holon.muni.il", "city"],
  munievents: [null, "city"], matnasnet: [null, "city"], eventpro: [null, "city"], onecity: [null, "city"], milgammuni: ["https://bet-shean.org.il", "city"],
};

const TEXT = {
  he: {
    title: "חנויות הכרטיסים",
    intro: "SPOTX לא מוכר כרטיסים. כל אירוע באתר מגיע מאחד האתרים האלה, ולחיצה על \"כרטיסים\" מעבירה ישר אליהם. תודה לכולם.",
    events: "אירועים קרובים",
    upcoming: "אירועים קרובים",
    sources: "אתרים",
    offices: "קופות",
    search: "חיפוש אתר, קופה או עיר",
    all: "הכל",
    sitesTitle: "אתרי הכרטיסים והמקורות",
    site: "לאתר",
    browse: "לאירועים שלהם",
    updated: "עודכן",
    officesTitle: "קופות של ערים, מועצות ואולמות",
    officesIntro: "קופות הכרטיסים של היכלות תרבות, מתנ\"סים, מועצות ותיאטראות שאנחנו קוראים דרך Smarticket ומערכות דומות.",
    allCities: "כל הערים",
    more: (n: number) => `עוד ${n} קופות`,
    allOf: (n: number) => `הצג את כל ה-${n}`,
    less: "הצג פחות",
    none: "לא מצאנו אתר או קופה בשם הזה.",
  },
  en: {
    title: "Ticket stores",
    intro: "SPOTX doesn't sell tickets. Every event here comes from one of these sites, and \"Tickets\" takes you straight to them. Thank you all.",
    events: "upcoming events",
    upcoming: "upcoming events",
    sources: "sites",
    offices: "box offices",
    search: "Search a site, box office or city",
    all: "All",
    sitesTitle: "Ticket sites & sources",
    site: "Website",
    browse: "Their events",
    updated: "Updated",
    officesTitle: "City, council and venue box offices",
    officesIntro: "The box offices of culture halls, community centres, councils and theatres we read through Smarticket and similar systems.",
    allCities: "All cities",
    more: (n: number) => `${n} more box offices`,
    allOf: (n: number) => `Show all ${n}`,
    less: "Show less",
    none: "No site or box office by that name.",
  },
};

function ago(iso: string, locale: string): string {
  return agoRaw(iso, locale).replace(/\s*\(\d+\)/, ""); // Hebrew "לפני שעה (1)": the count is already in the word
}

function agoRaw(iso: string, locale: string): string {
  const minutes = Math.round((Date.parse(iso) - Date.now()) / 60_000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (Math.abs(minutes) < 60) return rtf.format(minutes, "minute");
  if (Math.abs(minutes) < 48 * 60) return rtf.format(Math.round(minutes / 60), "hour");
  return rtf.format(Math.round(minutes / 1440), "day");
}

// The same store always gets the same neon hue for its monogram.
const hue = (name: string) => [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const domain = (url: string) => new URL(url).hostname.replace(/^(www|live|web)\./, "");
const fold = (s: string) => s.toLocaleLowerCase().normalize("NFKD").replace(/[֑-ׇ̀-ͯ]/g, "");

interface Row { site: TicketSite; url: string | null; kind: Kind }

// A filter chip. Not ui's Chip: that one scrolls its chosen chip into view on mount, which here (an "All" chip
// chosen in each section) dragged the page down to the box offices on load.
function Pick({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return <button className={on ? "chip on" : "chip"} aria-pressed={on} onClick={onClick}>{children}</button>;
}

export default function StoresPage() {
  const { t: strings, lang } = useT();
  const t = TEXT[lang];
  useTitle(t.title);
  const [loaded, onProgress] = useLoadShare();
  const sites = useTicketSites(onProgress);
  const cats = useCategories();
  // Stores with events in Israel are browsed on the home page (its ?source= filter), the rest on /abroad.
  const browsable = new Set(cats.data?.sources.map((s) => s.value));
  // /ticket-sites down (a server without it, a restart): the home filter's counts, Israel only.
  const fallback: TicketSite[] | undefined = sites.isError ? cats.data?.sources.map((s) => ({ name: s.value, events: s.events, updated_at: null })) : undefined;
  const stores = sites.data?.stores ?? fallback;
  const offices = useMemo(() => sites.data?.box_offices ?? [], [sites.data]);
  // Once per group, a place once: one sold on three sites is one event (the stores' counts would add it three
  // times). Israel and abroad together; the home page's total is Israel only.
  const total = sites.data?.total ?? stores?.reduce((n, s) => n + s.events, 0) ?? 0;
  const failed = sites.isError && cats.isError;
  const setFilters = useStore((s) => s.setFilters);
  const setQuizDone = useStore((s) => s.setQuizDone);
  const navigate = useNavigate();
  const browse = (name: string) => {
    setFilters({ ...DEFAULT_FILTERS, shops: [name] });
    setQuizDone(true);
    navigate("/");
  };

  const [typed, setTyped] = useState("");
  const query = fold(useDeferredValue(typed).trim());
  const [kind, setKind] = useState<Kind | null>(null);
  const [city, setCity] = useState<string | null>(null);
  const num = (n: number) => n.toLocaleString(strings.locale);

  // Every store is shown, all of them, none behind "show more" (the owner's rule, 25.11.2026): by kind,
  // biggest first (the API's order); a store with nothing upcoming right now is listed too, with 0.
  const rows = useMemo<Row[]>(
    () => (stores ?? []).map((site) => {
      const [url, k] = STORES[site.name] ?? [null, "tickets"];
      return { site, url, kind: k };
    }),
    [stores],
  );
  const matches = (...texts: (string | null | undefined)[]) => !query || texts.some((x) => x && fold(x).includes(query));
  const found = rows.filter((r) => matches(sourceName(r.site.name), r.site.name, r.url && domain(r.url)));
  const kindCount = (k: Kind) => found.filter((r) => r.kind === k).length;
  const groups = KIND_ORDER.filter((k) => (!kind || k === kind) && kindCount(k) > 0)
    .map((k) => ({ kind: k, rows: found.filter((r) => r.kind === k) }));

  const cities = useMemo(() => {
    const by = new Map<string, number>();
    for (const o of offices) if (o.city) by.set(o.city, (by.get(o.city) ?? 0) + o.events);
    return [...by].sort((a, b) => b[1] - a[1]).slice(0, CITY_CHIPS).map(([c]) => c);
  }, [offices]);
  const officesFound = offices.filter((o) => (!city || o.city === city) && matches(o.name, o.city, domain(o.url)));
  const officesShown = officesFound; // every box office, none behind "show more"

  return (
    <>
      <Hero title={t.title}>
        <p className="landing-intro">{t.intro}</p>
        {stores && (
          <ul className="stores-stats">
            <li><b>{num(total)}</b> {t.upcoming}</li>
            <li><a href="#sites"><b>{num(rows.length)}</b> {t.sources}</a></li>
            {offices.length > 0 && <li><a href="#offices"><b>{num(offices.length)}</b> {t.offices}</a></li>}
          </ul>
        )}
        {stores && (
          <div className="facet-search">
            <svg viewBox="0 0 24 24" aria-hidden><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
            <input className="search" type="search" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={t.search} aria-label={t.search} enterKeyHint="search" />
          </div>
        )}
      </Hero>
      <main className="wrap stores-page">
        {!stores && (failed ? <p className="msg">{strings.error}</p> : <Loader progress={loaded || undefined} />)}
        {stores && (
          <section id="sites" className="stores-section">
            <h2 className="stores-h">{t.sitesTitle}</h2>
            <div className="filters stores-filter" role="group" aria-label={t.sitesTitle}>
              <div className="chips scroll">
                <Pick on={!kind} onClick={() => setKind(null)}>{t.all} <small>{found.length}</small></Pick>
                {KIND_ORDER.filter((k) => kindCount(k) > 0).map((k) => (
                  <Pick key={k} on={kind === k} onClick={() => setKind(kind === k ? null : k)}>{KINDS[k][lang]} <small>{kindCount(k)}</small></Pick>
                ))}
              </div>
            </div>
            {groups.map((g) => {
              const max = g.rows[0]?.site.events || 1;
              const shown = g.rows; // every store of the kind
              return (
                <div key={g.kind} className="store-group">
                  <h3 className="section-title">{KINDS[g.kind][lang]} <small>{g.rows.length}</small></h3>
                  <ol className="stores">
                    {shown.map(({ site: s, url }, i) => (
                      <li key={s.name} className="store" style={{ "--hue": hue(s.name), animationDelay: `${Math.min(i, 12) * 25}ms` } as CSSProperties}>
                        <span className="store-mono" aria-hidden>{sourceName(s.name).replace(/^the /i, "").charAt(0).toUpperCase()}</span>
                        <div className="store-head">
                          <b dir="auto">{sourceName(s.name)}</b>
                          {url && <small dir="ltr">{domain(url)}</small>}
                        </div>
                        <div className="store-count">
                          <b>{num(s.events)}</b>
                          <small>{t.events}</small>
                        </div>
                        <span className="store-bar"><span style={{ width: `${Math.max(3, (s.events / max) * 100)}%` }} /></span>
                        <div className="store-foot">
                          <small>{s.updated_at ? `${t.updated} ${ago(s.updated_at, strings.locale)}` : ""}</small>
                          {browsable.has(s.name) ? <button className="linkish" onClick={() => browse(s.name)}>{t.browse}</button>
                            : <Link className="linkish" to={`/abroad?source=${encodeURIComponent(s.name)}`}>{t.browse}</Link>}
                          {url && <a href={outbound(url, "stores", "site")} target="_blank" rel="noopener noreferrer">{t.site} ↗</a>}
                        </div>
                      </li>
                    ))}
                  </ol>
                </div>
              );
            })}
            {query && found.length === 0 && officesFound.length === 0 && <p className="msg">{t.none}</p>}
          </section>
        )}
        {offices.length > 0 && (
          <section id="offices" className="stores-section">
            <h2 className="stores-h">{t.officesTitle}</h2>
            <p className="landing-intro">{t.officesIntro}</p>
            <div className="filters stores-filter" role="group" aria-label={t.officesTitle}>
              <div className="chips scroll">
                <Pick on={!city} onClick={() => setCity(null)}>📍 {t.allCities}</Pick>
                {cities.map((c) => <Pick key={c} on={city === c} onClick={() => setCity(city === c ? null : c)}>{c}</Pick>)}
              </div>
            </div>
            <ul className="offices">
              {officesShown.map((o: BoxOffice) => (
                <li key={o.url}>
                  <a className="office" href={outbound(o.url, "stores", "box-office")} target="_blank" rel="noopener noreferrer">
                    <span className="office-name">
                      <b dir="auto">{o.name}</b>
                      <small>{o.city && <>{o.city} · </>}<span dir="ltr">{domain(o.url)}</span> ↗</small>
                    </span>
                    <span className="office-count"><b>{num(o.events)}</b><small>{t.events}</small></span>
                  </a>
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>
    </>
  );
}
