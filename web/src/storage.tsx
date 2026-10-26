// What the site stores on the device and which third parties the browser contacts, shown on the
// privacy page (/privacy) per the Israeli Privacy Protection Law, 1981 (Amendment 13). There is no
// consent banner and no consent record: everything listed is needed for the site to work, and the
// site sets no advertising or analytics cookies. Keep STORAGE in sync with the code.
import { useT } from "./ui";

// --------------------------------------------------------------------------- what we store / send
type Text = { he: string; en: string };
export interface StorageItem {
  name: string;
  provider: Text;
  kind: Text;
  purpose: Text;
  duration: Text;
}

const US = { he: "האתר (צד ראשון)", en: "This site (first party)" };
const LOCAL = { he: "אחסון מקומי (localStorage)", en: "Local storage (localStorage)" };
const REQUEST = { he: "בקשת רשת לצד שלישי", en: "Request to a third party" };
const NONE = { he: "לא נשמר במכשיר", en: "Nothing stored on the device" };

export const STORAGE: StorageItem[] = [
  {
    name: "partylinks", provider: US, kind: LOCAL,
    purpose: { he: "שמירת שפת התצוגה שבחרת (עברית / אנגלית).", en: "Remembers the display language you chose (Hebrew / English)." },
    duration: { he: "עד שתמחקו את נתוני האתר בדפדפן", en: "Until you clear the site's data in your browser" },
  },
  {
    name: "asw-user-settings", provider: US,
    kind: { he: "אחסון מקומי (או עוגייה, אם האחסון חסום)", en: "Local storage (or a cookie if storage is blocked)" },
    purpose: { he: "שמירת הגדרות תפריט הנגישות שבחרת (גודל טקסט, ניגודיות וכו').", en: "Remembers your accessibility-menu settings (text size, contrast, etc.)." },
    duration: { he: "עד שתמחקו את נתוני האתר בדפדפן", en: "Until you clear the site's data in your browser" },
  },
  {
    name: "partylinks-install-dismissed", provider: US, kind: LOCAL,
    purpose: { he: "זמן הסגירה של באנר ההתקנה למסך הבית, כדי שלא יוצג שוב במשך 14 יום.", en: "When you closed the add-to-home-screen banner, so it stays hidden for 14 days." },
    duration: { he: "14 יום", en: "14 days" },
  },
  {
    name: "partylinks-admin-token", provider: US,
    kind: { he: "אחסון לסשן (sessionStorage)", en: "Session storage (sessionStorage)" },
    purpose: { he: "כניסה לממשק הניהול בלבד; לא נוצר אצל גולשים רגילים.", en: "Admin console sign-in only; never set for regular visitors." },
    duration: { he: "עד סגירת הלשונית", en: "Until the tab is closed" },
  },
  {
    name: "cdn.jsdelivr.net", provider: { he: "jsDelivr (רשת הפצת תוכן)", en: "jsDelivr (content delivery network)" }, kind: REQUEST,
    purpose: { he: "טעינת קובצי השפה וגופן הדיסלקציה של תפריט הנגישות. השרת מקבל את כתובת ה-IP ופרטי הדפדפן.", en: "Loads the accessibility menu's language files and dyslexia font. The server receives your IP address and browser details." },
    duration: NONE,
  },
  {
    name: "tile.openstreetmap.org", provider: { he: "קרן OpenStreetMap (בריטניה)", en: "OpenStreetMap Foundation (UK)" }, kind: REQUEST,
    purpose: { he: "אריחי המפה בעמוד המפה. השרת מקבל את כתובת ה-IP, פרטי הדפדפן ואזור המפה שמוצג.", en: "Map tiles on the map page. The server receives your IP address, browser details and the map area shown." },
    duration: NONE,
  },
];

const COL = {
  he: { name: "שם", provider: "ספק", purpose: "מטרה", duration: "משך" },
  en: { name: "Name", provider: "Provider", purpose: "Purpose", duration: "Duration" },
};

/** What the site stores on the device and which third parties it contacts (/privacy). */
export function StorageTable() {
  const { lang } = useT();
  const t = COL[lang];
  return (
    <div className="table-scroll">
      <table className="storage">
        <thead>
          <tr><th>{t.name}</th><th>{t.provider}</th><th>{t.purpose}</th><th>{t.duration}</th></tr>
        </thead>
        <tbody>
          {STORAGE.map((s) => (
            <tr key={s.name}>
              <td><code dir="ltr">{s.name}</code><small>{s.kind[lang]}</small></td>
              <td>{s.provider[lang]}</td>
              <td>{s.purpose[lang]}</td>
              <td>{s.duration[lang]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
