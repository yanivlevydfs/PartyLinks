// Passkey texts, Hebrew and English (kept beside the feature, not in the site-wide i18n).
import { useT } from "../ui";

const he = {
  signIn: "כניסה עם טביעת אצבע / פנים",
  working: "ממתין לאישור במכשיר…",
  title: "כניסה עם טביעת אצבע",
  intro: "הוסיפו את המכשיר הזה, ובפעם הבאה תיכנסו בטביעת אצבע, בזיהוי פנים או בקוד נעילת המסך, בלי Google ובלי סיסמה. טביעת האצבע עצמה לא יוצאת מהמכשיר.",
  add: "הוספת המכשיר הזה",
  added: "נוסף. מעכשיו אפשר להיכנס מהמכשיר הזה בטביעת אצבע.",
  remove: "הסרה",
  removeLabel: (name: string) => `הסרת ${name}`,
  created: "נוסף",
  lastUsed: "שימוש אחרון",
  never: "עוד לא",
  none: "עוד לא נוסף אף מכשיר.",
  already: "המכשיר הזה כבר מחובר לחשבון.",
  unknown: "המפתח הזה כבר לא מחובר לאף חשבון. היכנסו עם Google והוסיפו את המכשיר מחדש בעמוד החשבון.",
  blocked: "החשבון הזה חסום. לפרטים כתבו לנו דרך עמוד צור קשר.",
  failed: "הפעולה לא הצליחה. נסו שוב.",
  unsupported: "הדפדפן הזה לא תומך בכניסה עם טביעת אצבע.",
  noneHere: "לא נמצאה כניסה בטביעת אצבע במכשיר הזה. היכנסו עם Google, ומיד אחר כך נציע להוסיף אותה (או בעמוד החשבון).",
  offerTitle: "להיכנס בפעם הבאה בטביעת אצבע?",
  offerText: "שנייה אחת, ובפעם הבאה נכנסים מהמכשיר הזה בטביעת אצבע, בזיהוי פנים או בקוד המסך, בלי Google.",
  offerYes: "הוספת טביעת אצבע",
  offerNo: "לא עכשיו",
};

const en: typeof he = {
  signIn: "Sign in with fingerprint / face",
  working: "Confirm on your device…",
  title: "Fingerprint sign-in",
  intro: "Add this device, and next time you sign in with your fingerprint, face or screen lock: no Google, no password. Your fingerprint never leaves the device.",
  add: "Add this device",
  added: "Added. You can now sign in from this device with your fingerprint.",
  remove: "Remove",
  removeLabel: (name: string) => `Remove ${name}`,
  created: "Added",
  lastUsed: "Last used",
  never: "Not yet",
  none: "No device added yet.",
  already: "This device is already linked to your account.",
  unknown: "This passkey isn't linked to any account anymore. Continue with Google and add the device again on your account page.",
  blocked: "This account is blocked. Write to us through the contact page.",
  failed: "That didn't work. Try again.",
  unsupported: "This browser doesn't support fingerprint sign-in.",
  noneHere: "No fingerprint sign-in on this device yet. Continue with Google and we'll offer to add it right after (or on your account page).",
  offerTitle: "Sign in with your fingerprint next time?",
  offerText: "One second, and next time this device signs you in with your fingerprint, face or screen lock: no Google.",
  offerYes: "Add fingerprint",
  offerNo: "Not now",
};

export function usePasskeyText() {
  const { lang } = useT();
  return lang === "en" ? en : he;
}
