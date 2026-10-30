// "Save as alert": the current filters (or the quiz's answers, or an artist / venue) as an alert.
// Signed in: a click asks first (what will be saved, that matches land in the bell), then Saved · Manage · Undo.
// Signed out: the sign-in popup; back from Google, ?save_alert=<the criteria as JSON> saves it (Bell), once and fresh only.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { SignInSheet } from "../accounts/SignInSheet";
import { useAccountsOn, useMe } from "../accounts/useMe";
import type { Filters } from "../store";
import { useAlertText } from "./i18n";
import { useT } from "../ui";
import { criteriaWords, isEmpty, toCriteria, useAlertActions, useAlerts, useCriteriaName, type Criteria } from "./useAlerts";
import "./alerts.css";

export const SAVE_PARAM = "save_alert";
export const EMPTY: Criteria = {
  q: "", genres: [], regions: [], areas: [], city: null, slots: [], freeOnly: false, discountOnly: false, when: "all",
};

/** Where to come back after Google: this page, with the alert itself in the address (the page's filters
 *  don't survive the trip to Google and back), saved on arrival (Bell). */
function nextUrl(criteria: Criteria) {
  const url = new URL(location.href);
  url.searchParams.set(SAVE_PARAM, JSON.stringify(criteria));
  return url.pathname + url.search;
}

/** The alert a visitor asked for before signing in, from the address (null: none, or not one of ours). */
export function pendingCriteria(raw: string | null): Criteria | null {
  if (!raw) return null;
  try {
    const c = JSON.parse(raw);
    return c && typeof c === "object" ? { ...EMPTY, ...c } : null;
  } catch {
    return null;
  }
}

/** Set when the sign-in sheet's Google link is clicked: the return URL's ?save_alert= is honoured only with it,
 *  once, and only for a short while (a replayed / bookmarked / autocompleted URL saves nothing). */
const MARK = "pl-save-alert";
const MARK_TTL = 15 * 60_000;

function markPending(criteria: Criteria) {
  try { sessionStorage.setItem(MARK, JSON.stringify({ at: Date.now(), c: JSON.stringify(criteria) })); } catch { /* storage off */ }
}

/** Whether ?save_alert=<raw> is the one this tab just asked for; consumes the marker either way. */
export function takePending(raw: string): boolean {
  try {
    const mark = JSON.parse(sessionStorage.getItem(MARK) ?? "null");
    sessionStorage.removeItem(MARK);
    return !!mark && Date.now() - mark.at < MARK_TTL && mark.c === raw;
  } catch {
    return false;
  }
}

type State = { k: "idle" | "confirm" | "saving" | "failed" | "full" } | { k: "saved"; id: number | null }; // id null: no Undo

export function SaveAlertButton({ filters, q, label }: { filters?: Filters; q?: string; label?: string }) {
  const t = useAlertText();
  const { t: ui, lang } = useT();
  const on = useAccountsOn();
  const me = useMe().data;
  const { create, remove } = useAlertActions();
  const name = useCriteriaName();
  const mine = useAlerts().data?.items;
  const [state, setState] = useState<State>({ k: "idle" });
  const [ask, setAsk] = useState(false);
  const criteria: Criteria | null = q ? { ...EMPTY, q } : filters ? toCriteria(filters) : null;
  const key = JSON.stringify(criteria);
  useEffect(() => setState({ k: "idle" }), [key]); // other filters: a new search to save
  if (!on || !criteria || isEmpty(criteria)) return null;

  const open = () => (me ? setState({ k: "confirm" }) : setAsk(true));
  const save = async () => {
    setState({ k: "saving" });
    const had = mine?.map((a) => a.id);
    try {
      const made = await create(criteria, name(criteria));
      // The same search saved twice comes back as the old alert: Undo must not delete that one.
      setState({ k: "saved", id: had && !had.includes(made!.id) ? made!.id : null });
    } catch (e) {
      setState({ k: String(e).includes("409") ? "full" : "failed" });
    }
  };
  const undo = async (id: number) => {
    setState({ k: "idle" });
    await remove(id).catch(() => setState({ k: "saved", id }));
  };
  return (
    <>
      {state.k === "saved" ? (
        <span className="alert-done" role="status">
          <Link className="alert-save done" to="/alerts">{t.savedManage}</Link>
          {state.id !== null && <button type="button" className="link-btn" onClick={() => undo(state.id!)}>{t.undo}</button>}
        </span>
      ) : state.k === "confirm" || state.k === "saving" ? (
        <span className="alert-confirm" role="group" aria-label={t.confirmTitle}>
          <span>
            <b>{t.confirmTitle}</b> <span dir="auto">{criteriaWords(criteria, ui, lang).join(" · ")}</span>
            <small>{t.confirmText}</small>
          </span>
          <span className="alert-confirm-btns">
            <button type="button" className="alert-save" onClick={save} disabled={state.k === "saving"} autoFocus>
              {state.k === "saving" ? t.saving : t.create}
            </button>
            <button type="button" className="link-btn" onClick={() => setState({ k: "idle" })}>{t.cancel}</button>
          </span>
        </span>
      ) : (
        <button type="button" className="alert-save" onClick={open}>
          {state.k === "failed" ? t.failed : state.k === "full" ? t.tooMany : label ?? t.save}
        </button>
      )}
      {ask && (
        <span onClickCapture={(e) => { if ((e.target as Element).closest("a")) markPending(criteria); }}>
          <SignInSheet title={t.signInTitle} text={t.signInText} next={nextUrl(criteria)} onClose={() => setAsk(false)} />
        </span>
      )}
    </>
  );
}
