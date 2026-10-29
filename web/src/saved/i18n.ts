// Saved-events texts, Hebrew and English (kept beside the feature, not in the site-wide i18n).
import { useT } from "../ui";

const he = {
  save: "שמירת האירוע",
  unsave: "הסרה מהשמורים",
  title: "אירועים שמורים",
  nav: "שמורים",
  empty: "עוד לא שמרתם אירועים. לחצו על הפעמון באירוע כדי לשמור אותו כאן.",
  device: "התחברו כדי לשמור אירועים בפעמון ולראות אותם בכל מכשיר.",
  browse: "לכל האירועים",
  signInTitle: "שמירת אירועים 🔔",
  signInText: "התחברו עם Google כדי לשמור את האירוע ולראות את השמורים שלכם בכל מכשיר. האירוע יישמר מיד כשתחזרו.",
  savedToast: "✓ האירוע נשמר",
  failedToast: "האירוע לא נשמר, נסו שוב",
};

const en: typeof he = {
  save: "Save event",
  unsave: "Remove from saved",
  title: "Saved events",
  nav: "Saved",
  empty: "No saved events yet. Tap the bell on an event to keep it here.",
  device: "Sign in to save events with the bell and see them on every device.",
  browse: "All events",
  signInTitle: "Save events 🔔",
  signInText: "Sign in with Google to save this event and see your saved events on every device. It's saved as soon as you're back.",
  savedToast: "✓ Event saved",
  failedToast: "Not saved, try again",
};

export function useSavedText() {
  const { lang } = useT();
  return lang === "en" ? en : he;
}
