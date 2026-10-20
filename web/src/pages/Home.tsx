import { useEffect, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { useCategories, type Params } from "../api";
import { CITIES, genreLabel } from "../i18n";
import { useStore } from "../store";
import { Chip, EventGrid, Hero, label, today, TZ, useT, useTitle } from "../ui";

// Landing routes (/tonight, /weekend, /genre/:genre[/tonight|weekend], /city/:city) are the home page with its filters preset;
// the server renders the same URLs with their own title, description and FAQ for crawlers.
export default function Home({ landing }: { landing?: "tonight" | "weekend" | "genre" | "city" }) {
  const { t, lang } = useT();
  const f = useStore((s) => s.filters);
  const set = useStore((s) => s.setFilter);
  const cats = useCategories().data;
  const { genre, when, city } = useParams();
  const cityName = landing === "city" && city ? CITIES[city] : undefined;

  useEffect(() => {
    if (!landing) return;
    set("when", landing === "tonight" || landing === "weekend" ? landing : landing === "genre" && (when === "tonight" || when === "weekend") ? when : "all");
    set("genres", landing === "genre" && genre ? [genre] : []);
  }, [landing, genre, when, set]);
  const g = genreLabel(lang, genre ?? "");
  const title =
    landing === "genre" ? (when === "tonight" ? t.landing.genreTonight(g) : when === "weekend" ? t.landing.genreWeekend(g) : t.landing.genre(g))
    : landing === "city" ? t.landing.city(cityName?.[lang] ?? city ?? "")
    : landing ? t.landing[landing] : t.tagline;
  useTitle(landing ? title : undefined);

  // Debounce the search box; the store keeps what was typed.
  const [q, setQ] = useState(f.q);
  useEffect(() => {
    const id = setTimeout(() => set("q", q.trim()), 300);
    return () => clearTimeout(id);
  }, [q, set]);

  const params: Params = {
    sort: f.sort,
    q: f.q.length >= 2 ? f.q : undefined,
    genre: f.genres,
    time_slot: f.slot ?? undefined,
    in_target_city: f.region === "tel_aviv" ? "true" : undefined,
    area: f.region === "tel_aviv" ? f.areas : f.region ? [f.region] : [],
    city: cityName?.he ?? f.city ?? undefined,
    free_entry: f.freeOnly ? "true" : undefined,
    sold_out: f.hideSoldOut ? "false" : undefined,
    ...(f.when === "tonight" ? { date: today() } : f.when === "weekend" ? { weekend: "true" } : f.when !== "all" ? { date: f.when } : {}),
  };

  const toggleGenre = (g: string) => set("genres", f.genres.includes(g) ? f.genres.filter((x) => x !== g) : [...f.genres, g]);
  const toggleArea = (a: string) => set("areas", f.areas.includes(a) ? f.areas.filter((x) => x !== a) : [...f.areas, a]);
  const pickRegion = (r: string | null) => {
    set("region", r === f.region ? null : r);
    set("areas", []);
    set("city", null);
  };
  const regionKeys = new Set(cats?.regions?.map((r) => r.value));
  // Under Tel Aviv: its neighbourhoods. Under another region: its cities.
  const subAreas = f.region === "tel_aviv"
    ? (cats?.areas ?? []).filter((a) => !regionKeys.has(a.value) && a.value !== "outside" && a.value !== "abroad")
    : (cats?.cities ?? []).filter((c) => f.region && c.area === f.region);
  const dates = (cats?.dates ?? []).filter((d) => d.value >= today()).slice(0, 10);

  return (
    <>
      <Hero title={title}>
        <input className="search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t.search} aria-label={t.search} />
      </Hero>
      <main className="wrap">
        <div className="filters">
          <FilterRow label={t.when}>
            <Chip on={f.when === "all"} onClick={() => set("when", "all")}>{t.all}</Chip>
            <Chip on={f.when === "tonight"} onClick={() => set("when", "tonight")}>{t.tonight}</Chip>
            <Chip on={f.when === "weekend"} onClick={() => set("when", "weekend")}>{t.weekend}</Chip>
            {dates.map((d) => (
              <Chip key={d.value} on={f.when === d.value} onClick={() => set("when", d.value)}>
                {new Date(d.value + "T12:00:00Z").toLocaleDateString(t.locale, { timeZone: TZ, weekday: "short", day: "numeric", month: "numeric" })}
              </Chip>
            ))}
          </FilterRow>

          {!!cats?.regions?.length && !cityName && (
            <FilterRow label={t.area}>
              <Chip on={!f.region} onClick={() => pickRegion(null)}>{t.allAreas}</Chip>
              {cats.regions.map((r) => (
                <Chip key={r.value} on={f.region === r.value} onClick={() => pickRegion(r.value)}>
                  {label(t.areas, r.value)} <small>{r.events}</small>
                </Chip>
              ))}
            </FilterRow>
          )}

          {f.region && subAreas.length > 1 && !cityName && (
            <FilterRow label={f.region === "tel_aviv" ? t.neighborhood : t.city}>
              <Chip on={!f.areas.length && !f.city} onClick={() => { set("areas", []); set("city", null); }}>{t.all}</Chip>
              {subAreas.map((a) => f.region === "tel_aviv" ? (
                <Chip key={a.value} on={f.areas.includes(a.value)} onClick={() => toggleArea(a.value)}>
                  {label(t.areas, a.value)} <small>{a.events}</small>
                </Chip>
              ) : (
                <Chip key={a.value} on={f.city === a.value} onClick={() => set("city", f.city === a.value ? null : a.value)}>
                  {a.value} <small>{a.events}</small>
                </Chip>
              ))}
            </FilterRow>
          )}

          {!!cats?.genres.length && (
            <FilterRow label={t.genres}>
              {cats.genres.map((g) => (
                <Chip key={g.value} on={f.genres.includes(g.value)} onClick={() => toggleGenre(g.value)}>
                  {genreLabel(lang, g.value)} <small>{g.events}</small>
                </Chip>
              ))}
            </FilterRow>
          )}

          <FilterRow label={t.slot}>
            {Object.entries(t.slots).map(([k, name]) => (
              <Chip key={k} on={f.slot === k} onClick={() => set("slot", f.slot === k ? null : k)}>{name}</Chip>
            ))}
          </FilterRow>

          <div className="filter-bar">
            <Chip className="toggle free" on={f.freeOnly} onClick={() => set("freeOnly", !f.freeOnly)}>
              <span className="tick" aria-hidden /> {t.freeOnly}
            </Chip>
            <Chip className="toggle" on={f.hideSoldOut} onClick={() => set("hideSoldOut", !f.hideSoldOut)}>
              <span className="tick" aria-hidden /> {t.hideSoldOut}
            </Chip>
            <label className="sort">
              {t.sort}
              <select value={f.sort} onChange={(e) => set("sort", e.target.value)}>
                <option value="start_at">{t.sortDate}</option>
                <option value="min_price">{t.sortPrice}</option>
                <option value="-last_seen_at">{t.sortNew}</option>
              </select>
            </label>
          </div>
        </div>
        <EventGrid params={params} />
      </main>
    </>
  );
}

function FilterRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="frow" role="group" aria-label={label}>
      <span className="frow-label">{label}</span>
      <div className="chips">{children}</div>
    </div>
  );
}
