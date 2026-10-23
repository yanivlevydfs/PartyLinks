import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useCategories, useEvents, useLandingInfo } from "../api";
import { Filters, filterParams, Relax } from "../Filters";
import { CITIES, genreLabel } from "../i18n";
import { DEFAULT_FILTERS, useStore, type Filters as FilterState } from "../store";
import { Card, EventGrid, Hero, Related, useT, useTitle } from "../ui";
import { Quiz } from "./Quiz";

// Regions with a /region/<key> page (seo.py REGION_NAMES); Tel Aviv and Eilat are city pages.
const REGION_PAGES = ["gush_dan", "sharon", "jerusalem", "haifa", "galilee", "negev"];

/** The landing URL that shows exactly these filters (/genre/techno, /city/tel-aviv/tonight, /region/sharon...), else "/". */
export function landingPath(f: FilterState): string {
  const rest = { ...f, q: "", sort: DEFAULT_FILTERS.sort, when: "all", genres: [], regions: [], areas: [], city: null };
  if (JSON.stringify(rest) !== JSON.stringify(DEFAULT_FILTERS)) return "/";
  if (f.regions.length === 1 && REGION_PAGES.includes(f.regions[0]) && !f.city && !f.areas.length && !f.genres.length && f.when === "all")
    return `/region/${f.regions[0].replace("_", "-")}`;
  const where = f.regions.length > 1 || f.areas.length ? undefined
    : f.regions[0] === "tel_aviv" ? "tel-aviv"
    : f.city ? Object.keys(CITIES).find((k) => CITIES[k].he === f.city) : !f.regions.length ? "" : undefined;
  const when = f.when === "all" ? "" : f.when === "tonight" || f.when === "weekend" ? f.when : undefined;
  if (where === undefined || when === undefined || f.genres.length > 1) return "/";
  if (where) return f.genres.length && when ? "/" : `/city/${where}` + (when ? `/${when}` : f.genres.length ? `/${f.genres[0]}` : "");
  if (f.genres.length) return `/genre/${f.genres[0]}` + (when ? `/${when}` : "");
  return when ? `/${when}` : "/";
}

// Landing routes (/tonight, /weekend, /genre/:genre[/tonight|weekend], /city/:city) are the home page with its filters preset;
// the server renders the same URLs with their own title, description and FAQ for crawlers.
export default function Home({ landing }: { landing?: "tonight" | "weekend" | "genre" | "city" | "region" }) {
  const { t, lang } = useT();
  const set = useStore((s) => s.setFilters);
  const f = useStore((s) => s.filters);
  const cats = useCategories().data;
  const { genre, when, city, sub, region } = useParams();
  const cityWhen = sub === "tonight" || sub === "weekend" ? sub : undefined;
  // Info from the server: title, intro, related links, and the names of a town that is not in CITIES.
  const info = useLandingInfo(useLocation().pathname, lang).data;
  const cityName = landing === "city" && city ? CITIES[city] ?? info?.city ?? undefined : undefined;

  // A landing page starts from a clean filter (keeping the search text) with its own preset;
  // the plain home page drops that preset again (but keeps filters the visitor chose there).
  useEffect(() => {
    const { preset, setPreset, filters } = useStore.getState();
    setPreset(!!landing);
    if (!landing) {
      if (preset) {
        set({ ...DEFAULT_FILTERS, q: filters.q });
        useStore.getState().setQuizDone(false); // back from a landing page: ask again
      }
      return;
    }
    set({
      ...DEFAULT_FILTERS,
      q: filters.q,
      sort: filters.sort,
      when: landing === "tonight" || landing === "weekend" ? landing : landing === "genre" && (when === "tonight" || when === "weekend") ? when : cityWhen ?? "all",
      genres: landing === "genre" && genre ? [genre] : sub && !cityWhen ? [sub] : [],
      regions: landing === "region" && region ? [region.replace("-", "_")] : [],
    });
  }, [landing, genre, when, city, sub, region, set]);
  // /city/:city: the city's region comes with the categories.
  const cityRegion = cityName && cats?.cities.find((c) => c.value === cityName.he)?.area;
  useEffect(() => {
    if (cityName) set({ regions: cityRegion ? [cityRegion] : [], areas: [], city: cityRegion === "tel_aviv" ? null : cityName.he });
  }, [cityName, cityRegion, set]);
  // Filters picked here move the address to their landing URL (techno: /genre/techno), so it can be shared.
  // Not on mount: a remount (back from an event) keeps the route it came with.
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const mounted = useRef(f);
  useEffect(() => {
    if (f === mounted.current) return;
    const path = landingPath(f);
    if (path === pathname) return;
    useStore.getState().setPreset(false); // the target page keeps these filters instead of resetting them
    navigate(path + search, { replace: true });
  }, [f]); // eslint-disable-line react-hooks/exhaustive-deps

  const g = genreLabel(lang, genre ?? "");
  const title =
    landing === "genre" ? (when === "tonight" ? t.landing.genreTonight(g) : when === "weekend" ? t.landing.genreWeekend(g) : t.landing.genre(g))
    : landing === "city" ? t.landing.city(cityName?.[lang] ?? city ?? "")
    : landing === "region" ? t.landing.city(t.areas[region?.replace("-", "_") ?? ""] ?? "")
    : landing ? t.landing[landing] : t.tagline;
  const heading = (landing && info?.title) || title;
  useTitle(landing ? heading : undefined);

  // Debounce the search box; the store keeps what was typed. A new search starts from a clean
  // filter, so leftover filters (date, area, genre...) never hide what was searched for.
  const [q, setQ] = useState(f.q);
  useEffect(() => {
    const id = setTimeout(() => {
      const text = q.trim();
      if (text === useStore.getState().filters.q) return;
      set(text ? { ...DEFAULT_FILTERS, q: text } : { q: text });
    }, 300);
    return () => clearTimeout(id);
  }, [q, set]);

  // The plain home page asks "where to?" first; a search, a landing page or an answered quiz shows the list.
  const quizDone = useStore((s) => s.quizDone);
  const quiz = !landing && !f.q && !quizDone;
  const again = () => {
    set({ ...DEFAULT_FILTERS, sort: f.sort });
    useStore.getState().setQuizDone(false);
  };

  return (
    <>
      <Hero title={quiz ? t.quiz.title : heading}>
        <input className="search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t.search} aria-label={t.search} />
      </Hero>
      <main className="wrap">
        {quiz ? (
          <>
            <Quiz />
            <Trending />
          </>
        ) : (
          <>
            {!landing && !f.q && <button className="quiz-again" onClick={again}>✨ {t.quiz.again}</button>}
            {landing && info?.intro && <p className="landing-intro">{info.intro}</p>}
            <Filters />
            <EventGrid params={filterParams(f)} empty={<Relax />} />
            {landing && <Related path={pathname} />}
          </>
        )}
      </main>
    </>
  );
}

const TRENDING_DAYS = 14;

/** A sideways row of the events sold on the most ticket sites in the next two weeks. */
function Trending() {
  const { t } = useT();
  const row = useRef<HTMLDivElement>(null);
  // Day-granular, so the query key stays stable within a visit.
  const until = new Date(Date.now() + TRENDING_DAYS * 24 * 3600 * 1000).toISOString().slice(0, 10);
  const items = (useEvents({ sort: "-group_size", date_to: until, page_size: "12" }).data?.pages[0]?.items ?? []).filter(
    (e) => (e.listings ?? 1) > 1,
  );
  if (items.length < 3) return null;
  const scroll = (dir: number) => row.current?.scrollBy({ left: dir * row.current.clientWidth * 0.8, behavior: "smooth" });
  return (
    <section className="trending">
      <div className="trending-head">
        <div>
          <h2>{t.trending} <span aria-hidden>↗</span></h2>
          <p>{t.trendingSub}</p>
        </div>
        <div className="trending-nav">
          <button onClick={() => scroll(-1)} aria-label="‹">‹</button>
          <button onClick={() => scroll(1)} aria-label="›">›</button>
        </div>
      </div>
      <div className="trending-row" ref={row}>
        {items.map((e) => <Card key={e.id} e={e} />)}
      </div>
    </section>
  );
}
