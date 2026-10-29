// The header's heart: a link to /saved with the number of saved events.
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
        <path d="M12 20.5s-7.5-4.6-10-9.3C.4 7.9 2.3 4 6.1 4c2.2 0 3.5 1.2 4.4 2.5h3C14.4 5.2 15.7 4 17.9 4c3.8 0 5.7 3.9 4.1 7.2-2.5 4.7-10 9.3-10 9.3z" />
      </svg>
      {count > 0 && <b aria-hidden>{count > 99 ? "99+" : count}</b>}
    </Link>
  );
}
