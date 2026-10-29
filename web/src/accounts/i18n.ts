// Account texts, Hebrew and English (kept beside the feature, not in the site-wide i18n).
import { useT } from "../ui";

const he = {
  google: "המשך עם Google",
  signIn: "כניסה",
  menu: "החשבון שלי",
  account: "החשבון שלי",
  saved: "אירועים שמורים",
  alerts: "ההתראות שלי",
  admin: "ניהול",
  signOut: "יציאה",
  name: "שם",
  email: "דוא״ל",
  emailLang: "שפת המיילים",
  save: "שמירה",
  savedOk: "נשמר",
  deleteTitle: "מחיקת החשבון",
  deleteText: "החשבון, האירועים השמורים וההתראות יימחקו מיד ולצמיתות. האתר ימשיך לעבוד לך כרגיל, בלי חשבון.",
  deleteType: "כדי לאשר, כתבו: מחק",
  deleteWord: "מחק",
  deleteButton: "מחיקת החשבון לצמיתות",
  why: "החשבון לא חובה: כל האתר פתוח בלי הרשמה. עם חשבון אפשר לשמור אירועים בכל מכשיר, לקבל התראות על מסיבות שמתאימות לך ועדכון שבועי.",
  welcomeTitle: (name: string) => `ברוכים הבאים, ${name}!`,
  welcomeText: "מעכשיו אפשר לשמור אירועים ולקבל התראות על מסיבות שמתאימות לך.",
  close: "סגירה",
  failed: "משהו השתבש, נסו שוב",
  signin: {
    failed: "הכניסה עם Google בוטלה או נכשלה.",
    unverified: "חשבון ה-Google לא אומת. נסו חשבון אחר.",
    blocked: "החשבון הזה חסום. לפרטים כתבו לנו דרך עמוד צור קשר.",
    taken: "כתובת הדוא״ל הזו כבר מחוברת לחשבון אחר. כתבו לנו דרך עמוד צור קשר.",
  } as Record<string, string>,
};

const en: typeof he = {
  google: "Continue with Google",
  signIn: "Sign in",
  menu: "My account",
  account: "My account",
  saved: "Saved events",
  alerts: "My alerts",
  admin: "Admin",
  signOut: "Sign out",
  name: "Name",
  email: "Email",
  emailLang: "Email language",
  save: "Save",
  savedOk: "Saved",
  deleteTitle: "Delete account",
  deleteText: "Your account, saved events and alerts are deleted at once and for good. The site keeps working for you as before, without an account.",
  deleteType: "To confirm, type: delete",
  deleteWord: "delete",
  deleteButton: "Delete my account for good",
  why: "An account is optional: the whole site is open without signing up. With one you can keep saved events on every device, get alerts for parties that suit you and a weekly update.",
  welcomeTitle: (name) => `Welcome, ${name}!`,
  welcomeText: "You can now save events and get alerts for parties that suit you.",
  close: "Close",
  failed: "Something went wrong, try again",
  signin: {
    failed: "Signing in with Google was cancelled or failed.",
    unverified: "That Google account isn't verified. Try another one.",
    blocked: "This account is blocked. Write to us through the contact page.",
    taken: "That email is already linked to another account. Write to us through the contact page.",
  },
};

export function useAccountText() {
  const { lang } = useT();
  return lang === "en" ? en : he;
}
