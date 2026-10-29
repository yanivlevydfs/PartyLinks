// The header's saved events: "Saved" with the count, linking to /saved. Words, not a second bell: the
// header's bell is alert news, and the 🔔 on an event is what saves it.
import { Link } from "react-router-dom";
import { useSavedText } from "./i18n";
import { useMergeOnSignIn, useSaved } from "./useSaved";
import "./saved.css";

export function SavedLink() {
  const t = useSavedText();
  const { count } = useSaved();
  useMergeOnSignIn();
  return (
    <Link className="saved-link" to="/saved" aria-label={count ? `${t.title} (${count})` : t.title}>
      <span>{t.nav}</span>
      {count > 0 && <b aria-hidden>{count > 99 ? "99+" : count}</b>}
    </Link>
  );
}
