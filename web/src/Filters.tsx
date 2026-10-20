import { useEffect, useRef, useState, type ReactNode } from "react";
import { useCategories, useEvents, type Params } from "./api";
import { genreLabel } from "./i18n";
import { DEFAULT_FILTERS, useStore, type Filters as FilterState } from "./store";
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

const MOBILE = "(max-width: 760px)";
const GENRES_SHOWN = 12; // desktop: the busiest genres; the rest behind "+N more"

function useMedia(query: string) {
  const [on, setOn] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const m = matchMedia(query);
    const change = () => setOn(m.matches);
    m.addEventListener("change", change);
    return () => m.removeEventListener("change", change);
  }, [query]);
  return on;
}

// Desktop: one panel (dates on one line, top genres, a bar for time / toggles / sort).
// Phones: dates + a bar with "Filters (n)", free entry and sort; everything else in a bottom sheet,
// and what is selected shows as removable pills, so nothing is set without being visible.
export function Filters() {
  const { t, lang } = useT();
  const f = useStore((s) => s.filters);
  const set = useStore((s) => s.setFilters);
  const cats = useCategories().data;
  const mobile = useMedia(MOBILE);
  const sheet = useRef<HTMLDialogElement>(null);
  // Same query as the grid below: shares its cache, so the sheet's "Show N" costs no request.
  const total = useEvents(filterParams(f)).data?.pages[0]?.total;

  const extras = (f.region ? 1 : 0) + f.areas.length + (f.city ? 1 : 0) + f.genres.length + (f.slot ? 1 : 0) + (f.hideSoldOut ? 1 : 0);
  const dirty = extras > 0 || f.freeOnly || f.when !== "all";
  const clear = () => set({ ...DEFAULT_FILTERS, q: f.q, sort: f.sort });

  // Today is "Tonight": the date strip starts tomorrow.
  const dates = (cats?.dates ?? []).filter((d) => d.value > today()).slice(0, 9);
  const day = (v: string, o: Intl.DateTimeFormatOptions) => new Date(v + "T12:00:00Z").toLocaleDateString(t.locale, { timeZone: TZ, ...o });
  const dateLabel = (v: string) => day(v, { weekday: "short", day: "numeric", month: "numeric" });
  const whenLabel = f.when === "tonight" ? t.tonight : f.when === "weekend" ? t.weekend : dateLabel(f.when);

  const toggles = (
    <>
      <Chip className="toggle free" on={f.freeOnly} onClick={() => set({ freeOnly: !f.freeOnly })}>
        <span className="tick" aria-hidden /> {t.freeOnly}
      </Chip>
      {!mobile && (
        <Chip className="toggle" on={f.hideSoldOut} onClick={() => set({ hideSoldOut: !f.hideSoldOut })}>
          <span className="tick" aria-hidden /> {t.hideSoldOut}
        </Chip>
      )}
    </>
  );
  const sort = (
    <label className="sort">
      <span className="sort-label">{t.sort}</span>
      <select value={f.sort} onChange={(e) => set({ sort: e.target.value })} aria-label={t.sort}>
        <option value="start_at">{t.sortDate}</option>
        <option value="min_price">{t.sortPrice}</option>
        <option value="-last_seen_at">{t.sortNew}</option>
      </select>
    </label>
  );

  return (
    <div className="filters">
      <FilterRow label={t.when} scroll>
        <Chip on={f.when === "all"} onClick={() => set({ when: "all" })}>{t.all}</Chip>
        <Chip on={f.when === "tonight"} onClick={() => set({ when: "tonight" })}>{t.tonight}</Chip>
        <Chip on={f.when === "weekend"} onClick={() => set({ when: "weekend" })}>{t.weekend}</Chip>
        <span className="sep" aria-hidden />
        {dates.map((d) => (
          <Chip key={d.value} on={f.when === d.value} onClick={() => set({ when: d.value })}>
            <span className="wd">{day(d.value, { weekday: lang === "he" ? "narrow" : "short" })}</span>
            {day(d.value, { day: "numeric", month: "numeric" })}
          </Chip>
        ))}
      </FilterRow>

      {mobile ? (
        <>
          <div className="filter-bar">
            <button className="f-open" onClick={() => sheet.current?.showModal()} aria-haspopup="dialog">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
                <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0" /><circle cx="16" cy="6" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="18" cy="18" r="2" />
              </svg>
              {t.filtersBtn}{extras > 0 && <b>{extras}</b>}
            </button>
            {toggles}
            {sort}
          </div>
          {dirty && (
            <div className="f-active">
              {f.when !== "all" && <Pill onClear={() => set({ when: "all" })}>{whenLabel}</Pill>}
              {f.region && <Pill onClear={() => set({ region: null, areas: [], city: null })}>{label(t.areas, f.region)}</Pill>}
              {f.areas.map((a) => <Pill key={a} onClear={() => set({ areas: f.areas.filter((x) => x !== a) })}>{label(t.areas, a)}</Pill>)}
              {f.city && <Pill onClear={() => set({ city: null })}>{f.city}</Pill>}
              {f.genres.map((g) => <Pill key={g} onClear={() => set({ genres: f.genres.filter((x) => x !== g) })}>{genreLabel(lang, g)}</Pill>)}
              {f.slot && <Pill onClear={() => set({ slot: null })}>{label(t.slots, f.slot)}</Pill>}
              {f.freeOnly && <Pill onClear={() => set({ freeOnly: false })}>{t.freeOnly}</Pill>}
              {f.hideSoldOut && <Pill onClear={() => set({ hideSoldOut: false })}>{t.hideSoldOut}</Pill>}
              <button className="f-clear" onClick={clear}>{t.clearFilters}</button>
            </div>
          )}
          <dialog className="f-sheet" ref={sheet} aria-label={t.filtersBtn}
            onClick={(e) => e.target === e.currentTarget && sheet.current?.close()}>
            <div className="f-sheet-head">
              <b>{t.filtersBtn}</b>
              <button className="f-x" onClick={() => sheet.current?.close()} aria-label={t.close}>✕</button>
            </div>
            <div className="f-sheet-body">
              <Rows all />
              <div className="f-sheet-toggles">
                <Chip className="toggle free" on={f.freeOnly} onClick={() => set({ freeOnly: !f.freeOnly })}>
                  <span className="tick" aria-hidden /> {t.freeOnly}
                </Chip>
                <Chip className="toggle" on={f.hideSoldOut} onClick={() => set({ hideSoldOut: !f.hideSoldOut })}>
                  <span className="tick" aria-hidden /> {t.hideSoldOut}
                </Chip>
              </div>
            </div>
            <div className="f-sheet-foot">
              <button className="f-clear" onClick={clear} disabled={!dirty}>{t.clearFilters}</button>
              <button className="f-apply" onClick={() => sheet.current?.close()}>
                {total != null ? t.showN(total) : t.loading}
              </button>
            </div>
          </dialog>
        </>
      ) : (
        <>
          <Rows />
          <div className="filter-bar">
            <div className="chips seg" role="group" aria-label={t.slot}>
              {Object.entries(t.slots).map(([k, name]) => (
                <Chip key={k} on={f.slot === k} onClick={() => set({ slot: f.slot === k ? null : k })}>{name}</Chip>
              ))}
            </div>
            <span className="sep" aria-hidden />
            {toggles}
            {dirty && <button className="f-clear" onClick={clear}>✕ {t.clearFilters}</button>}
            {sort}
          </div>
        </>
      )}
    </div>
  );
}

function Pill({ children, onClear }: { children: ReactNode; onClear: () => void }) {
  const { t } = useT();
  return (
    <button className="f-pill" onClick={onClear} aria-label={`${t.clearFilters}: ${typeof children === "string" ? children : ""}`}>
      {children} <span aria-hidden>✕</span>
    </button>
  );
}

/** Area, neighbourhood / city, genres (and, in the phone sheet, time of day). */
function Rows({ all }: { all?: boolean }) {
  const { t, lang } = useT();
  const f = useStore((s) => s.filters);
  const set = useStore((s) => s.setFilters);
  const cats = useCategories().data;
  const [moreGenres, setMoreGenres] = useState(false);

  const regionKeys = new Set(cats?.regions?.map((r) => r.value));
  // Under Tel Aviv: its neighbourhoods. Under another region: its cities.
  const subAreas = f.region === "tel_aviv"
    ? (cats?.areas ?? []).filter((a) => !regionKeys.has(a.value) && a.value !== "outside" && a.value !== "abroad")
    : (cats?.cities ?? []).filter((c) => f.region && c.area === f.region);
  // Busiest first; a selected genre always stays visible.
  const genres = [...(cats?.genres ?? [])].sort((a, b) => b.events - a.events);
  const shownGenres = all || moreGenres ? genres : genres.filter((g, i) => i < GENRES_SHOWN || f.genres.includes(g.value));
  const hidden = genres.length - shownGenres.length;

  return (
    <>
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

      {genres.length > 0 && (
        <FilterRow label={t.genres}>
          {shownGenres.map((g) => (
            <Chip key={g.value} on={f.genres.includes(g.value)} onClick={() => set({ genres: toggle(f.genres, g.value) })}>
              {genreLabel(lang, g.value)} <small>{g.events}</small>
            </Chip>
          ))}
          {!all && (hidden > 0 || moreGenres) && (
            <button className="chip more-chip" onClick={() => setMoreGenres(!moreGenres)} aria-expanded={moreGenres}>
              {moreGenres ? t.less : t.moreN(hidden)}
            </button>
          )}
        </FilterRow>
      )}

      {all && (
        <FilterRow label={t.slot}>
          {Object.entries(t.slots).map(([k, name]) => (
            <Chip key={k} on={f.slot === k} onClick={() => set({ slot: f.slot === k ? null : k })}>{name}</Chip>
          ))}
        </FilterRow>
      )}
    </>
  );
}

function FilterRow({ label, children, scroll }: { label: string; children: ReactNode; scroll?: boolean }) {
  return (
    <div className="frow" role="group" aria-label={label}>
      <span className="frow-label">{label}</span>
      <div className={"chips" + (scroll ? " scroll" : "")}>{children}</div>
    </div>
  );
}
