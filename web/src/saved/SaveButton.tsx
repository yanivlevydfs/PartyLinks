// The bell on an event: save it, or take it back. Works signed out (kept on this device).
import { useSavedText } from "./i18n";
import { useSaved } from "./useSaved";
import "./saved.css";

export function SaveButton({ eventId, large = false }: { eventId: string; large?: boolean }) {
  const t = useSavedText();
  const { isSaved, toggle } = useSaved();
  const on = isSaved(eventId);
  return (
    <button
      type="button"
      className={"save-btn" + (on ? " on" : "") + (large ? " large" : "")}
      aria-pressed={on}
      aria-label={on ? t.unsave : t.save}
      title={on ? t.unsave : t.save}
      onClick={(ev) => { ev.preventDefault(); ev.stopPropagation(); void toggle(eventId); }}
    >
      <svg viewBox="0 0 24 24" aria-hidden width="18" height="18">
        <path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15L6 16zM10 20a2 2 0 0 0 4 0" />
      </svg>
    </button>
  );
}
