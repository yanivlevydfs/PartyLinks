// Cookie / local-storage consent, per the Israeli Privacy Protection Law, 1981 (as amended by
// Amendment 13, in force 14.8.2025): tell visitors what is stored on their device and which third
// parties receive their data, and load optional third-party content only after an informed, freely
// given, opt-in choice that is as easy to refuse or withdraw as to give.
//
// The site sets no advertising or analytics cookies. Strictly necessary storage needs no consent but
// is disclosed; the only optional category is external content (OpenStreetMap map tiles).
// STORAGE below is also the table on the privacy page (/privacy): keep it in sync with the code.
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { create } from "zustand";
import { useT } from "./ui";

export type Category = "external";
const OPTIONAL: Category[] = ["external"];

// Never written to the device: the choice lives in memory for this tab only, so the banner
// comes back on every visit. The key earlier versions stored is deleted on load.
try { localStorage.removeItem("partylinks-consent"); } catch {}
// Bump when a category is added or the use of data changes: everyone is asked again.
export const CONSENT_VERSION = 1;

type Choices = Record<Category, boolean>;
export interface Consent {
  v: number;
  id: string; // random, identifies this record if the visitor asks about their consent
  at: string; // ISO time of the choice
  choices: Choices;
}

interface ConsentState {
  consent: Consent | null;
  panel: "banner" | "settings" | null;
  save: (choices: Choices) => void;
  openSettings: () => void;
  close: () => void;
}

export const useConsent = create<ConsentState>()((set, get) => ({
  consent: null,
  panel: "banner",
  save: (choices) => {
    const consent: Consent = { v: CONSENT_VERSION, id: get().consent?.id ?? crypto.randomUUID(), at: new Date().toISOString(), choices };
    set({ consent, panel: null });
  },
  openSettings: () => set({ panel: "settings" }),
  // Closing without a choice is only possible once one was made; until then the banner stays.
  close: () => set((s) => ({ panel: s.consent ? null : "banner" })),
}));

/** Whether the visitor agreed to a category (false until they choose). */
export const useAllowed = (c: Category) => useConsent((s) => !!s.consent?.choices[c]);

const all = (value: boolean) => Object.fromEntries(OPTIONAL.map((c) => [c, value])) as Choices;

// --------------------------------------------------------------------------- what we store / send
type Text = { he: string; en: string };
export interface StorageItem {
  name: string;
  provider: Text;
  kind: Text;
  purpose: Text;
  duration: Text;
  category: "necessary" | Category;
}

const US = { he: "האתר (צד ראשון)", en: "This site (first party)" };
const LOCAL = { he: "אחסון מקומי (localStorage)", en: "Local storage (localStorage)" };
const REQUEST = { he: "בקשת רשת לצד שלישי", en: "Request to a third party" };
const NONE = { he: "לא נשמר במכשיר", en: "Nothing stored on the device" };

export const STORAGE: StorageItem[] = [
  {
    name: "partylinks", provider: US, kind: LOCAL, category: "necessary",
    purpose: { he: "שמירת שפת התצוגה שבחרת (עברית / אנגלית).", en: "Remembers the display language you chose (Hebrew / English)." },
    duration: { he: "עד שתמחקו את נתוני האתר בדפדפן", en: "Until you clear the site's data in your browser" },
  },
  {
    name: "asw-user-settings", provider: US, category: "necessary",
    kind: { he: "אחסון מקומי (או עוגייה, אם האחסון חסום)", en: "Local storage (or a cookie if storage is blocked)" },
    purpose: { he: "שמירת הגדרות תפריט הנגישות שבחרת (גודל טקסט, ניגודיות וכו').", en: "Remembers your accessibility-menu settings (text size, contrast, etc.)." },
    duration: { he: "עד שתמחקו את נתוני האתר בדפדפן", en: "Until you clear the site's data in your browser" },
  },
  {
    name: "partylinks-install-dismissed", provider: US, kind: LOCAL, category: "necessary",
    purpose: { he: "זמן הסגירה של באנר ההתקנה למסך הבית, כדי שלא יוצג שוב במשך 14 יום.", en: "When you closed the add-to-home-screen banner, so it stays hidden for 14 days." },
    duration: { he: "14 יום", en: "14 days" },
  },
  {
    name: "partylinks-admin-token", provider: US, category: "necessary",
    kind: { he: "אחסון לסשן (sessionStorage)", en: "Session storage (sessionStorage)" },
    purpose: { he: "כניסה לממשק הניהול בלבד; לא נוצר אצל גולשים רגילים.", en: "Admin console sign-in only; never set for regular visitors." },
    duration: { he: "עד סגירת הלשונית", en: "Until the tab is closed" },
  },
  {
    name: "cdn.jsdelivr.net", provider: { he: "jsDelivr (רשת הפצת תוכן)", en: "jsDelivr (content delivery network)" }, kind: REQUEST, category: "necessary",
    purpose: { he: "טעינת קובצי השפה וגופן הדיסלקציה של תפריט הנגישות. השרת מקבל את כתובת ה-IP ופרטי הדפדפן.", en: "Loads the accessibility menu's language files and dyslexia font. The server receives your IP address and browser details." },
    duration: NONE,
  },
  {
    name: "tile.openstreetmap.org", provider: { he: "קרן OpenStreetMap (בריטניה)", en: "OpenStreetMap Foundation (UK)" }, kind: REQUEST, category: "external",
    purpose: { he: "אריחי המפה בעמוד המפה. השרת מקבל את כתובת ה-IP, פרטי הדפדפן ואזור המפה שמוצג.", en: "Map tiles on the map page. The server receives your IP address, browser details and the map area shown." },
    duration: NONE,
  },
];

// --------------------------------------------------------------------------- UI
const TEXT = {
  he: {
    region: "הסכמה לעוגיות ולתוכן חיצוני",
    title: "הפרטיות שלך חשובה לנו",
    body: "אנחנו שומרים בדפדפן רק את מה שנחוץ להפעלת האתר: השפה והגדרות הנגישות. הבחירה שלך כאן לא נשמרת במכשיר. אין באתר עוגיות פרסום, מעקב או סטטיסטיקה. בהסכמתך נטען גם תוכן מצד שלישי: מפת OpenStreetMap, שמקבלת את כתובת ה-IP שלך. אפשר לשנות את הבחירה בכל עת דרך \"הגדרות עוגיות\" בתחתית העמוד.",
    policy: "מדיניות הפרטיות והעוגיות",
    acceptAll: "אישור הכל",
    rejectAll: "דחיית הכל",
    customize: "התאמה אישית",
    save: "שמירת הבחירה",
    settingsTitle: "הגדרות עוגיות ותוכן חיצוני",
    always: "תמיד פעיל",
    details: "מה נשמר ולמי נשלח",
    lastChoice: (d: string) => `הבחירה האחרונה נשמרה ב-${d}.`,
    close: "סגירה",
    categories: {
      necessary: { title: "הכרחי", text: "נדרש כדי שהאתר יעבוד ויזכור את השפה והגדרות הנגישות. אינו משמש למעקב ולא ניתן לכבות אותו." },
      external: { title: "תוכן חיצוני (מפה)", text: "טעינת אריחי המפה מ-OpenStreetMap בעמוד המפה. בלי הסכמה, המפה לא תיטען עד שתאשרו זאת שם." },
    },
    col: { name: "שם", provider: "ספק", purpose: "מטרה", duration: "משך" },
  },
  en: {
    region: "Cookie and external content consent",
    title: "Your privacy matters to us",
    body: "We only store what the site needs to work in your browser: your language and accessibility settings. The choice you make here is not saved on your device. There are no advertising, tracking or analytics cookies. With your consent we also load third-party content: the OpenStreetMap map, which receives your IP address. You can change your choice at any time from \"Cookie settings\" at the bottom of the page.",
    policy: "Privacy and cookie policy",
    acceptAll: "Accept all",
    rejectAll: "Reject all",
    customize: "Customize",
    save: "Save my choice",
    settingsTitle: "Cookie and external content settings",
    always: "Always on",
    details: "What is stored and who receives it",
    lastChoice: (d: string) => `Your last choice was saved on ${d}.`,
    close: "Close",
    categories: {
      necessary: { title: "Strictly necessary", text: "Needed for the site to work and remember your language and accessibility settings. Never used for tracking; can't be switched off." },
      external: { title: "External content (map)", text: "Loads map tiles from OpenStreetMap on the map page. Without consent, the map won't load until you allow it there." },
    },
    col: { name: "Name", provider: "Provider", purpose: "Purpose", duration: "Duration" },
  },
};

export function CookieConsent() {
  const { t: ui, lang } = useT();
  const t = TEXT[lang];
  const { consent, panel, save, close } = useConsent();
  const [choices, setChoices] = useState<Choices>(consent?.choices ?? all(false));
  const [custom, setCustom] = useState(false);
  const ref = useRef<HTMLElement>(null);
  const titleId = useId();
  const settings = panel === "settings" || custom;

  // Opened from the footer: move focus in, and back to the opener when it closes.
  useEffect(() => {
    if (panel !== "settings") return;
    const opener = document.activeElement as HTMLElement | null;
    setChoices(consent?.choices ?? all(false));
    ref.current?.focus();
    return () => opener?.focus?.();
  }, [panel]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!panel) return null;
  const choose = (c: Choices) => { setCustom(false); save(c); };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key !== "Escape") return;
    if (custom && !consent) setCustom(false);
    else close();
  };

  return (
    <section
      ref={ref}
      className={"consent" + (settings ? " open" : "")}
      role="dialog"
      aria-modal="false"
      aria-labelledby={titleId}
      tabIndex={-1}
      onKeyDown={onKey}
    >
      <div className="consent-in">
        <h2 id={titleId}>{settings ? t.settingsTitle : t.title}</h2>
        {!settings && (
          <p>
            {t.body} <Link to="/privacy">{t.policy}</Link>
          </p>
        )}

        {settings && (
          <>
            <ul className="consent-cats">
              <Cat title={t.categories.necessary.title} text={t.categories.necessary.text} on disabled label={t.always} />
              {OPTIONAL.map((c) => (
                <Cat
                  key={c}
                  title={t.categories[c].title}
                  text={t.categories[c].text}
                  on={choices[c]}
                  onChange={(on) => setChoices({ ...choices, [c]: on })}
                />
              ))}
            </ul>
            <details className="consent-details">
              <summary>{t.details}</summary>
              <StorageTable />
            </details>
            <p className="consent-meta">
              <Link to="/privacy">{t.policy}</Link>
              {consent && <> · {t.lastChoice(new Date(consent.at).toLocaleString(ui.locale, { dateStyle: "medium", timeStyle: "short" }))}</>}
            </p>
          </>
        )}

        {/* Accept and reject get the same weight: refusing must be as easy as agreeing. */}
        <div className="consent-btns">
          <button className="c-btn" onClick={() => choose(all(true))}>{t.acceptAll}</button>
          <button className="c-btn" onClick={() => choose(all(false))}>{t.rejectAll}</button>
          {settings ? (
            <button className="c-btn ghost" onClick={() => choose(choices)}>{t.save}</button>
          ) : (
            <button className="c-btn ghost" onClick={() => setCustom(true)}>{t.customize}</button>
          )}
          {panel === "settings" && consent && (
            <button className="c-btn ghost" onClick={close}>{t.close}</button>
          )}
        </div>
      </div>
    </section>
  );
}

function Cat({ title, text, on, disabled, label, onChange }: {
  title: string; text: string; on: boolean; disabled?: boolean; label?: string; onChange?: (on: boolean) => void;
}) {
  const id = useId();
  return (
    <li className="consent-cat">
      <div>
        <label htmlFor={id}><b>{title}</b></label>
        <p id={id + "-d"}>{text}</p>
      </div>
      <span className="switch-wrap">
        {label && <small>{label}</small>}
        <input
          id={id}
          type="checkbox"
          role="switch"
          className="switch"
          checked={on}
          disabled={disabled}
          aria-describedby={id + "-d"}
          onChange={(e) => onChange?.(e.target.checked)}
        />
      </span>
    </li>
  );
}

/** What the site stores on the device and which third parties it contacts (banner details, /privacy). */
export function StorageTable() {
  const { lang } = useT();
  const t = TEXT[lang];
  return (
    <div className="table-scroll">
      <table className="storage">
        <thead>
          <tr><th>{t.col.name}</th><th>{t.col.provider}</th><th>{t.col.purpose}</th><th>{t.col.duration}</th></tr>
        </thead>
        <tbody>
          {STORAGE.map((s) => (
            <tr key={s.name}>
              <td><code dir="ltr">{s.name}</code><small>{s.kind[lang]} · {t.categories[s.category].title}</small></td>
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
