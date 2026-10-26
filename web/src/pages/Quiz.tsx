import { useState, type CSSProperties, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useCategories, useEvents } from "../api";
import { Calendar, filterParams, toggle } from "../Filters";
import { genreLabel } from "../i18n";
import { DEFAULT_FILTERS, useStore, type Filters } from "../store";
import { eventPath, label, today, tomorrow, TZ, useT } from "../ui";
import { Weather } from "../Weather";

const SLOT_ICONS: Record<string, string> = { morning: "☀️", afternoon: "🌤️", evening: "🌆", night: "🌙" };
const GENRES_SHOWN = 12;
const STEPS = 4;

/** The query of a draft, without the sort (facets don't care, so their cache stays shared). */
function query(f: Filters) {
  const { sort: _, ...params } = filterParams(f);
  return params;
}

/**
 * "Where to?": when, where, vibe, time. Each question only offers what the earlier answers left
 * (its options are the facets of the matching events, busiest first, with counts), and the button
 * says how many events the answers so far add up to. The last answer opens that list.
 */
export function Quiz() {
  const { t, lang } = useT();
  const q = t.quiz;
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [d, setD] = useState<Filters>(() => ({ ...DEFAULT_FILTERS, sort: useStore.getState().filters.sort }));
  const patch = (p: Partial<Filters>) => setD((cur) => ({ ...cur, ...p }));
  const [calOpen, setCalOpen] = useState(false);
  const [allGenres, setAllGenres] = useState(false);
  const [geo, setGeo] = useState<"idle" | "busy" | "fail" | { place: string }>("idle");

  // The answers before this step: its options never shrink because of its own picks.
  const before = { ...DEFAULT_FILTERS, when: step > 0 ? d.when : "all",
    ...(step > 1 && { regions: d.regions, city: d.city }), ...(step > 2 && { genres: d.genres }) };
  const cats = useCategories(query(before)).data;
  const dateCount = new Map((cats?.dates ?? []).map((f) => [f.value, f.events]));
  const weekendTotal = useEvents(filterParams({ ...d, when: "weekend" })).data?.pages[0]?.total;
  // Same query as the result grid: the list is already loaded when the last answer opens it.
  const results = useEvents(filterParams(d)).data?.pages[0];
  const total = step > 0 ? results?.total : undefined;
  // Unfiltered count, the home grid's own query: shows the answers narrowed the list, nothing went missing.
  const all = useEvents(filterParams({ ...DEFAULT_FILTERS, sort: d.sort })).data?.pages[0]?.total;

  const finish = (f: Filters = d) => {
    useStore.getState().setFilters(f);
    useStore.getState().setQuizDone(true);
    scrollTo({ top: 0 });
  };
  const next = () => (step < STEPS - 1 ? setStep(step + 1) : finish());
  const pickWhen = (when: string) => { patch({ when }); setCalOpen(false); setStep(1); };
  const surprise = () => {
    const items = results?.items ?? [];
    if (items.length) navigate(eventPath(items[Math.floor(Math.random() * items.length)]));
  };

  const locate = () => {
    if (!navigator.geolocation) return setGeo("fail");
    setGeo("busy");
    navigator.geolocation.getCurrentPosition(
      async ({ coords }) => {
        try {
          const res = await fetch(`/locate?lat=${coords.latitude.toFixed(3)}&lng=${coords.longitude.toFixed(3)}`);
          const where: { region: string | null; city: string | null } = await res.json();
          if (!where.region) return setGeo("fail");
          patch({ regions: [where.region], areas: [], city: where.region === "tel_aviv" ? null : where.city });
          setGeo({ place: where.city ?? label(t.areas, where.region) });
        } catch {
          setGeo("fail");
        }
      },
      () => setGeo("fail"),
      { timeout: 10_000, maximumAge: 10 * 60_000 },
    );
  };

  const day = (v: string) => new Date(v + "T12:00:00Z").toLocaleDateString(t.locale, { timeZone: TZ, weekday: "long" });
  const isPicked = d.when !== "all" && ![today(), tomorrow(), "tonight", "weekend"].includes(d.when);
  const regions = cats?.regions ?? [];
  const genres = [...(cats?.genres ?? [])].sort((a, b) => b.events - a.events);
  const shownGenres = allGenres ? genres : genres.slice(0, GENRES_SHOWN);
  // Picked options stay visible even if the facets no longer list them.
  const slots = Object.keys(t.slots).filter((k) => d.slots.includes(k) || cats?.time_slots.some((s) => s.value === k));
  const slotCount = new Map((cats?.time_slots ?? []).map((s) => [s.value, s.events]));

  return (
    <section className="quiz" aria-label={q.title}>
      <div className="quiz-top">
        <div className="quiz-eq" aria-hidden>
          {Array.from({ length: STEPS }, (_, i) => (
            <span key={i} className={i < step ? "done" : i === step ? "now" : ""} style={{ "--i": i } as CSSProperties} />
          ))}
        </div>
        <div className="quiz-q">
          <h2 key={step}>{q.steps[step]}</h2>
          <p>{q.hints[step]}</p>
        </div>
        {total != null && (
          <div className={"quiz-count" + (total ? "" : " zero")} aria-live="polite">
            <b key={total}>{total}</b>
            <span>{total ? q.waiting : q.none}</span>
            {all != null && all > total && <small>{q.outOf(all)}</small>}
          </div>
        )}
      </div>

      <div className="quiz-body" key={step}>
        {step === 0 && (
          <>
            <div className="quiz-tiles">
              <Tile icon="⚡" title={q.today} sub={day(today())} n={dateCount.get(today())} on={d.when === "tonight"} onClick={() => pickWhen("tonight")} wx={tileWeather(evening(today()))} />
              <Tile icon="🌙" title={t.tomorrow} sub={day(tomorrow())} n={dateCount.get(tomorrow())} on={d.when === tomorrow()} onClick={() => pickWhen(tomorrow())} wx={tileWeather(evening(tomorrow()))} />
              <Tile icon="🎉" title={t.weekend} n={weekendTotal} on={d.when === "weekend"} onClick={() => pickWhen("weekend")} wx={tileWeather(evening(friday(), 21))} />
              <Tile icon="📅" title={q.calendar} sub={isPicked ? day(d.when) : undefined} on={isPicked || calOpen} onClick={() => setCalOpen(!calOpen)} />
            </div>
            {calOpen && (
              <div className="quiz-cal">
                <Calendar value={d.when} counts={dateCount} onPick={(v) => pickWhen(v === today() ? "tonight" : v)} />
              </div>
            )}
          </>
        )}

        {step === 1 && (
          <>
            <button className={"quiz-near" + (geo === "busy" ? " busy" : "")} onClick={locate} disabled={geo === "busy"}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
                <circle cx="12" cy="12" r="3.5" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" /><circle cx="12" cy="12" r="7.5" />
              </svg>
              {geo === "busy" ? q.locating : q.near}
            </button>
            {geo === "fail" && <p className="quiz-note">{q.noLocation}</p>}
            {typeof geo === "object" && <p className="quiz-note ok">{q.located(geo.place)}</p>}
            <div className="quiz-chips">
              <Opt on={!d.regions.length} onClick={() => patch({ regions: [], areas: [], city: null })}>{q.anywhere}</Opt>
              {regions.map((r) => (
                <Opt key={r.value} n={r.events} on={d.regions.includes(r.value)}
                  onClick={() => patch({ regions: toggle(d.regions, r.value), areas: [], city: null })}>
                  {label(t.areas, r.value)}
                </Opt>
              ))}
            </div>
          </>
        )}

        {step === 2 && (
          <div className="quiz-chips">
            {shownGenres.map((g) => (
              <Opt key={g.value} n={g.events} on={d.genres.includes(g.value)} onClick={() => patch({ genres: toggle(d.genres, g.value) })}>
                {genreLabel(lang, g.value)}
              </Opt>
            ))}
            {!allGenres && genres.length > GENRES_SHOWN && (
              <button className="quiz-more" onClick={() => setAllGenres(true)}>{q.more(genres.length - GENRES_SHOWN)} ▾</button>
            )}
          </div>
        )}

        {step === 3 && (
          <div className="quiz-tiles">
            {slots.map((k) => (
              <Tile key={k} icon={SLOT_ICONS[k] ?? "🕒"} title={label(t.slots, k)} n={slotCount.get(k) ?? 0}
                on={d.slots.includes(k)} onClick={() => patch({ slots: toggle(d.slots, k) })} />
            ))}
          </div>
        )}
      </div>

      <div className="quiz-foot">
        {step > 0 && <button className="quiz-back" onClick={() => setStep(step - 1)}>{lang === "he" ? "→" : "←"} {q.back}</button>}
        <button className="quiz-skipall" onClick={() => finish({ ...DEFAULT_FILTERS, sort: d.sort })}>{q.skipAll}</button>
        {step > 0 && (
          <>
            {step === STEPS - 1 && !!total && <button className="quiz-skip" onClick={surprise}>{q.surprise}</button>}
            <button className="quiz-go" onClick={next} disabled={total === 0}>
              {step === STEPS - 1 ? q.go(total ?? 0) : picked(step, d) ? q.next : q.skip}
              <span aria-hidden>{lang === "he" ? "←" : "→"}</span>
            </button>
          </>
        )}
      </div>
    </section>
  );
}

// The tiles' forecast: Tel Aviv in the evening (now, once the evening has begun); the weekend's is Friday night.
const TLV = { lat: 32.08, lng: 34.78 };
function evening(date: string, hour = 20): string {
  const at = new Date(`${date}T${String(hour).padStart(2, "0")}:00:00`);
  return new Date(Math.max(at.getTime(), Date.now())).toISOString();
}
function friday(): string {
  const d = new Date();
  d.setDate(d.getDate() + ((5 - d.getDay() + 7) % 7));
  return d.toLocaleDateString("en-CA", { timeZone: TZ });
}
const tileWeather = (start: string) => <Weather lat={TLV.lat} lng={TLV.lng} start={start} />;

const picked = (step: number, d: Filters) => (step === 1 ? d.regions.length : step === 2 ? d.genres.length : d.slots.length) > 0;

function Tile({ icon, title, sub, n, on, onClick, wx }: { icon: string; title: string; sub?: string; n?: number; on: boolean; onClick: () => void; wx?: ReactNode }) {
  return (
    <button className={"quiz-tile" + (on ? " on" : "")} aria-pressed={on} onClick={onClick} disabled={n === 0 && !on}>
      <span className="quiz-icon" aria-hidden>{icon}</span>
      <b>{title}</b>
      {sub && <small>{sub}</small>}
      {n != null && <em>{n}</em>}
      {wx}
    </button>
  );
}

function Opt({ n, on, onClick, children }: { n?: number; on: boolean; onClick: () => void; children: string }) {
  return (
    <button className={"chip quiz-opt" + (on ? " on" : "")} aria-pressed={on} onClick={onClick}>
      {children}{n != null && <small>{n}</small>}
    </button>
  );
}
