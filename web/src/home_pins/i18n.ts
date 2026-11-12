// Pin texts, Hebrew and English (kept beside the feature, not in the site-wide i18n).
import { useT } from "../ui";

const he = {
  tab: "📌 נעיצה בדף הבית",
  search: "חיפוש אירוע לפי שם, אמן או מקום…",
  none: "לא נמצאו אירועים קרובים",
  rows: "באילו שורות להציג אותו ראשון?",
  auto: "בלי בחירה: ב🔥 חם עכשיו ובכל שורה שהוא מתאים לה מעצמו",
  natural: "✓ = מתאים לשורה מעצמו",
  until: "עד (לא חובה, ברירת מחדל: עד שהאירוע נגמר)",
  note: "הערה",
  save: "שמירה",
  saved: "נשמר. יופיע בדף הבית תוך דקה",
  unpin: "הסרת הנעיצה",
  change: "אירוע אחר",
  error: "משהו השתבש, נסו שוב",
  pinned: "נעוצים עכשיו",
  empty: "אין עדיין אירועים נעוצים",
  shows: "מוצג ב:",
  hidden: "לא מוצג כרגע",
  edit: "עריכה",
  up: "למעלה",
  down: "למטה",
  close: "סגירה",
  pinBtn: "📌 נעיצה בדף הבית",
  addBtn: "➕ הוסף אירוע",
  holiday: "✡️ שורת החג",
};

const en: typeof he = {
  tab: "📌 Home page pins",
  search: "Search an event by title, artist or venue…",
  none: "No upcoming events match",
  rows: "Which rows should show it first?",
  auto: "None chosen: 🔥 Trending and every row it matches by itself",
  natural: "✓ = matches the row by itself",
  until: "Until (optional; default: until the event ends)",
  note: "Note",
  save: "Save",
  saved: "Saved. It shows on the home page within a minute",
  unpin: "Unpin",
  change: "Another event",
  error: "Something went wrong, try again",
  pinned: "Pinned now",
  empty: "Nothing pinned yet",
  shows: "Shows in:",
  hidden: "Not showing now",
  edit: "Edit",
  up: "Up",
  down: "Down",
  close: "Close",
  pinBtn: "📌 Pin to the home page",
  addBtn: "➕ Add an event",
  holiday: "✡️ Holiday row",
};

export function usePinText() {
  const { t, lang } = useT();
  const p = lang === "he" ? he : en;
  /** Every home row a pin may choose, in screen order, with its title. */
  const rows: [string, string][] = [["trending", t.trending], ...Object.entries(t.rails).map(([k, v]) => [k, v[0]] as [string, string]), ["holiday", p.holiday]];
  return { p, rows, lang, locale: t.locale };
}
