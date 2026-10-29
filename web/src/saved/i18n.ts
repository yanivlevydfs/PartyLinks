// Saved-events texts, Hebrew and English (kept beside the feature, not in the site-wide i18n).
import { useT } from "../ui";

const he = {
  save: "שמירת האירוע",
  unsave: "הסרה מהשמורים",
  title: "אירועים שמורים",
  nav: "שמורים",
  empty: "עוד לא שמרתם אירועים. לחצו על הלב באירוע כדי לשמור אותו כאן.",
  device: "השמורים נשמרים במכשיר הזה. התחברו כדי לראות אותם בכל מכשיר.",
  browse: "לכל האירועים",
};

const en: typeof he = {
  save: "Save event",
  unsave: "Remove from saved",
  title: "Saved events",
  nav: "Saved",
  empty: "No saved events yet. Tap the heart on an event to keep it here.",
  device: "Saved on this device. Sign in to see them on every device.",
  browse: "All events",
};

export function useSavedText() {
  const { lang } = useT();
  return lang === "en" ? en : he;
}
