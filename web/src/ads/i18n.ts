// Texts of the on-site sponsor controls (event page 💰, a rail's ✎), Hebrew and English.
import { useT } from "../ui";

const he = {
  title: "💰 קידום ממומן",
  btn: "💰 קידום ממומן",
  editSide: "עריכת הצד: הוספה והסרה של אירועים ממומנים",
  side: "צד",
  right: "ימין",
  left: "שמאל",
  placement: "איפה",
  rails: "פס צד",
  results: "ראשון בתוצאות",
  both: "פס צד + תוצאות",
  until: "עד (לא חובה, ברירת מחדל: עד שהאירוע נגמר)",
  save: "שמירה",
  saved: "נשמר. יופיע באתר בטעינה הבאה",
  remove: "הפסקת הקידום",
  removeAsk: "להפסיק לקדם את האירוע? הצפיות והקליקים שלו נשארים בדוח.",
  live: "מוצג עכשיו",
  notLive: "לא מוצג כרגע (האירוע עבר או שהקמפיין נגמר)",
  off: "הפרסום הממומן כבוי באתר כולו. מדליקים בלשונית ממומן באדמין.",
  inSide: "ממומנים בצד הזה",
  noneInSide: "אין כרגע אירועים ממומנים בצד הזה",
  add: "הוספת אירוע",
  search: "חיפוש אירוע לפי שם, אמן או מקום…",
  none: "לא נמצאו אירועים קרובים",
  report: "דוח צפיות וקליקים",
  error: "משהו השתבש, נסו שוב",
  close: "סגירה",
};

const en: typeof he = {
  title: "💰 Sponsored",
  btn: "💰 Sponsor",
  editSide: "Edit this side: add and remove sponsored events",
  side: "Side",
  right: "Right",
  left: "Left",
  placement: "Where",
  rails: "Side rail",
  results: "First in results",
  both: "Side rail + results",
  until: "Until (optional; default: until the event ends)",
  save: "Save",
  saved: "Saved. Shows on the next page load",
  remove: "Stop sponsoring",
  removeAsk: "Stop sponsoring this event? Its views and clicks stay in the report.",
  live: "Showing now",
  notLive: "Not showing (the event is over or the campaign ended)",
  off: "Sponsored placements are off site-wide. Turn them on in the admin's Sponsored tab.",
  inSide: "Sponsored on this side",
  noneInSide: "Nothing is sponsored on this side",
  add: "Add an event",
  search: "Search an event by title, artist or venue…",
  none: "No upcoming events match",
  report: "Views and clicks report",
  error: "Something went wrong, try again",
  close: "Close",
};

export function useAdText() {
  const { lang } = useT();
  return { a: lang === "he" ? he : en, lang };
}
