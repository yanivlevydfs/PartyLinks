// The heart on an event: save it, or take it back. Works signed out (kept on this device).
import { useSavedText } from "./i18n";
import { useSaved } from "./useSaved";
import "./saved.css";

export function SaveHeart({ eventId, large = false }: { eventId: string; large?: boolean }) {
  const t = useSavedText();
  const { isSaved, toggle } = useSaved();
  const on = isSaved(eventId);
  return (
    <button
      type="button"
      className={"save-heart" + (on ? " on" : "") + (large ? " large" : "")}
      aria-pressed={on}
      aria-label={on ? t.unsave : t.save}
      title={on ? t.unsave : t.save}
      onClick={(ev) => { ev.preventDefault(); ev.stopPropagation(); void toggle(eventId); }}
    >
      <svg viewBox="0 0 24 24" aria-hidden width="18" height="18">
        <path d="M12 21s-7.5-4.6-10-9.3C.4 8.4 2.3 4.5 6.1 4.5c2.2 0 3.5 1.2 4.4 2.5h3c.9-1.3 2.2-2.5 4.4-2.5 3.8 0 5.7 3.9 4.1 7.2C19.5 16.4 12 21 12 21z"
          transform="translate(0 -.5)" />
      </svg>
    </button>
  );
}
