// The header bell (signed in only): new matches of the user's alerts. Opening it marks them seen.
// Back from Google with ?save_alert=..., it also saves the alert the visitor asked for before signing in.
// On phones the bell button hides and the avatar menu's BellMenuItem opens the same panel.
import { useEffect, useRef, useState } from "react";
import { create } from "zustand";
import { Link } from "react-router-dom";
import { send, useMe } from "../accounts/useMe";
import { eventPath, Img, sized, useT, when } from "../ui";
import { useAlertText } from "./i18n";
import { pendingCriteria, SAVE_PARAM } from "./SaveAlertButton";
import { isEmpty, useAlertActions, useBell, useCriteriaName } from "./useAlerts";
import "./alerts.css";

/** Whether the bell's panel is open: shared, so the avatar menu's row can open it. */
const usePanel = create<{ open: boolean; setOpen: (open: boolean) => void }>((set) => ({ open: false, setOpen: (open) => set({ open }) }));

/** Opens or closes the panel; opening marks the news seen. */
function useToggle() {
  const { open, setOpen } = usePanel();
  const bell = useBell();
  return () => {
    setOpen(!open);
    if (!open && bell.data?.unseen) send("/api/me/bell/seen", "POST").then(() => bell.refetch()).catch(() => {});
  };
}

/** The avatar menu's "Alerts" row with the unseen count (phones, where the header has no bell button). */
export function BellMenuItem({ onClick }: { onClick: () => void }) {
  const t = useAlertText();
  const unseen = useBell().data?.unseen ?? 0;
  const toggle = useToggle();
  return (
    <button role="menuitem" className="menu-phone menu-count" onClick={() => { onClick(); toggle(); }}>
      <span>🔔 {t.bell}</span>
      {unseen > 0 && <b aria-hidden>{unseen > 99 ? "99+" : unseen}</b>}
    </button>
  );
}

/** Back from Google with ?save_alert=...: save the alert the visitor asked for, and say whether it worked. */
function usePendingAlert(setSaved: (ok: boolean) => void) {
  const me = useMe().data;
  const { create } = useAlertActions();
  const name = useCriteriaName();
  useEffect(() => {
    const url = new URL(location.href);
    const pending = url.searchParams.get(SAVE_PARAM);
    if (!me || !pending) return;
    url.searchParams.delete(SAVE_PARAM);
    history.replaceState(history.state, "", url.pathname + url.search + url.hash);
    const criteria = pendingCriteria(pending);
    if (criteria && !isEmpty(criteria)) create(criteria, name(criteria)).then(() => setSaved(true)).catch(() => setSaved(false));
  }, [me?.id]);
}

export function Bell() {
  const t = useAlertText();
  const { t: ui } = useT();
  const me = useMe().data;
  const bell = useBell();
  const { clear } = useAlertActions();
  const { open, setOpen } = usePanel();
  const toggle = useToggle();
  const box = useRef<HTMLDivElement>(null);
  const [saved, setSaved] = useState<boolean | null>(null);
  usePendingAlert(setSaved);
  useEffect(() => {
    if (saved === null) return;
    const done = setTimeout(() => setSaved(null), 6000);
    return () => clearTimeout(done);
  }, [saved]);
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  if (!me) return null;
  const unseen = bell.data?.unseen ?? 0;
  /** Remove one notification, or all of them (no id). */
  const remove = (eventId?: string) => { clear(eventId).catch(() => {}); };
  return (
    <div className="bell" ref={box}>
      <button className="bell-btn" aria-haspopup="true" aria-expanded={open} onClick={toggle}
        aria-label={unseen ? `${t.bell}: ${t.unseen(unseen)}` : t.bell}>
        <svg viewBox="0 0 24 24" aria-hidden width="18" height="18">
          <path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15L6 16zM10 20a2 2 0 0 0 4 0" />
        </svg>
        {unseen > 0 && <b aria-hidden>{unseen > 99 ? "99+" : unseen}</b>}
      </button>
      <span className="sr-only" aria-live="polite">{unseen ? t.unseen(unseen) : ""}</span>
      {saved !== null && <p className="bell-toast" role="status">{saved ? t.saved : t.failed}</p>}
      {open && (
        <div className="bell-pop">
          <div className="bell-head">
            <b className="bell-title">{t.bell}</b>
            {!!bell.data?.items.length && <button type="button" className="link-btn" onClick={() => remove()}>{t.clearAll}</button>}
          </div>
          {!bell.data?.items.length ? (
            <p className="muted">{t.bellEmpty}</p>
          ) : (
            <ul>
              {bell.data.items.map((h) => (
                <li key={h.event.id} className={h.seen ? "" : "new"}>
                  <Link to={eventPath(h.event)} onClick={() => setOpen(false)}>
                    {/* No picture: a small placeholder, not the cards' title-as-artwork (unreadable at this size). */}
                    {h.event.cover_url ?? h.event.image_url
                      ? <Img src={sized(h.event.cover_url ?? h.event.image_url, 640)} alt="" className="bell-img" />
                      : <span className="bell-img bell-noimg" aria-hidden>🎉</span>}
                    <span>
                      <b dir="auto">{h.event.title}</b>
                      <small>{when(h.event, ui, { weekday: "short", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" })}</small>
                      <small className="bell-why" dir="auto">🔔 {h.alert}</small>
                    </span>
                  </Link>
                  <button type="button" className="bell-x" aria-label={`${t.remove}: ${h.event.title}`} title={t.remove}
                    onClick={() => remove(h.event.id)}>✕</button>
                </li>
              ))}
            </ul>
          )}
          <Link className="bell-all" to="/alerts" onClick={() => setOpen(false)}>{t.bellAll}</Link>
        </div>
      )}
    </div>
  );
}
