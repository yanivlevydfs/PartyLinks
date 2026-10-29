// The header's saved events: "Saved" with the count, linking to /saved. Words, not a second bell: the
// header's bell is alert news, and the 🔔 on an event is what saves it. Signed in, the link lives in the
// avatar menu (SavedMenuItem) and the header keeps only the toast; a guest sees it only with events
// saved on this device (saving asks to sign in), so the phone header fits.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { send, useMe } from "../accounts/useMe";
import { useSavedText } from "./i18n";
import { SAVE_EVENT } from "./SaveButton";
import { useMergeOnSignIn, useSaved } from "./useSaved";

/** Back from Google with ?save_event=<id>: save the event the visitor pressed before signing in. */
function usePendingSave(done: (ok: boolean) => void) {
  const me = useMe().data;
  const qc = useQueryClient();
  useEffect(() => {
    const url = new URL(location.href);
    const id = url.searchParams.get(SAVE_EVENT);
    if (!me || !id) return;
    url.searchParams.delete(SAVE_EVENT);
    history.replaceState(history.state, "", url.pathname + url.search + url.hash);
    send(`/api/me/saved/${encodeURIComponent(id)}`, "PUT")
      .then(() => { done(true); return qc.invalidateQueries({ queryKey: ["saved"] }); })
      .catch(() => done(false));
  }, [me?.id]);
}
import "./saved.css";

/** "Saved events" with the count, a row of the avatar menu. */
export function SavedMenuItem({ onClick }: { onClick: () => void }) {
  const t = useSavedText();
  const { count } = useSaved();
  return (
    <Link role="menuitem" className="saved-item" to="/saved" onClick={onClick}>
      <span>{t.title}</span>
      {count > 0 && <b aria-hidden>{count > 99 ? "99+" : count}</b>}
    </Link>
  );
}

export function SavedLink() {
  const t = useSavedText();
  const me = useMe().data;
  const { count } = useSaved();
  useMergeOnSignIn();
  const [toast, setToast] = useState<boolean | null>(null);
  usePendingSave(setToast);
  useEffect(() => {
    if (toast === null) return;
    const hide = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(hide);
  }, [toast]);
  return (
    <span className="saved-wrap">
      {!me && count > 0 && <Link className="saved-link" to="/saved" aria-label={count ? `${t.title} (${count})` : t.title}>
        <span>{t.nav}</span>
        {count > 0 && <b aria-hidden>{count > 99 ? "99+" : count}</b>}
      </Link>}
      {toast !== null && <span className={"saved-toast" + (toast ? "" : " bad")} role="status">{toast ? t.savedToast : t.failedToast}</span>}
    </span>
  );
}
