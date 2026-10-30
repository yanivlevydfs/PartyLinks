// /alerts: the user's saved searches as cards (what it looks for, new matches, on/off, instant email,
// see its events, edit, delete), and one form, in steps, to make a new one or edit one:
// what to look for -> how many events match now -> a name -> how to be told -> save.
import { useId, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { SignInButton } from "../accounts/AccountSlot";
import { useNoIndex } from "../accounts/AccountPage";
import { useMe } from "../accounts/useMe";
import { useCategories } from "../api";
import { GenrePicker, toggle, whenText } from "../Filters";
import { DEFAULT_FILTERS, useStore } from "../store";
import { byName, Chip, Hero, label, Loader, useT, useTitle } from "../ui";
import { useAlertText } from "./i18n";
import { EMPTY } from "./SaveAlertButton";
import { criteriaWords, isEmpty, useAlertActions, useAlerts, useCriteriaName, usePreview, type Alert, type Criteria } from "./useAlerts";
import "./alerts.css";

const WHEN = ["all", "weekend", "week", "month"] as const;

/** An on/off switch with its words beside it (the site's consent switch). */
function Switch({ on, onChange, children }: { on: boolean; onChange: () => void; children: ReactNode }) {
  return (
    <label className="alert-switch">
      <input type="checkbox" role="switch" className="switch" checked={on} onChange={onChange} />
      <span>{children}</span>
    </label>
  );
}

/** One labelled group of chips in the form. */
function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="alert-group">
      <legend>{title}</legend>
      <div className="alert-chips">{children}</div>
    </fieldset>
  );
}

function AlertCard({ a }: { a: Alert }) {
  const t = useAlertText();
  const { t: ui, lang } = useT();
  const { edit, remove } = useAlertActions();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const show = () => {
    useStore.getState().setFilters({ ...DEFAULT_FILTERS, ...a.criteria, shops: [], hideSoldOut: false, sort: "start_at" });
    useStore.getState().setQuizDone(true);
    navigate("/");
  };
  if (editing) return <li className="alert-card"><AlertForm alert={a} onDone={() => setEditing(false)} /></li>;
  return (
    <li className={"alert-card" + (a.active ? "" : " off")}>
      <div className="alert-head">
        <h3 dir="auto">{a.name}</h3>
        {a.unseen > 0 && <span className="alert-new">{t.unseen(a.unseen)}</span>}
      </div>
      <div className="alert-tags">{criteriaWords(a.criteria, ui, lang).map((w) => <span key={w} className="tag" dir="auto">{w}</span>)}</div>
      {!a.active && <p className="alert-note">{t.pausedNote}</p>}
      <div className="alert-switches">
        <Switch on={a.active} onChange={() => edit(a.id, { active: !a.active })}>{a.active ? t.active : t.paused}</Switch>
        {useAlerts().data?.instant_email && <Switch on={a.instant} onChange={() => edit(a.id, { instant: !a.instant })}>{t.email}</Switch>}
      </div>
      {confirm ? (
        <div className="alert-actions" role="group" aria-label={t.confirmRemove}>
          <b>{t.confirmRemove}</b>
          <button type="button" className="c-btn danger" onClick={() => remove(a.id)}>{t.yesRemove}</button>
          <button type="button" className="c-btn ghost" onClick={() => setConfirm(false)} autoFocus>{t.cancel}</button>
        </div>
      ) : (
        <div className="alert-actions">
          <button type="button" className="c-btn" onClick={show}>{t.show}</button>
          <button type="button" className="c-btn ghost" onClick={() => setEditing(true)}>{t.edit}</button>
          <button type="button" className="c-btn ghost" onClick={() => setConfirm(true)}>{t.remove}</button>
        </div>
      )}
    </li>
  );
}

/** A new alert (no ``alert``) or an edit of one. */
function AlertForm({ alert, onDone }: { alert?: Alert; onDone?: () => void }) {
  const t = useAlertText();
  const { t: ui, lang } = useT();
  const cats = useCategories().data;
  const { create, edit } = useAlertActions();
  const autoName = useCriteriaName();
  const [c, setC] = useState<Criteria>(alert?.criteria ?? EMPTY);
  const [name, setName] = useState(alert?.name ?? "");
  const [instant, setInstant] = useState(alert?.instant ?? false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const id = useId();
  const patch = (p: Partial<Criteria>) => setC((cur) => ({ ...cur, ...p }));
  const preview = usePreview(c);
  const empty = isEmpty(c);

  // Under one region: its cities (Tel Aviv: its neighbourhoods), as in the home filters.
  const regionKeys = new Set(cats?.regions?.map((r) => r.value));
  const one = c.regions.length === 1 ? c.regions[0] : null;
  // In the order of the names they show (א-ת / A-Z), as in the home filters.
  const regions = byName(cats?.regions, (v) => label(ui.areas, v), lang);
  const subAreas = one === "tel_aviv"
    ? byName((cats?.areas ?? []).filter((a) => !regionKeys.has(a.value) && a.value !== "outside" && a.value !== "abroad"), (v) => label(ui.areas, v), lang)
    : byName((cats?.cities ?? []).filter((x) => one && x.area === one), (v) => v, lang);

  const submit = async () => {
    if (empty) return setNote(t.emptyCriteria);
    setBusy(true);
    try {
      const n = name.trim() || autoName(c);
      if (alert) await edit(alert.id, { criteria: c, name: n, instant });
      else await create(c, n, instant);
      setC(EMPTY); setName(""); setInstant(false); setNote("");
      onDone?.();
    } catch (err) {
      setNote(String(err).includes("409") ? t.tooMany : t.failed);
    } finally {
      setBusy(false);
    }
  };

  return (
    // Not a <form>: the chips and genre picker are plain buttons, which would each submit one.
    <section className="alert-form" aria-labelledby={id}>
      <h2 id={id}>{alert ? t.editTitle : t.newTitle}</h2>

      <div className="alert-step">
        <h3><span className="alert-num" aria-hidden>1</span>{t.what}</h3>
        <p className="alert-hint">{t.whatHint}</p>
        <label className="alert-field">{t.word}
          <input value={c.q} maxLength={100} onChange={(e) => patch({ q: e.target.value })} dir="auto" placeholder={t.wordHint} />
        </label>
        {!!cats?.genres.length && (
          <GenrePicker cats={cats} value={c.genres} onChange={(genres) => patch({ genres })} />
        )}
        {!!regions.length && (
          <Group title={ui.area}>
            <Chip on={!c.regions.length} onClick={() => patch({ regions: [], areas: [], city: null })}>{ui.allAreas}</Chip>
            {regions.map((r) => (
              <Chip key={r.value} on={c.regions.includes(r.value)} onClick={() => patch({ regions: toggle(c.regions, r.value), areas: [], city: null })}>
                {label(ui.areas, r.value)}
              </Chip>
            ))}
          </Group>
        )}
        {one && subAreas.length > 1 && (
          <Group title={one === "tel_aviv" ? ui.neighborhood : ui.city}>
            <Chip on={!c.areas.length && !c.city} onClick={() => patch({ areas: [], city: null })}>{ui.all}</Chip>
            {subAreas.map((a) => one === "tel_aviv" ? (
              <Chip key={a.value} on={c.areas.includes(a.value)} onClick={() => patch({ areas: toggle(c.areas, a.value) })}>{label(ui.areas, a.value)}</Chip>
            ) : (
              <Chip key={a.value} on={c.city === a.value} onClick={() => patch({ city: c.city === a.value ? null : a.value })}>{a.value}</Chip>
            ))}
          </Group>
        )}
        <Group title={t.when}>
          {WHEN.map((w) => <Chip key={w} on={c.when === w} onClick={() => patch({ when: w })}>{w === "all" ? t.all : ui[w]}</Chip>)}
          {/* A date range saved from the home filters: kept until another choice. */}
          {!(WHEN as readonly string[]).includes(c.when) && <Chip on onClick={() => {}}>{whenText(ui, c.when)}</Chip>}
        </Group>
        <Group title={ui.slot}>
          {Object.entries(ui.slots).map(([k, n]) => <Chip key={k} on={c.slots.includes(k)} onClick={() => patch({ slots: toggle(c.slots, k) })}>{n}</Chip>)}
        </Group>
        <Group title={t.price}>
          <Chip className="toggle free" on={c.freeOnly} onClick={() => patch({ freeOnly: !c.freeOnly })}>
            <span className="tick" aria-hidden /> {ui.freeOnly}
          </Chip>
          <Chip className="toggle" on={c.discountOnly} onClick={() => patch({ discountOnly: !c.discountOnly })}>
            <span className="tick" aria-hidden /> {ui.discountOnly}
          </Chip>
        </Group>
        <p className={"alert-preview" + (empty ? "" : " live")} role="status">
          {empty ? t.emptyCriteria : preview.data ? t.now(preview.data.total) : <Loader small />}
        </p>
      </div>

      <div className="alert-step">
        <h3><span className="alert-num" aria-hidden>2</span>{t.name}</h3>
        <label className="alert-field">
          <span className="sr-only">{t.name}</span>
          <input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} dir="auto"
            placeholder={empty ? "" : autoName(c)} aria-describedby={id + "-name"} />
        </label>
        <p className="alert-hint" id={id + "-name"}>{t.nameHint}</p>
      </div>

      <div className="alert-step">
        <h3><span className="alert-num" aria-hidden>3</span>{t.how}</h3>
        <p className="alert-hint">{t.howBell}</p>
        {useAlerts().data?.instant_email && <Switch on={instant} onChange={() => setInstant(!instant)}>{t.instant}</Switch>}
      </div>

      <div className="alert-actions">
        <button type="button" className="c-btn" disabled={empty || busy} onClick={submit}>{alert ? t.saveEdit : t.create}</button>
        {onDone && <button type="button" className="c-btn ghost" onClick={onDone}>{t.cancel}</button>}
      </div>
      {note && <p className="adm-err" role="alert">{note}</p>}
    </section>
  );
}

export default function AlertsPage() {
  const t = useAlertText();
  useTitle(t.title);
  useNoIndex();
  const me = useMe();
  const alerts = useAlerts();
  const items = alerts.data?.items ?? [];
  const max = alerts.data?.max ?? 20;
  const [adding, setAdding] = useState(false);
  return (
    <>
      <Hero title={t.title} />
      <main className="wrap alerts-page">
        <p className="alert-intro">{t.intro}</p>
        {me.isLoading ? <Loader /> : !me.data ? (
          <div className="alert-empty"><p>{t.signedOut}</p><SignInButton next="/alerts" /></div>
        ) : alerts.isLoading ? <Loader /> : items.length ? (
          <>
            <div className="alert-list-head">
              <h2>{t.listTitle} <small>{t.limit(items.length, max)}</small></h2>
              {!adding && items.length < max && <button type="button" className="c-btn" onClick={() => setAdding(true)}>{t.add}</button>}
            </div>
            {adding && <AlertForm onDone={() => setAdding(false)} />}
            {items.length >= max && <p className="alert-note">{t.tooMany}</p>}
            <ul className="alert-list">{items.map((a) => <AlertCard key={a.id} a={a} />)}</ul>
          </>
        ) : (
          <>
            <div className="alert-empty">
              <span className="alert-empty-icon" aria-hidden>🔔</span>
              <h2>{t.noneTitle}</h2>
              <p>{t.none}</p>
            </div>
            <AlertForm />
          </>
        )}
      </main>
    </>
  );
}
