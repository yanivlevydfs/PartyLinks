// "Save as alert": the current filters (or the quiz's answers, or an artist / venue) as an alert.
// Signed out: a small sheet with "Continue with Google"; back from Google, ?save_alert=1 saves it (Bell).
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { SignInButton } from "../accounts/AccountSlot";
import { useAccountsOn, useMe } from "../accounts/useMe";
import type { Filters } from "../store";
import { useAlertText } from "./i18n";
import { isEmpty, toCriteria, useAlertActions, useCriteriaName, type Criteria } from "./useAlerts";
import "./alerts.css";

export const SAVE_PARAM = "save_alert";
export const EMPTY: Criteria = {
  q: "", genres: [], regions: [], areas: [], city: null, slots: [], freeOnly: false, discountOnly: false, when: "all",
};

/** Where to come back after Google: this page, saving the alert on arrival. ``q`` for an artist / venue alert. */
function nextUrl(q?: string) {
  const url = new URL(location.href);
  url.searchParams.set(SAVE_PARAM, q ? `q:${q}` : "1");
  return url.pathname + url.search;
}

export function SaveAlertButton({ filters, q, label }: { filters?: Filters; q?: string; label?: string }) {
  const t = useAlertText();
  const on = useAccountsOn();
  const me = useMe().data;
  const { create } = useAlertActions();
  const name = useCriteriaName();
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed" | "full">("idle");
  const [ask, setAsk] = useState(false);
  const criteria: Criteria | null = q ? { ...EMPTY, q } : filters ? toCriteria(filters) : null;
  const key = JSON.stringify(criteria);
  useEffect(() => setState("idle"), [key]); // other filters: a new search to save
  if (!on || !criteria || isEmpty(criteria)) return null;

  const save = async () => {
    if (!me) return setAsk(true);
    setState("saving");
    try {
      await create(criteria, name(criteria));
      setState("saved");
    } catch (e) {
      setState(String(e).includes("409") ? "full" : "failed");
    }
  };
  return (
    <>
      {state === "saved" ? (
        <Link className="alert-save done" to="/alerts">{t.saved}</Link>
      ) : (
        <button type="button" className="alert-save" onClick={save} disabled={state === "saving"}>
          {state === "saving" ? t.saving : state === "failed" ? t.failed : state === "full" ? t.tooMany : label ?? t.save}
        </button>
      )}
      {ask && (
        <div className="sheet-back" onClick={() => setAsk(false)}>
          <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="alert-signin" onClick={(e) => e.stopPropagation()}>
            <h2 id="alert-signin">{t.signInTitle}</h2>
            <p>{t.signInText}</p>
            <SignInButton next={nextUrl(q)} />
          </div>
        </div>
      )}
    </>
  );
}
