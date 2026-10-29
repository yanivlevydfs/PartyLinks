// The header's saved events: a bell with a tick (the plain bell beside it is alert news), linking to /saved
// with the number of saved events.
import { Link } from "react-router-dom";
import { useSavedText } from "./i18n";
import { useMergeOnSignIn, useSaved } from "./useSaved";
import "./saved.css";

export function SavedLink() {
  const t = useSavedText();
  const { count } = useSaved();
  useMergeOnSignIn();
  return (
    <Link className="saved-link" to="/saved" aria-label={count ? `${t.title} (${count})` : t.title} title={t.title}>
      <svg viewBox="0 0 24 24" aria-hidden width="18" height="18">
        <path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15L6 16zM10 20a2 2 0 0 0 4 0" /><path d="M9.6 10.8l1.7 1.7 3.1-3.3" />
      </svg>
      {count > 0 && <b aria-hidden>{count > 99 ? "99+" : count}</b>}
    </Link>
  );
}
