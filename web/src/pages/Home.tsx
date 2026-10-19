import { useEffect, useState } from "react";
import { useCategories, type Params } from "../api";
import { genreLabel } from "../i18n";
import { useStore } from "../store";
import { Chip, EventGrid, Hero, today, useT } from "../ui";

export default function Home() {
  const { t, lang } = useT();
  const f = useStore((s) => s.filters);
  const set = useStore((s) => s.setFilter);
  const cats = useCategories().data;

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
    free_entry: f.freeOnly ? "true" : undefined,
    sold_out: f.hideSoldOut ? "false" : undefined,
    ...(f.when === "tonight" ? { date: today() } : f.when === "weekend" ? { weekend: "true" } : f.when !== "all" ? { date: f.when } : {}),
  };

  const toggleGenre = (g: string) => set("genres", f.genres.includes(g) ? f.genres.filter((x) => x !== g) : [...f.genres, g]);
  const dates = (cats?.dates ?? []).filter((d) => d.value >= today()).slice(0, 10);

  return (
    <>
      <Hero title={t.tagline}>
        <input className="search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t.search} aria-label={t.search} />
      </Hero>
      <main className="wrap">
        <div className="filters">
          <div className="chips" role="group" aria-label={t.when}>
            <Chip on={f.when === "all"} onClick={() => set("when", "all")}>{t.all}</Chip>
            <Chip on={f.when === "tonight"} onClick={() => set("when", "tonight")}>{t.tonight}</Chip>
            <Chip on={f.when === "weekend"} onClick={() => set("when", "weekend")}>{t.weekend}</Chip>
            {dates.map((d) => (
              <Chip key={d.value} on={f.when === d.value} onClick={() => set("when", d.value)}>
                {new Date(d.value + "T12:00:00").toLocaleDateString(t.locale, { weekday: "short", day: "numeric", month: "numeric" })}
              </Chip>
            ))}
          </div>

          {!!cats?.genres.length && (
            <div className="chips" role="group" aria-label={t.genres}>
              {cats.genres.map((g) => (
                <Chip key={g.value} on={f.genres.includes(g.value)} onClick={() => toggleGenre(g.value)}>
                  {genreLabel(lang, g.value)} <small>{g.events}</small>
                </Chip>
              ))}
            </div>
          )}

          <div className="chips" role="group" aria-label={t.slot}>
            {Object.entries(t.slots).map(([k, name]) => (
              <Chip key={k} on={f.slot === k} onClick={() => set("slot", f.slot === k ? null : k)}>{name}</Chip>
            ))}
            <span className="sep" />
            <Chip on={f.freeOnly} onClick={() => set("freeOnly", !f.freeOnly)}>{t.freeOnly}</Chip>
            <Chip on={f.hideSoldOut} onClick={() => set("hideSoldOut", !f.hideSoldOut)}>{t.hideSoldOut}</Chip>
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
