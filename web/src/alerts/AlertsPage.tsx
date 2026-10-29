// /alerts: the user's saved searches. Each one: its filters, new matches, "email me at once", pause,
// see its events, delete. A new one from the same pickers as the home filters.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { SignInButton } from "../accounts/AccountSlot";
import { useNoIndex } from "../accounts/AccountPage";
import { useMe } from "../accounts/useMe";
import { useCategories } from "../api";
import { GenrePicker, toggle } from "../Filters";
import { DEFAULT_FILTERS, useStore } from "../store";
import { Chip, Hero, label, Loader, useT, useTitle } from "../ui";
import { useAlertText } from "./i18n";
import { EMPTY } from "./SaveAlertButton";
import { criteriaWords, isEmpty, useAlertActions, useAlerts, useCriteriaName, usePreview, type Alert, type Criteria } from "./useAlerts";
import "./alerts.css";

function AlertRow({ a }: { a: Alert }) {
  const t = useAlertText();
  const { t: ui, lang } = useT();
  const { edit, remove } = useAlertActions();
  const navigate = useNavigate();
  const show = () => {
    const c = a.criteria;
    useStore.getState().setFilters({ ...DEFAULT_FILTERS, ...c, shops: [], hideSoldOut: false, sort: "start_at" });
    useStore.getState().setQuizDone(true);
    navigate("/");
  };
  return (
    <li className={"alert-row" + (a.active ? "" : " off")}>
      <div className="alert-head">
        <b dir="auto">{a.name}</b>
        {a.unseen > 0 && <span className="alert-new">{t.unseen(a.unseen)}</span>}
      </div>
      <div className="alert-tags">{criteriaWords(a.criteria, ui, lang).map((w) => <span key={w} className="tag" dir="auto">{w}</span>)}</div>
      <label className="alert-check">
        <input type="checkbox" checked={a.instant} onChange={() => edit(a.id, { instant: !a.instant })} /> {t.instant}
      </label>
      <div className="alert-actions">
        <button className="c-btn ghost" onClick={show}>{t.show}</button>
        <button className="c-btn ghost" onClick={() => edit(a.id, { active: !a.active })} aria-pressed={a.active}>
          {a.active ? t.active : t.paused}
        </button>
        <button className="c-btn ghost" onClick={() => remove(a.id)}>{t.remove}</button>
      </div>
    </li>
  );
}

function NewAlert({ full }: { full: boolean }) {
  const t = useAlertText();
  const { t: ui } = useT();
  const cats = useCategories().data;
  const { create } = useAlertActions();
  const name = useCriteriaName();
  const [c, setC] = useState<Criteria>(EMPTY);
  const [instant, setInstant] = useState(false);
  const [note, setNote] = useState("");
  const patch = (p: Partial<Criteria>) => setC((cur) => ({ ...cur, ...p }));
  const preview = usePreview(c);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isEmpty(c)) return setNote(t.emptyCriteria);
    try {
      await create(c, name(c), instant);
      setC(EMPTY);
      setInstant(false);
      setNote("");
    } catch (err) {
      setNote(String(err).includes("409") ? t.tooMany : t.failed);
    }
  };
  if (full) return <p className="muted">{t.tooMany}</p>;
  return (
    <form className="alert-new-form" onSubmit={submit}>
      <h2>{t.newTitle}</h2>
      <label className="alert-word">{t.word}
        <input value={c.q} maxLength={100} onChange={(e) => patch({ q: e.target.value })} dir="auto" />
      </label>
      <div className="alert-chips">
        {(["all", "weekend", "week", "month"] as const).map((w) => (
          <Chip key={w} on={c.when === w} onClick={() => patch({ when: w })}>{w === "all" ? t.all : ui[w]}</Chip>
        ))}
      </div>
      {!!cats?.regions?.length && (
        <div className="alert-chips">
          {cats.regions.map((r) => (
            <Chip key={r.value} on={c.regions.includes(r.value)} onClick={() => patch({ regions: toggle(c.regions, r.value), areas: [], city: null })}>
              {label(ui.areas, r.value)}
            </Chip>
          ))}
        </div>
      )}
      {!!cats?.genres.length && <GenrePicker cats={cats} value={c.genres} onChange={(genres) => patch({ genres })} />}
      <div className="alert-chips">
        {Object.entries(ui.slots).map(([k, n]) => (
          <Chip key={k} on={c.slots.includes(k)} onClick={() => patch({ slots: toggle(c.slots, k) })}>{n}</Chip>
        ))}
        <Chip className="toggle free" on={c.freeOnly} onClick={() => patch({ freeOnly: !c.freeOnly })}>
          <span className="tick" aria-hidden /> {ui.freeOnly}
        </Chip>
      </div>
      <label className="alert-check">
        <input type="checkbox" checked={instant} onChange={() => setInstant(!instant)} /> {t.instant}
      </label>
      <p className="muted" role="status">
        {isEmpty(c) ? t.emptyCriteria : preview.data ? t.now(preview.data.total) : <Loader small />}
      </p>
      <button className="c-btn" disabled={isEmpty(c)}>{t.create}</button>
      {note && <p className="adm-err" role="alert">{note}</p>}
    </form>
  );
}

export default function AlertsPage() {
  const t = useAlertText();
  useTitle(t.title);
  useNoIndex();
  const me = useMe();
  const alerts = useAlerts();
  const items = alerts.data?.items ?? [];
  return (
    <>
      <Hero title={t.title} />
      <main className="wrap statement">
        <p>{t.intro}</p>
        {me.isLoading ? <Loader /> : !me.data ? <SignInButton next="/alerts" /> : alerts.isLoading ? <Loader /> : (
          <>
            {items.length ? (
              <>
                <p className="muted">{t.limit(items.length, alerts.data!.max)}</p>
                <ul className="alert-list">{items.map((a) => <AlertRow key={a.id} a={a} />)}</ul>
              </>
            ) : <p className="muted">{t.none}</p>}
            <NewAlert full={items.length >= (alerts.data?.max ?? 20)} />
          </>
        )}
      </main>
    </>
  );
}
