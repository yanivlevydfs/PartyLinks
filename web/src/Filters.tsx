import type { ReactNode } from "react";
import { useCategories, type Params } from "./api";
import { genreLabel } from "./i18n";
import { useStore, type Filters as FilterState } from "./store";
import { Chip, label, today, TZ, useT } from "./ui";

// The one home-page filter: its state lives in the store, the API query comes from filterParams(),
// and every landing page (/tonight, /genre/x, /city/x) only presets that same state.

/** Query for /events from the filter state. */
export function filterParams(f: FilterState): Params {
  return {
    sort: f.sort,
    q: f.q.length >= 2 ? f.q : undefined,
    genre: f.genres,
    time_slot: f.slot ?? undefined,
    // Tel Aviv: the target city, optionally narrowed to neighbourhoods. Other regions: their area key + a city.
    in_target_city: f.region === "tel_aviv" ? "true" : undefined,
    area: f.region === "tel_aviv" ? f.areas : f.region ? [f.region] : [],
    city: f.region !== "tel_aviv" ? f.city ?? undefined : undefined,
    free_entry: f.freeOnly ? "true" : undefined,
    sold_out: f.hideSoldOut ? "false" : undefined,
    ...(f.when === "tonight" ? { date: today() } : f.when === "weekend" ? { weekend: "true" } : f.when !== "all" ? { date: f.when } : {}),
  };
}

const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

export function Filters() {
  const { t, lang } = useT();
  const f = useStore((s) => s.filters);
  const set = useStore((s) => s.setFilters);
  const cats = useCategories().data;

  const regionKeys = new Set(cats?.regions?.map((r) => r.value));
  // Under Tel Aviv: its neighbourhoods. Under another region: its cities.
  const subAreas = f.region === "tel_aviv"
    ? (cats?.areas ?? []).filter((a) => !regionKeys.has(a.value) && a.value !== "outside" && a.value !== "abroad")
    : (cats?.cities ?? []).filter((c) => f.region && c.area === f.region);
  const dates = (cats?.dates ?? []).filter((d) => d.value >= today()).slice(0, 10);

  return (
    <div className="filters">
      <FilterRow label={t.when}>
        <Chip on={f.when === "all"} onClick={() => set({ when: "all" })}>{t.all}</Chip>
        <Chip on={f.when === "tonight"} onClick={() => set({ when: "tonight" })}>{t.tonight}</Chip>
        <Chip on={f.when === "weekend"} onClick={() => set({ when: "weekend" })}>{t.weekend}</Chip>
        {dates.map((d) => (
          <Chip key={d.value} on={f.when === d.value} onClick={() => set({ when: d.value })}>
            {new Date(d.value + "T12:00:00Z").toLocaleDateString(t.locale, { timeZone: TZ, weekday: "short", day: "numeric", month: "numeric" })}
          </Chip>
        ))}
      </FilterRow>

      {!!cats?.regions?.length && (
        <FilterRow label={t.area}>
          <Chip on={!f.region} onClick={() => set({ region: null, areas: [], city: null })}>{t.allAreas}</Chip>
          {cats.regions.map((r) => (
            <Chip key={r.value} on={f.region === r.value}
              onClick={() => set({ region: f.region === r.value ? null : r.value, areas: [], city: null })}>
              {label(t.areas, r.value)} <small>{r.events}</small>
            </Chip>
          ))}
        </FilterRow>
      )}

      {f.region && subAreas.length > 1 && (
        <FilterRow label={f.region === "tel_aviv" ? t.neighborhood : t.city}>
          <Chip on={!f.areas.length && !f.city} onClick={() => set({ areas: [], city: null })}>{t.all}</Chip>
          {subAreas.map((a) => f.region === "tel_aviv" ? (
            <Chip key={a.value} on={f.areas.includes(a.value)} onClick={() => set({ areas: toggle(f.areas, a.value) })}>
              {label(t.areas, a.value)} <small>{a.events}</small>
            </Chip>
          ) : (
            <Chip key={a.value} on={f.city === a.value} onClick={() => set({ city: f.city === a.value ? null : a.value })}>
              {a.value} <small>{a.events}</small>
            </Chip>
          ))}
        </FilterRow>
      )}

      {!!cats?.genres.length && (
        <FilterRow label={t.genres}>
          {cats.genres.map((g) => (
            <Chip key={g.value} on={f.genres.includes(g.value)} onClick={() => set({ genres: toggle(f.genres, g.value) })}>
              {genreLabel(lang, g.value)} <small>{g.events}</small>
            </Chip>
          ))}
        </FilterRow>
      )}

      <FilterRow label={t.slot}>
        {Object.entries(t.slots).map(([k, name]) => (
          <Chip key={k} on={f.slot === k} onClick={() => set({ slot: f.slot === k ? null : k })}>{name}</Chip>
        ))}
      </FilterRow>

      <div className="filter-bar">
        <Chip className="toggle free" on={f.freeOnly} onClick={() => set({ freeOnly: !f.freeOnly })}>
          <span className="tick" aria-hidden /> {t.freeOnly}
        </Chip>
        <Chip className="toggle" on={f.hideSoldOut} onClick={() => set({ hideSoldOut: !f.hideSoldOut })}>
          <span className="tick" aria-hidden /> {t.hideSoldOut}
        </Chip>
        <label className="sort">
          {t.sort}
          <select value={f.sort} onChange={(e) => set({ sort: e.target.value })}>
            <option value="start_at">{t.sortDate}</option>
            <option value="min_price">{t.sortPrice}</option>
            <option value="-last_seen_at">{t.sortNew}</option>
          </select>
        </label>
      </div>
    </div>
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
