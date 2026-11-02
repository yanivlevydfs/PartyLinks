// The heart on an event: save it, or take it back. A signed-out visitor gets the sign-in popup, and the
// event is saved when they come back from Google (?save_event=<id>, handled by the header's SavedLink).
import { useState } from "react";
import { SignInSheet, returnTo } from "../accounts/SignInSheet";
import { useAccountsOn, useMe } from "../accounts/useMe";
import { useSavedText } from "./i18n";
import { useSaved } from "./useSaved";
import "./saved.css";

export const SAVE_EVENT = "save_event";

export function SaveButton({ eventId, large = false }: { eventId: string; large?: boolean }) {
  const t = useSavedText();
  const on = useAccountsOn();
  const me = useMe();
  const { isSaved, toggle } = useSaved();
  const [ask, setAsk] = useState(false);
  const saved = isSaved(eventId);
  const press = () => {
    if (on && !me.data && !me.isLoading) return setAsk(true);
    void toggle(eventId); // accounts off (localhost without Google): kept on the device
  };
  return (
    <>
      <button
        type="button"
        className={"save-btn" + (saved ? " on" : "") + (large ? " large" : "")}
        aria-pressed={saved}
        aria-label={saved ? t.unsave : t.save}
        title={saved ? t.unsave : t.save}
        onClick={(ev) => { ev.preventDefault(); ev.stopPropagation(); press(); }}
      >
        <svg viewBox="0 0 24 24" aria-hidden width="18" height="18">
          <path d="M12 20s-7.5-4.6-9.3-9.1C1.5 7.8 3.6 4.5 7 4.5c2 0 3.5 1.1 5 3 1.5-1.9 3-3 5-3 3.4 0 5.5 3.3 4.3 6.4C19.5 15.4 12 20 12 20z" />
        </svg>
      </button>
      {ask && <SignInSheet title={t.signInTitle} text={t.signInText} next={returnTo(SAVE_EVENT, eventId)} onClose={() => setAsk(false)} />}
    </>
  );
}
