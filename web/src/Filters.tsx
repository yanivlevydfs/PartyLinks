import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { create } from "zustand";
import { SaveAlertButton } from "./alerts/SaveAlertButton";
import { useCategories, useEvents, type Params } from "./api";
import type { Categories, Facet } from "./types";
import { genreLabel } from "./i18n";
import { DEFAULT_FILTERS, useStore, type Filters as FilterState } from "./store";
import { Chip, label, Loader, Select, sourceName, today, tomorrow, TZ, useT } from "./ui";

// The one home-page filter: its state lives in the store, the API query comes from filterParams(),
// and every landing page (/tonight, /genre/x, /city/x) only presets that same state.

/** Query for /events from the filter state. */
export function filterParams(f: FilterState): Params {
  return {
    sort: f.sort,
    q: f.q.length >= 2 ? f.q : undefined,
    genre: f.genres,
    source: f.shops,
    time_slot: f.slots,
    // Any of the regions (Tel Aviv and/or regions of the country); one region alone narrows to neighbourhoods or a city.
    region: f.regions,
    area: f.areas,
    city: f.city ?? undefined,
    free_entry: f.freeOnly ? "true" : undefined,
    discount: f.discountOnly ? "true" : undefined,
    sold_out: f.hideSoldOut ? "false" : undefined,
    ...whenParams(f.when),
  };
}

function whenParams(when: string): Params {
  if (when === "tonight") return { date: today() };
  // "Weekend" is this weekend (as the server's /weekend page), not every weekend ahead.
  if (when === "weekend") return { weekend: "true", date_to: ahead(7) };
  if (when === "week") return { date_to: ahead(6) };
  if (when === "month") return { date_to: ahead(29) };
  const [from, to] = when.split("~");
  if (to) return { date_from: from + "T00:00:00", date_to: to + "T23:59:59" };
  return when !== "all" ? { date: when } : {};
}

/** The end of the day `days` after today (local dates, read in the server's timezone). */
const ahead = (days: number) => new Date(Date.parse(today() + "T12:00:00Z") + days * 864e5).toISOString().slice(0, 10) + "T23:59:59";
const PRESETS = ["all", "tonight", "weekend", "week", "month"];

/** Short name of a `when` value: a preset, a day, or a range of days. */
export function whenText(t: ReturnType<typeof useT>["t"], when: string) {
  if (when === "tonight" || when === "weekend" || when === "week" || when === "month") return t[when];
  if (when === tomorrow()) return t.tomorrow;
  const day = (v: string) => new Date(v + "T12:00:00Z").toLocaleDateString(t.locale, { timeZone: TZ, weekday: "short", day: "numeric", month: "numeric" });
  return when.split("~").map(day).join(" – ");
}

export const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

const MOBILE = "(max-width: 760px)";

// Which drawer and family are open. Outside the components: a picked filter moves the address
// (/genre/g_parties) and the router remounts the page, which must not close what the visitor is using.
const useDrawer = create<{
  panel: Panel | null; family: string | null; sheet: boolean;
  setPanel: (p: Panel | null) => void; setFamily: (f: string | null) => void; setSheet: (open: boolean) => void;
}>()((set) => ({
  panel: null, family: null, sheet: false,
  setPanel: (panel) => set({ panel }), setFamily: (family) => set({ family }), setSheet: (sheet) => set({ sheet }),
}));
type Panel = "area" | "genre" | "shop" | "slot";

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

// Desktop: dates on one line, then a bar of drop-down buttons (area / genres / ticket site / time) that open one
// drawer at a time, plus the main toggles and sort.
// Phones: dates + a bar with "Filters (n)", free entry and sort; everything else in a bottom sheet.
// Both: what is selected shows as removable pills, so nothing is set without being visible.
export function Filters() {
  const { t, lang } = useT();
  const f = useStore((s) => s.filters);
  const set = useStore((s) => s.setFilters);
  const cats = useCategories().data;
  const mobile = useMedia(MOBILE);
  const sheet = useRef<HTMLDialogElement>(null);
  // The phone sheet reopens after a remount (see useDrawer).
  useEffect(() => {
    if (mobile && useDrawer.getState().sheet && sheet.current && !sheet.current.open) sheet.current.showModal();
  }, [mobile]);
  const { panel: open, setPanel: setOpen } = useDrawer();
  // Same query as the grid below: shares its cache, so the sheet's "Show N" costs no request.
  const total = useEvents({ ...filterParams(f), sponsored: "true" }).data?.pages[0]?.total;

  const extras = f.regions.length + f.areas.length + (f.city ? 1 : 0) + f.genres.length + f.shops.length + f.slots.length + (f.hideSoldOut ? 1 : 0) + (f.discountOnly ? 1 : 0);
  const dirty = extras > 0 || f.freeOnly || f.when !== "all";
  const clear = () => set({ ...DEFAULT_FILTERS, q: f.q, sort: f.sort });

  const [calOpen, setCalOpen] = useState(false);
  const isDate = !PRESETS.includes(f.when) && f.when !== tomorrow();
  const counts = new Map((cats?.dates ?? []).map((d) => [d.value, d.events]));
  const whenLabel = whenText(t, f.when);
  const pickWhen = (when: string) => { set({ when }); setCalOpen(false); };
  // A first day filters at once and keeps the calendar open; a second click closes it (same day: that day only).
  const pickDay = (v: string, done: boolean) => {
    const when = v === today() ? "tonight" : v;
    if (done) pickWhen(when); else set({ when });
  };

  const toggles = (
    <>
      <Chip className="toggle free" on={f.freeOnly} onClick={() => set({ freeOnly: !f.freeOnly })}>
        <span className="tick" aria-hidden /> {mobile ? t.free : t.freeOnly}
      </Chip>
      {!mobile && (
        <Chip className="toggle" on={f.discountOnly} onClick={() => set({ discountOnly: !f.discountOnly })}>
          <span className="tick" aria-hidden /> {t.discountOnly}
        </Chip>
      )}
      {!mobile && (
        <Chip className="toggle" on={f.hideSoldOut} onClick={() => set({ hideSoldOut: !f.hideSoldOut })}>
          <span className="tick" aria-hidden /> {t.hideSoldOut}
        </Chip>
      )}
    </>
  );
  const sort = (
    <div className="sort">
      <span className="sort-label">{t.sort}</span>
      <Select value={f.sort} onChange={(sort) => set({ sort })} label={t.sort}
        options={[["start_at", t.sortDate], ["min_price", t.sortPrice], ["-last_seen_at", t.sortNew]]} />
    </div>
  );

  const active = (dirty || f.q.trim().length >= 2) && (
    <div className="f-active">
      {f.when !== "all" && <Pill onClear={() => set({ when: "all" })}>{whenLabel}</Pill>}
      {f.regions.map((r) => <Pill key={r} onClear={() => set({ regions: f.regions.filter((x) => x !== r), areas: [], city: null })}>{label(t.areas, r)}</Pill>)}
      {f.areas.map((a) => <Pill key={a} onClear={() => set({ areas: f.areas.filter((x) => x !== a) })}>{label(t.areas, a)}</Pill>)}
      {f.city && <Pill onClear={() => set({ city: null })}>{f.city}</Pill>}
      {f.genres.map((g) => <Pill key={g} onClear={() => set({ genres: f.genres.filter((x) => x !== g) })}>{genreLabel(lang, g)}</Pill>)}
      {f.shops.map((s) => <Pill key={s} onClear={() => set({ shops: f.shops.filter((x) => x !== s) })}>{sourceName(s)}</Pill>)}
      {f.slots.map((s) => <Pill key={s} onClear={() => set({ slots: f.slots.filter((x) => x !== s) })}>{label(t.slots, s)}</Pill>)}
      {f.freeOnly && <Pill onClear={() => set({ freeOnly: false })}>{t.freeOnly}</Pill>}
      {f.discountOnly && <Pill onClear={() => set({ discountOnly: false })}>{t.discountOnly}</Pill>}
      {f.hideSoldOut && <Pill onClear={() => set({ hideSoldOut: false })}>{t.hideSoldOut}</Pill>}
      <SaveAlertButton filters={f} />
      {dirty && <button className="f-clear" onClick={clear}>{t.clearFilters}</button>}
    </div>
  );
  const panels: [Panel, string, number][] = [
    ["area", t.area, f.regions.length + f.areas.length + (f.city ? 1 : 0)],
    ["genre", t.genres, f.genres.length],
    ["shop", t.shop, f.shops.length],
    ["slot", t.slot, f.slots.length],
  ];

  return (
    <div className="filters">
      <FilterRow label={t.when} scroll>
        <Chip on={f.when === "all"} onClick={() => pickWhen("all")}>{t.all}</Chip>
        <Chip on={f.when === "tonight"} onClick={() => pickWhen("tonight")}>{t.tonight}</Chip>
        <Chip on={f.when === tomorrow()} onClick={() => pickWhen(tomorrow())}>{t.tomorrow}</Chip>
        <Chip on={f.when === "weekend"} onClick={() => pickWhen("weekend")}>{t.weekend}</Chip>
        <Chip on={f.when === "week"} onClick={() => pickWhen("week")}>{t.week}</Chip>
        <Chip on={f.when === "month"} onClick={() => pickWhen("month")}>{t.month}</Chip>
        <button className={"chip f-drop cal-btn" + (isDate ? " on" : "") + (calOpen ? " open" : "")} aria-expanded={calOpen}
          onClick={() => setCalOpen(!calOpen)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
            <rect x="3.5" y="5" width="17" height="15.5" rx="3" /><path d="M3.5 10h17M8 3v4M16 3v4" />
          </svg>
          {isDate ? whenLabel : t.pickDate}<span className="caret" aria-hidden>▾</span>
        </button>
      </FilterRow>
      {calOpen && (
        <div className="f-drawer">
          <Calendar value={f.when === "tonight" ? today() : f.when} counts={counts} onPick={pickDay} />
        </div>
      )}

      {mobile ? (
        <>
          <div className="filter-bar">
            <button className="f-open" onClick={() => { sheet.current?.showModal(); useDrawer.getState().setSheet(true); }} aria-haspopup="dialog">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
                <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0" /><circle cx="16" cy="6" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="18" cy="18" r="2" />
              </svg>
              {t.filtersBtn}{extras > 0 && <b>{extras}</b>}
            </button>
            {toggles}
            {sort}
          </div>
          {active}
          <dialog className="f-sheet" ref={sheet} aria-label={t.filtersBtn} onClose={() => useDrawer.getState().setSheet(false)}
            onClick={(e) => e.target === e.currentTarget && sheet.current?.close()}>
            <div className="f-sheet-head">
              <b>{t.filtersBtn}</b>
              <button className="f-x" onClick={() => sheet.current?.close()} aria-label={t.close}>✕</button>
            </div>
            <div className="f-sheet-body">
              <Rows />
              <div className="f-sheet-toggles">
                <Chip className="toggle free" on={f.freeOnly} onClick={() => set({ freeOnly: !f.freeOnly })}>
                  <span className="tick" aria-hidden /> {t.freeOnly}
                </Chip>
                <Chip className="toggle" on={f.discountOnly} onClick={() => set({ discountOnly: !f.discountOnly })}>
                  <span className="tick" aria-hidden /> {t.discountOnly}
                </Chip>
                <Chip className="toggle" on={f.hideSoldOut} onClick={() => set({ hideSoldOut: !f.hideSoldOut })}>
                  <span className="tick" aria-hidden /> {t.hideSoldOut}
                </Chip>
              </div>
            </div>
            <div className="f-sheet-foot">
              <button className="f-clear" onClick={clear} disabled={!dirty}>{t.clearFilters}</button>
              <button className="f-apply" onClick={() => sheet.current?.close()}>
                {total != null ? t.showN(total) : <Loader small />}
              </button>
            </div>
          </dialog>
        </>
      ) : (
        <>
          <div className="filter-bar">
            {panels.map(([k, name, n]) => (
              <button key={k} className={"chip f-drop" + (open === k ? " open" : "")} aria-expanded={open === k}
                onClick={() => setOpen(open === k ? null : k)}>
                {name}{n > 0 && <b>{n}</b>}<span className="caret" aria-hidden>▾</span>
              </button>
            ))}
            <span className="sep" aria-hidden />
            {toggles}
            {sort}
          </div>
          {open && <div className="f-drawer"><Rows only={open} /></div>}
          {active}
        </>
      )}
    </div>
  );
}

/** Empty result: one button per active filter, showing how many events dropping it would bring back. */
export function Relax({ didYouMean }: { didYouMean?: string | null }) {
  const { t, lang } = useT();
  const f = useStore((s) => s.filters);
  const set = useStore((s) => s.setFilters);
  if (f.q) {
    return (
      <div className="msg">
        <p>{didYouMean ? t.closest(f.q) : t.noMatch(f.q)}</p>
        {didYouMean && <button className="f-pill" onClick={() => set({ ...DEFAULT_FILTERS, q: didYouMean })}>{didYouMean}</button>}
      </div>
    );
  }
  const options: [string, Partial<FilterState>][] = [
    ...(f.freeOnly ? [[t.freeOnly, { freeOnly: false }] as [string, Partial<FilterState>]] : []),
    ...(f.when !== "all" ? [[whenText(t, f.when), { when: "all" }] as [string, Partial<FilterState>]] : []),
    ...f.genres.map((g) => [genreLabel(lang, g), { genres: f.genres.filter((x) => x !== g) }] as [string, Partial<FilterState>]),
    ...f.slots.map((s) => [label(t.slots, s), { slots: f.slots.filter((x) => x !== s) }] as [string, Partial<FilterState>]),
    ...f.regions.map((r) => [label(t.areas, r), { regions: f.regions.filter((x) => x !== r), areas: [], city: null }] as [string, Partial<FilterState>]),
    ...f.shops.map((s) => [sourceName(s), { shops: f.shops.filter((x) => x !== s) }] as [string, Partial<FilterState>]),
    ...(f.discountOnly ? [[t.discountOnly, { discountOnly: false }] as [string, Partial<FilterState>]] : []),
    ...(f.hideSoldOut ? [[t.hideSoldOut, { hideSoldOut: false }] as [string, Partial<FilterState>]] : []),
  ];
  if (!options.length) return <p className="msg">{t.empty}</p>;
  return (
    <div className="msg">
      <p>{t.relax}</p>
      <div className="relax">
        {options.map(([name, patch]) => <RelaxOption key={name} name={name} params={filterParams({ ...f, ...patch })} onClick={() => set(patch)} />)}
      </div>
    </div>
  );
}

function RelaxOption({ name, params, onClick }: { name: string; params: Params; onClick: () => void }) {
  const { t } = useT();
  const total = useEvents(params).data?.pages[0]?.total;
  return total ? <button className="f-pill" onClick={onClick}>{t.without(name, total)}</button> : null;
}

function Pill({ children, onClear }: { children: ReactNode; onClear: () => void }) {
  const { t } = useT();
  return (
    <button className="f-pill" onClick={onClear} aria-label={`${t.clearFilters}: ${typeof children === "string" ? children : ""}`}>
      {children} <span aria-hidden>✕</span>
    </button>
  );
}

/** One drawer's rows (desktop), or all of them (the phone sheet). */
function Rows({ only }: { only?: Panel }) {
  const { t } = useT();
  const f = useStore((s) => s.filters);
  const set = useStore((s) => s.setFilters);
  const cats = useCategories().data;
  const show = (p: Panel) => !only || only === p;

  const regionKeys = new Set(cats?.regions?.map((r) => r.value));
  // Under Tel Aviv alone: its neighbourhoods. Under another region alone: its cities.
  const one = f.regions.length === 1 ? f.regions[0] : null;
  const subAreas = one === "tel_aviv"
    ? (cats?.areas ?? []).filter((a) => !regionKeys.has(a.value) && a.value !== "outside" && a.value !== "abroad")
    : (cats?.cities ?? []).filter((c) => one && c.area === one);
  const shops = cats?.sources ?? []; // busiest first

  return (
    <>
      {show("area") && !!cats?.regions?.length && (
        <FilterRow label={t.area}>
          <Chip on={!f.regions.length} onClick={() => set({ regions: [], areas: [], city: null })}>{t.allAreas}</Chip>
          {cats.regions.map((r) => (
            <Chip key={r.value} on={f.regions.includes(r.value)}
              onClick={() => set({ regions: toggle(f.regions, r.value), areas: [], city: null })}>
              {label(t.areas, r.value)} <small>{r.events}</small>
            </Chip>
          ))}
        </FilterRow>
      )}

      {show("area") && one && subAreas.length > 1 && (
        <FilterRow label={one === "tel_aviv" ? t.neighborhood : t.city}>
          <Chip on={!f.areas.length && !f.city} onClick={() => set({ areas: [], city: null })}>{t.all}</Chip>
          {subAreas.map((a) => one === "tel_aviv" ? (
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

      {show("genre") && !!cats?.genres.length && <GenrePicker cats={cats} value={f.genres} onChange={(genres) => set({ genres })} />}

      {show("shop") && shops.length > 1 && (
        <FilterRow label={t.shop}>
          {shops.map((s) => (
            <Chip key={s.value} on={f.shops.includes(s.value)} onClick={() => set({ shops: toggle(f.shops, s.value) })}>
              {sourceName(s.value)} <small>{s.events}</small>
            </Chip>
          ))}
        </FilterRow>
      )}

      {show("slot") && (
        <FilterRow label={t.slot}>
          {Object.entries(t.slots).map(([k, name]) => (
            <Chip key={k} on={f.slots.includes(k)} onClick={() => set({ slots: toggle(f.slots, k) })}>{name}</Chip>
          ))}
        </FilterRow>
      )}
    </>
  );
}

export const GROUP_ICON: Record<string, string> = {
  g_parties: "🎧", g_live: "🎤", g_stage: "🎭", g_culture: "🖼️", g_kids: "🧸", g_food: "🍽️", g_leisure: "🎡", g_tech: "💻", g_community: "🌈",
};

/** Genres by family: the families, then the open one's genres (an umbrella genre with its sub-genres).
 *  Desktop: a row of families over the open one. Phone sheet: a list of families; one opens in place, with a way back.
 *  Typing in the search box lists every matching genre instead. */
export function GenrePicker({ cats, value, onChange, quiz }: { cats?: Categories; value: string[]; onChange: (genres: string[]) => void; quiz?: boolean }) {
  const { t, lang } = useT();
  const mobile = useMedia(MOBILE);
  const f = { genres: value };
  const set = ({ genres }: { genres: string[] }) => onChange(genres);
  const name = (v: string) => genreLabel(lang, v);
  const abc = (a: string, b: string) => name(a).localeCompare(name(b), lang);
  // Families and categories in alphabetical order (א-ת / A-Z), each with its count.
  const groups = [...(cats?.genre_groups ?? [])].sort((a, b) => abc(a.value, b.value));
  const fams = cats?.genre_families ?? {};
  const counts = new Map(cats?.genres.map((g) => [g.value, g.events]));
  // Sub-genres all the way down (dance -> latin_dance -> salsa); hip hop <-> R&B point at each other.
  const subsOf = (v: string) => {
    const out: string[] = [];
    for (let i = -1; i < out.length; i++) for (const x of fams[i < 0 ? v : out[i]] ?? []) if (x !== v && !out.includes(x)) out.push(x);
    return out;
  };
  const inside = (g: Facet) => [g.value, ...(g.members ?? []).flatMap((m) => [m, ...subsOf(m)])];
  const picked = (g: Facet) => inside(g).some((v) => f.genres.includes(v));
  // Quiz and filter each keep their own open family; the filter's survives the remount a picked filter's new address brings.
  const [quizKey, setQuizKey] = useState<string | null>(null);
  const drawer = useDrawer();
  const openKey = quiz ? quizKey : drawer.family;
  const setOpen = quiz ? setQuizKey : drawer.setFamily;
  const [q, setQ] = useState("");
  // Desktop always shows one family: the chosen one, else the first with a pick, else the first.
  const group = groups.find((g) => g.value === openKey) ?? (mobile ? undefined : groups.find(picked) ?? groups[0]);
  const sorted = (vs: string[]) => vs.filter((v) => counts.get(v)).sort(abc);

  // A whole family or umbrella replaces the genres picked inside it; a genre inside replaces the whole.
  const pickAll = (v: string, within: string[]) =>
    set({ genres: f.genres.includes(v) ? f.genres.filter((x) => x !== v) : [...f.genres.filter((x) => !within.includes(x)), v] });
  const pick = (v: string, parents: string[]) =>
    set({ genres: toggle(f.genres.filter((x) => x === v || !parents.includes(x)), v) });
  const chip = (v: string, parents: string[]) => (
    <Chip key={v} on={f.genres.includes(v)} onClick={() => pick(v, parents)}>{name(v)} <small>{counts.get(v)}</small></Chip>
  );

  const search = (
    <input className="g-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t.findGenre} aria-label={t.findGenre} />
  );
  const needle = q.trim().toLowerCase();
  const found = needle && sorted([...counts.keys()].filter((v) => !v.startsWith("g_") && name(v).toLowerCase().includes(needle)));

  const body = (g: Facet) => {
    const members = sorted(g.members ?? []);
    // An umbrella opens as its own section, unless its sub-genres are all in the family (hip hop <-> R&B).
    const umbrella = (m: string) => (fams[m] ?? []).some((s) => !members.includes(s));
    return (
      <div className="g-body">
        <div className="chips">
          <Chip on={f.genres.includes(g.value)} onClick={() => pickAll(g.value, inside(g))}>
            {t.all} <small>{g.events}</small>
          </Chip>
          {members.filter((m) => !umbrella(m)).map((m) => chip(m, [g.value]))}
        </div>
        {members.filter(umbrella).map((m) => (
          <div key={m} className="g-sub">
            <span className="g-sub-name">{name(m)}</span>
            <div className="chips">
              <Chip on={f.genres.includes(m)} onClick={() => pickAll(m, [g.value, ...subsOf(m)])}>
                {t.allOf(name(m))} <small>{counts.get(m)}</small>
              </Chip>
              {sorted([...new Set(subsOf(m))]).map((s) => chip(s, [g.value, m, ...subsOf(m).filter((x) => subsOf(x).includes(s))]))}
            </div>
          </div>
        ))}
      </div>
    );
  };

  if (mobile) {
    return (
      <div className="frow g-mobile" role="group" aria-label={t.genres}>
        {group && !needle ? (
          <>
            <button className="g-back" onClick={() => setOpen(null)}>
              <span aria-hidden>{lang === "he" ? "›" : "‹"}</span> {GROUP_ICON[group.value]} {name(group.value)}
            </button>
            {body(group)}
          </>
        ) : (
          <>
            <span className="frow-label">{t.genres}</span>
            {search}
            {found ? <div className="chips">{found.map((v) => chip(v, []))}</div> : (
              <div className="g-list">
                {groups.map((g) => (
                  <button key={g.value} className="g-row" onClick={() => setOpen(g.value)}>
                    <span aria-hidden>{GROUP_ICON[g.value]}</span>
                    <b>{name(g.value)}</b>
                    {picked(g) && <i className="g-dot" aria-label="✓" />}
                    <small>{g.events}</small>
                    <span className="g-go" aria-hidden>{lang === "he" ? "‹" : "›"}</span>
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    );
  }

  return (
    <div className="g-picker" role="group" aria-label={t.genres}>
      <div className="g-head">
        {search}
        <div className="chips">
          {groups.map((g) => (
            <button key={g.value} aria-expanded={g === group} aria-pressed={f.genres.includes(g.value)}
              className={"chip g-fam" + (g === group && !needle ? " open" : "") + (f.genres.includes(g.value) ? " on" : picked(g) ? " some" : "")}
              onClick={() => {
                // A click picks the family (and opens it); a click on the open, picked one drops it.
                // A family with only some of its categories picked just opens.
                setQ("");
                setOpen(g.value);
                if (g === group || !picked(g)) pickAll(g.value, inside(g));
              }}>
              <span aria-hidden>{GROUP_ICON[g.value]}</span> {name(g.value)} <small>{g.events}</small>
            </button>
          ))}
        </div>
      </div>
      {found ? <div className="chips g-body">{found.map((v) => chip(v, []))}</div> : group && body(group)}
    </div>
  );
}

/** Month grid, Sunday first. A day glows by how many events it has; past days and days with none are disabled.
 *  `value` is one day or a range "from~to". The first click picks one day (done=false); a second click on the
 *  same day keeps just it, on a later day makes the range (done=true); an earlier day starts over. */
export function Calendar({ value, counts, onPick }: { value: string; counts: Map<string, number>; onPick: (v: string, done: boolean) => void }) {
  const { t, lang } = useT();
  const now = today();
  const [anchor, setAnchor] = useState<string | null>(null); // first click of this opening, not the saved filter
  const click = (v: string) => {
    if (anchor && v >= anchor) { setAnchor(null); onPick(v === anchor ? v : `${anchor}~${v}`, true); }
    else { setAnchor(v); onPick(v, false); }
  };
  const [from, to = from] = value.split("~");
  const [month, setMonth] = useState(() => (/^\d{4}-\d\d-\d\d$/.test(value) ? value : now).slice(0, 7));
  const [y, m] = month.split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1, 1));
  const length = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const lastMonth = [...counts.keys()].sort().at(-1)?.slice(0, 7) ?? now.slice(0, 7);
  const max = Math.max(1, ...counts.values());
  const fmt = (d: Date, o: Intl.DateTimeFormatOptions) => d.toLocaleDateString(t.locale, { timeZone: "UTC", ...o });
  const shift = (n: number) => setMonth(new Date(Date.UTC(y, m - 1 + n, 1)).toISOString().slice(0, 7));
  const rtl = lang === "he";

  return (
    <div className="cal">
      <div className="cal-head">
        <button className="cal-nav" onClick={() => shift(-1)} disabled={month <= now.slice(0, 7)} aria-label="‹">{rtl ? "›" : "‹"}</button>
        <b>{fmt(first, { month: "long", year: "numeric" })}</b>
        <button className="cal-nav" onClick={() => shift(1)} disabled={month >= lastMonth} aria-label="›">{rtl ? "‹" : "›"}</button>
      </div>
      <div className="cal-grid">
        {/* 7 Jan 2024 was a Sunday. */}
        {Array.from({ length: 7 }, (_, i) => (
          <span key={i} className="cal-wd">{fmt(new Date(Date.UTC(2024, 0, 7 + i)), { weekday: "narrow" })}</span>
        ))}
        {Array.from({ length: first.getUTCDay() }, (_, i) => <span key={"pad" + i} />)}
        {Array.from({ length }, (_, i) => {
          const v = `${month}-${String(i + 1).padStart(2, "0")}`;
          const n = counts.get(v) ?? 0;
          const on = v >= from && v <= to;
          return (
            <button key={v} className={"cal-day" + (on ? " on" : "") + (on && from !== to ? " range" : "") + (v === now ? " today" : "")}
              disabled={v < now || !n} aria-pressed={on} onClick={() => click(v)}
              aria-label={`${fmt(new Date(v + "T12:00:00Z"), { weekday: "long", day: "numeric", month: "long" })} · ${n} ${t.calEvents}`}
              title={n ? `${n} ${t.calEvents}` : undefined}
              style={{ "--heat": n / max } as CSSProperties}>
              <span>{i + 1}</span>{n > 0 && <small>{n}</small>}
            </button>
          );
        })}
      </div>
      <p className="cal-note">{t.calNote}</p>
    </div>
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
