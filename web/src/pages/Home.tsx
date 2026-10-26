import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { useCategories, useLandingInfo, useRails } from "../api";
import { Filters, filterParams, Relax } from "../Filters";
import { CITIES, genreLabel } from "../i18n";
import { DEFAULT_FILTERS, useStore, type Filters as FilterState } from "../store";
import { artistPath, Card, CardSkeletons, EventGrid, Hero, Related, useT, useTitle, venuePath } from "../ui";
import type { EventSummary } from "../types";
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
  const [suggest, setSuggest] = useState(false);
  useEffect(() => {
    const id = setTimeout(() => {
      const text = q.trim();
      // One letter matches nearly everything (and the API wants 2+): the list waits for the second.
      if (text.length === 1 || text === useStore.getState().filters.q) return;
      set(text ? { ...DEFAULT_FILTERS, q: text } : { q: text });
    }, 250);
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
        {/* Suggestions stay open while focus is anywhere in the box (Tab into them), close on leaving or Escape. */}
        <div className="search-box" onFocus={() => setSuggest(true)} onKeyDown={(e) => e.key === "Escape" && setSuggest(false)}
          onBlur={(e) => !e.currentTarget.contains(e.relatedTarget as Node | null) && setSuggest(false)}>
          <input className="search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t.search} aria-label={t.search} />
          {suggest && <Suggestions q={q} />}
        </div>
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

/** "Trending" (sold on the most ticket sites in two weeks), then a row per audience for the month ahead:
 *  all from one cached request (/events/rails), drawn from the visitor's last copy until it arrives. */
function Trending() {
  const { t } = useT();
  const q = useRails();
  const rails = Object.entries(q.data?.rails ?? {});
  if (q.isPending) return <Rail title={t.trending} sub={t.trendingSub} pool={undefined} />;
  return <>{rails.map(([key, pool]) => key === "trending"
    ? <Rail key={key} title={t.trending} sub={t.trendingSub} pool={pool} />
    : t.rails[key] && <Rail key={key} title={t.rails[key][0]} sub={t.rails[key][1]} pool={pool} />)}</>;
}

/** A sideways row of cards: a random 12 of the row's pool (undefined: still loading). */
function Rail({ title, sub, pool }: { title: string; sub: string; pool: EventSummary[] | undefined }) {
  const row = useRef<HTMLDivElement>(null);
  // Shuffled once per visit: a fresh copy replacing the stored one doesn't reshuffle the row under the visitor.
  // A stored copy may be days old, so events already over are dropped.
  const loaded = !!pool;
  const items = useMemo(() => {
    const now = Date.now();
    const list = (pool ?? []).filter((e) => !e.start_at || Date.parse(e.end_at ?? e.start_at) > now);
    for (let i = list.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [list[i], list[j]] = [list[j], list[i]];
    }
    return list.slice(0, 12);
  }, [loaded]); // eslint-disable-line react-hooks/exhaustive-deps
  if (loaded && items.length < 3) return null;
  const scroll = (dir: number) => row.current?.scrollBy({ left: dir * row.current.clientWidth * 0.8, behavior: "smooth" });
  return (
    <section className="trending">
      <div className="trending-head">
        <div>
          <h2>{title} <span aria-hidden>↗</span></h2>
          <p>{sub}</p>
        </div>
        <div className="trending-nav">
          <button onClick={() => scroll(-1)} aria-label="‹">‹</button>
          <button onClick={() => scroll(1)} aria-label="›">›</button>
        </div>
      </div>
      <div className="trending-row" ref={row}>
        {loaded ? items.map((e) => <Card key={e.id} e={e} />) : <CardSkeletons n={4} />}
      </div>
    </section>
  );
}

/** Artists, venues and cities matching the search text, under the box while typing. The full name index
 *  (/categories?anywhere=true, ~190KB, cached) loads on the first focus, not with the page.
 *  ponytail: filtered in the browser; a /search/suggest endpoint if the index outgrows a phone. */
function Suggestions({ q }: { q: string }) {
  const { t, lang } = useT();
  const cats = useCategories({ anywhere: "true" }).data;
  const text = q.trim().toLocaleLowerCase();
  const items = useMemo(() => {
    if (!cats || text.length < 2) return [];
    const pick = <T extends { value: string; events: number }>(list: T[], n: number) => list
      .filter((x) => x.value.toLocaleLowerCase().includes(text))
      .sort((a, b) => +b.value.toLocaleLowerCase().startsWith(text) - +a.value.toLocaleLowerCase().startsWith(text) || b.events - a.events)
      .slice(0, n);
    const cities = Object.entries(CITIES).filter(([, c]) => c.he.includes(q.trim()) || c.en.toLowerCase().includes(text)).slice(0, 2)
      .map(([slug, c]) => ({ key: "c" + slug, icon: "📍", label: c[lang], to: `/city/${slug}`, n: cats.cities.find((x) => x.value === c.he)?.events }));
    const genres = cats.genres.filter((g) => genreLabel(lang, g.value).toLocaleLowerCase().includes(text) || g.value.replace("_", " ").includes(text))
      .slice(0, 2).map((g) => ({ key: "g" + g.value, icon: "🎵", label: genreLabel(lang, g.value), to: `/genre/${g.value}`, n: g.events }));
    return [
      ...genres,
      ...cities,
      ...pick(cats.lineup, 4).map((a) => ({ key: "a" + a.value, icon: "🎧", label: a.value, to: artistPath(a.value), n: a.events })),
      ...pick(cats.venues, 3).map((v) => ({ key: "v" + v.value, icon: "🏛", label: v.value, to: venuePath(v.value), n: v.events })),
    ];
  }, [cats, text, q, lang]);
  if (!items.length) return null;
  return (
    <ul className="suggest" aria-label={t.search}>
      {items.map((i) => (
        <li key={i.key}><Link to={i.to}>
          <span aria-hidden>{i.icon}</span><b dir="auto">{i.label}</b>{i.n != null && <small>{t.results(i.n)}</small>}
        </Link></li>
      ))}
    </ul>
  );
}
