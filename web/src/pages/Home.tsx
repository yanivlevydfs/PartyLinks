import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useCategories, useEvents } from "../api";
import { Filters, filterParams, Relax } from "../Filters";
import { CITIES, genreLabel } from "../i18n";
import { DEFAULT_FILTERS, useStore } from "../store";
import { Card, EventGrid, Hero, useT, useTitle } from "../ui";

// Landing routes (/tonight, /weekend, /genre/:genre[/tonight|weekend], /city/:city) are the home page with its filters preset;
// the server renders the same URLs with their own title, description and FAQ for crawlers.
export default function Home({ landing }: { landing?: "tonight" | "weekend" | "genre" | "city" }) {
  const { t, lang } = useT();
  const set = useStore((s) => s.setFilters);
  const f = useStore((s) => s.filters);
  const cats = useCategories().data;
  const { genre, when, city } = useParams();
  const cityName = landing === "city" && city ? CITIES[city] : undefined;

  // A landing page starts from a clean filter (keeping the search text) with its own preset;
  // the plain home page drops that preset again (but keeps filters the visitor chose there).
  useEffect(() => {
    const { preset, setPreset, filters } = useStore.getState();
    setPreset(!!landing);
    if (!landing) {
      if (preset) set({ ...DEFAULT_FILTERS, q: filters.q });
      return;
    }
    set({
      ...DEFAULT_FILTERS,
      q: useStore.getState().filters.q,
      when: landing === "tonight" || landing === "weekend" ? landing : landing === "genre" && (when === "tonight" || when === "weekend") ? when : "all",
      genres: landing === "genre" && genre ? [genre] : [],
    });
  }, [landing, genre, when, city, set]);
  // /city/:city: the city's region comes with the categories.
  const cityRegion = cityName && cats?.cities.find((c) => c.value === cityName.he)?.area;
  useEffect(() => {
    if (cityName) set({ region: cityRegion ?? null, areas: [], city: cityRegion === "tel_aviv" ? null : cityName.he });
  }, [cityName, cityRegion, set]);
  const g = genreLabel(lang, genre ?? "");
  const title =
    landing === "genre" ? (when === "tonight" ? t.landing.genreTonight(g) : when === "weekend" ? t.landing.genreWeekend(g) : t.landing.genre(g))
    : landing === "city" ? t.landing.city(cityName?.[lang] ?? city ?? "")
    : landing ? t.landing[landing] : t.tagline;
  useTitle(landing ? title : undefined);

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

  return (
    <>
      <Hero title={title}>
        <input className="search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t.search} aria-label={t.search} />
      </Hero>
      <main className="wrap">
        {!landing && !f.q && <Trending />}
        <Filters />
        <EventGrid params={filterParams(f)} empty={<Relax />} />
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
