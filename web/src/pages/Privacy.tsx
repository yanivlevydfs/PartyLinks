// Privacy and cookie policy (מדיניות פרטיות ועוגיות), per the Israeli Privacy Protection Law, 1981
// (Amendment 13) and the Privacy Protection (Data Security) Regulations, 2017.
// Contact details come from config (site.privacy) via GET /site; the storage table from consent.tsx.
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { StorageTable, useConsent } from "../consent";
import { Hero, HiddenEmail, useT, useTitle } from "../ui";

interface SiteInfo {
  name: string;
  privacy: { email: string; updated: string | null };
}

type Section = { title: string; body: string[]; list?: string[] };

const TEXT = {
  he: {
    title: "מדיניות פרטיות ועוגיות",
    intro: (name: string) =>
      `${name} ("האתר", "אנחנו") מרכז מידע על אירועים בישראל מאתרי כרטיסים ציבוריים. אנחנו מכבדים את פרטיותך ופועלים לפי חוק הגנת הפרטיות, התשמ"א-1981 (כולל תיקון 13 שנכנס לתוקף ב-14 באוגוסט 2025) ותקנות הגנת הפרטיות (אבטחת מידע), התשע"ז-2017. מסמך זה מסביר איזה מידע מעובד כשאתם גולשים באתר, למה, למי הוא מועבר, ומהן זכויותיכם. השימוש באתר אינו מחייב מסירת מידע, והוא נעשה מרצונכם החופשי.`,
    sections: [
      {
        title: "המידע שאנחנו אוספים",
        body: [
          "השימוש באתר אינו מחייב הרשמה: כל האירועים, החיפוש והמפה פתוחים לכולם בלי חשבון. מי שבוחר להתחבר עם Google מוסר לנו את המידע המפורט בסעיף \"חשבון משתמש (רשות)\". מעבר לכך אין באתר טפסים, רכישות או פרסומות.",
          "כמו בכל אתר, השרת מקבל בכל בקשה מידע טכני: כתובת IP, סוג הדפדפן ומערכת ההפעלה (User-Agent), הכתובת המבוקשת ושעת הבקשה. המידע משמש אך ורק להפעלת האתר ולאבטחתו:",
        ],
        list: [
          "הגבלת קצב בקשות וחסימה זמנית של כתובות שמבצעות סריקות או ניסיונות תקיפה. מונים אלה נשמרים בזיכרון השרת בלבד ונמחקים תוך דקות עד שעות.",
          "יומן שגיאות טכני (בקשות שנכשלו וחסימות), שנשמר לתקופה קצרה ונמחק במחזוריות.",
        ],
      },
      {
        title: "חשבון משתמש (רשות)",
        body: ["אפשר, אך לא חובה, להתחבר עם חשבון Google כדי לשמור אירועים בכל מכשיר, לקבל התראות על מסיבות ואירועים שמתאימים לכם ועדכון שבועי. במקרה כזה:"],
        list: [
          "בכניסה עם Google אנחנו מקבלים ושומרים את מזהה חשבון ה-Google, כתובת הדוא\"ל, השם וכתובת תמונת הפרופיל. איננו מקבלים את הסיסמה שלכם.",
          "נשמר גם מה שאתם יוצרים: אירועים ששמרתם, התראות (סוגי אירועים, אזורים, זמנים ומילות חיפוש שבחרתם), אילו אירועים התאימו להן ונשלחו אליכם, ושפת המיילים.",
          "העדכון השבועי בדוא\"ל נשלח רק למי שביקש אותו במפורש. אנחנו שומרים אם ביקשתם ומתי (חוק התקשורת, סעיף 30א), וכל מייל כולל קישור להסרה בלחיצה אחת.",
          "התחברות: תאריך יצירת החשבון והכניסה האחרונה, ועוגיית התחברות שרק טביעת ה-SHA-256 שלה נשמרת אצלנו. איננו שומרים כתובת IP או סוג דפדפן לצד החשבון.",
          "המטרות: הפעלת החשבון, שמירת אירועים, התראות, מיילים שביקשתם ואבטחה. איננו משתמשים במידע לפרסום ממוקד ואיננו מוכרים אותו.",
          "שמירה ומחיקה: המידע נשמר כל עוד החשבון קיים. אירוע שהסתיים נמחק מהשמורים ומההתראות. אפשר למחוק את החשבון בכל עת בעמוד \"החשבון שלי\"; המחיקה מיידית ולצמיתות.",
          "החשבון מיועד לבני 16 ומעלה.",
        ],
      },
      {
        title: "מה אנחנו לא עושים",
        body: [],
        list: [
          "לא משתמשים בעוגיות פרסום או פרופיילינג, ולא בפיקסלים של רשתות חברתיות. סטטיסטיקת גלישה (Google Analytics) נטענת רק בהסכמתכם.",
          "לא מוכרים, לא משכירים ולא מעבירים מידע לצדדים שלישיים לשיווק.",
          "לא מצליבים מידע כדי לזהות אתכם, ולא מקבלים החלטות אוטומטיות שנוגעות אליכם.",
        ],
      },
      {
        title: "עוגיות ואחסון מקומי",
        body: [
          "עוגיות (Cookies) ואחסון מקומי (Local Storage) הם קבצים קטנים שאתר שומר בדפדפן. בלי הסכמה, האתר שומר רק פריטים הכרחיים להפעלתו, שאינם משמשים למעקב. סטטיסטיקת גלישה (Google Analytics, ששומרת עוגייה) ותוכן מצד שלישי שאינו הכרחי (מפת OpenFreeMap) נטענים רק לאחר שנתתם לכך הסכמה מפורשת, שאותה אפשר לשנות או לבטל בכל עת דרך \"הגדרות עוגיות\" בתחתית כל עמוד או בכפתור שלמטה.",
          "הטבלה הבאה מפרטת כל מה שנשמר במכשיר שלכם וכל צד שלישי שהדפדפן פונה אליו:",
        ],
      },
    ] as Section[],
    sections2: [
      {
        title: "צדדים שלישיים והעברת מידע לחו\"ל",
        body: [
          "האתר מתארח אצל ספק תשתית ענן (Railway, ארה\"ב), שמעבד עבורנו את התעבורה ואת יומני השרת. ספקי התשתית, ו-jsDelivr, Google ו-OpenFreeMap המפורטים בטבלה, נמצאים מחוץ לישראל; ההעברה נעשית בהתאם לתקנות הגנת הפרטיות (העברת מידע אל מאגרי מידע שמחוץ לגבולות המדינה), התשס\"א-2001, ולמדיניות הפרטיות של כל ספק.",
          "הגופנים באתר מאוחסנים בשרת שלנו, כך שלא נשלחת בקשה ל-Google Fonts. כרזות האירועים נשמרות ברובן בשרת שלנו; כשכרזה טרם נשמרה, היא נטענת ישירות מאתר הכרטיסים שפרסם אותה, בלי לשלוח לו את כתובת העמוד שממנו הגעתם.",
          "לחיצה על קישור לרכישת כרטיסים מעבירה אתכם לאתר חיצוני (כמו Go-Out, Eventer וכו'). אתרים אלה אינם בשליטתנו, וחלה עליהם מדיניות הפרטיות שלהם.",
          "כניסה עם Google מתבצעת בעמוד של Google (Google LLC, ארה\"ב), שמעביר לנו רק את פרטי החשבון שאישרתם. מיילים נשלחים דרך ספק הדיוור Resend (ארה\"ב), שמקבל לשם כך את כתובת הדוא\"ל ואת תוכן המייל בלבד.",
        ],
      },
      {
        title: "אבטחת מידע",
        body: [
          "האתר מוגש בחיבור מוצפן (HTTPS) ומוגן במדיניות אבטחת תוכן (CSP), בכותרות אבטחה ובחסימה אוטומטית של סורקי פגיעויות. הגישה לממשק הניהול מוגבלת למנהלי האתר (חשבון Google בהרשאת מנהל או אסימון גישה), וכתובות דוא\"ל ואסימוני התחברות אינם נרשמים ביומני השרת. אף שאנו נוקטים אמצעים סבירים, אין אבטחה מושלמת ברשת.",
        ],
      },
      {
        title: "הזכויות שלכם",
        body: ["לפי חוק הגנת הפרטיות, עומדות לכם בין השאר הזכויות הבאות:"],
        list: [
          "לעיין במידע עליכם המוחזק אצלנו (סעיף 13 לחוק).",
          "לבקש לתקן או למחוק מידע שאינו נכון, שלם, ברור או מעודכן (סעיף 14 לחוק).",
          "לבטל בכל עת הסכמה שנתתם, באותה קלות שבה נתתם אותה, דרך \"הגדרות עוגיות\". הביטול חל מכאן והלאה.",
          "למחוק בעצמכם את כל מה שנשמר במכשיר, דרך הגדרות הדפדפן (מחיקת נתוני אתרים).",
          "בעלי חשבון יכולים לעיין בפרטיהם, לתקן אותם ולמחוק את החשבון בעצמם בעמוד \"החשבון שלי\", ולהפסיק מיילים בקישור שבכל מייל.",
          "להגיש תלונה לרשות להגנת הפרטיות במשרד המשפטים (gov.il/he/departments/the_privacy_protection_authority).",
        ],
      },
      {
        title: "קטינים",
        body: [
          "חשבון משתמש מיועד לבני 16 ומעלה, והאתר אינו אוסף ביודעין מידע אישי מקטינים מתחת לגיל זה. חלק מהאירועים המופיעים באתר מוגבלים לגיל מסוים; האחריות לבדיקת הגיל ולמכירת הכרטיסים היא של המפיקים ואתרי הכרטיסים.",
        ],
      },
      {
        title: "שינויים במדיניות",
        body: [
          "אם נשנה את המידע שנשמר או את השימוש בו, נעדכן את המסמך ואת תאריך העדכון. שינוי שמחייב הסכמה יוביל להצגת חלון ההסכמה מחדש, וההסכמה הקודמת לא תחול עליו. הבחירה שלכם אינה נשמרת במכשיר, ולכן נשאל אתכם מחדש בכל ביקור.",
        ],
      },
    ] as Section[],
    manage: "שינוי הגדרות עוגיות",
    contactTitle: "יצירת קשר",
    contact: "לשאלות, לבקשות עיון, תיקון או מחיקה, או לכל פנייה בנושא פרטיות:",
    email: "דוא\"ל",
    contactMissing: "כתובת הפנייה בנושא פרטיות תפורסם בקרוב.",
    consentId: "מזהה ההסכמה שלך (לציון בפנייה):",
    updated: "המדיניות עודכנה לאחרונה בתאריך",
    back: "לכל האירועים",
  },
  en: {
    title: "Privacy and cookie policy",
    intro: (name: string) =>
      `${name} ("the site", "we") gathers event listings in Israel from public ticketing sites. We respect your privacy and follow the Israeli Privacy Protection Law, 1981 (including Amendment 13, in force since 14 August 2025) and the Privacy Protection (Data Security) Regulations, 2017. This policy explains what data is processed when you browse the site, why, who receives it, and your rights. Using the site requires no personal details, and you use it of your own free will.`,
    sections: [
      {
        title: "What we collect",
        body: [
          "Using the site needs no sign-up: every event, the search and the map are open to everyone without an account. If you choose to sign in with Google, you give us the data listed under \"User account (optional)\". Beyond that the site has no forms, purchases or ads.",
          "Like every website, the server receives technical data with each request: IP address, browser and operating system (User-Agent), the requested address and the time. It is used only to run and protect the site:",
        ],
        list: [
          "Rate limiting and temporary blocking of addresses that scan or attack the site. These counters live in server memory only and are gone within minutes to hours.",
          "A technical error log (failed requests and blocks), kept for a short period and rotated.",
        ],
      },
      {
        title: "User account (optional)",
        body: ["You may, but don't have to, sign in with a Google account to keep saved events on every device, get alerts for parties and events that suit you, and a weekly update. If you do:"],
        list: [
          "When you sign in with Google we receive and store your Google account ID, email address, name and profile picture URL. We never receive your password.",
          "We also keep what you create: saved events, alerts (the event types, areas, times and search words you chose), which events matched them and were sent to you, and your email language.",
          "The weekly email update goes only to people who asked for it. We keep whether and when you asked (Communications Law, section 30A), and every email has a one-click unsubscribe link.",
          "Sign-in: when the account was created and last used, and a sign-in cookie of which we keep only a SHA-256 fingerprint. We store no IP address or browser type with the account.",
          "Purposes: running the account, saved events, alerts, the emails you asked for, and security. We don't use the data for targeted advertising and never sell it.",
          "Retention and deletion: data is kept while the account exists. An event that has ended is removed from saved events and alerts. You can delete the account at any time on \"My account\"; deletion is immediate and permanent.",
          "Accounts are for people aged 16 and over.",
        ],
      },
      {
        title: "What we don't do",
        body: [],
        list: [
          "No advertising or profiling cookies, and no social media pixels. Usage statistics (Google Analytics) load only with your consent.",
          "We don't sell, rent or share data with third parties for marketing.",
          "We don't combine data to identify you, and make no automated decisions about you.",
        ],
      },
      {
        title: "Cookies and local storage",
        body: [
          "Cookies and local storage are small files a website keeps in your browser. Without consent, this site stores only items it needs to work, never used for tracking. Usage statistics (Google Analytics, which sets a cookie) and non-essential third-party content (the OpenFreeMap map) load only after your explicit consent, which you can change or withdraw at any time from \"Cookie settings\" at the bottom of every page or with the button below.",
          "This table lists everything stored on your device and every third party your browser contacts:",
        ],
      },
    ] as Section[],
    sections2: [
      {
        title: "Third parties and transfers abroad",
        body: [
          "The site is hosted by a cloud infrastructure provider (Railway, USA), which processes traffic and server logs on our behalf. The hosting provider, and jsDelivr, Google and OpenFreeMap listed above, are outside Israel; transfers follow the Privacy Protection (Transfer of Data to Databases Abroad) Regulations, 2001, and each provider's privacy policy.",
          "Our fonts are served from our own server, so nothing is sent to Google Fonts. Event posters are mostly stored on our server; a poster not yet stored loads directly from the ticketing site that published it, without telling it which page you came from.",
          "Ticket links take you to external sites (Go-Out, Eventer, etc.). They are not under our control and their own privacy policies apply.",
          "Signing in with Google happens on Google's own page (Google LLC, USA), which passes us only the account details you approve. Emails are sent through the email provider Resend (USA), which receives only your email address and the email's content for that purpose.",
        ],
      },
      {
        title: "Security",
        body: [
          "The site is served over an encrypted connection (HTTPS) and protected by a Content Security Policy, security headers and automatic blocking of vulnerability scanners. The admin console is limited to the site's administrators (a Google account with admin rights, or an access token), and email addresses and sign-in tokens are never written to server logs. While we take reasonable measures, no security on the internet is perfect.",
        ],
      },
      {
        title: "Your rights",
        body: ["Under the Privacy Protection Law you have, among others, the right to:"],
        list: [
          "Review the data we hold about you (section 13 of the law).",
          "Ask us to correct or delete data that is wrong, incomplete, unclear or outdated (section 14).",
          "Withdraw consent at any time, as easily as you gave it, from \"Cookie settings\". Withdrawal applies from then on.",
          "Delete everything stored on your device yourself, in your browser settings (clear site data).",
          "Account holders can review, correct and delete their account themselves on \"My account\", and stop emails with the link in every email.",
          "Complain to the Privacy Protection Authority at the Ministry of Justice (gov.il/en/departments/the_privacy_protection_authority).",
        ],
      },
      {
        title: "Minors",
        body: [
          "User accounts are for people aged 16 and over, and the site does not knowingly collect personal data from minors under that age. Some listed events have age limits; checking age and selling tickets is the responsibility of the organizers and ticketing sites.",
        ],
      },
      {
        title: "Changes to this policy",
        body: [
          "If we change what is stored or how it is used, we will update this page and its date. A change that needs consent brings the consent window back, and earlier consent will not cover it. Your choice is not saved on your device, so we ask again on every visit.",
        ],
      },
    ] as Section[],
    manage: "Change cookie settings",
    contactTitle: "Contact",
    contact: "For questions, requests to review, correct or delete data, or any privacy matter:",
    email: "Email",
    contactMissing: "The privacy contact address will be published soon.",
    consentId: "Your consent ID (quote it when contacting us):",
    updated: "This policy was last updated on",
    back: "All events",
  },
};

function Sections({ list }: { list: Section[] }) {
  return list.map((s) => (
    <section key={s.title}>
      <h2>{s.title}</h2>
      {s.body.map((p) => <p key={p}>{p}</p>)}
      {s.list && <ul>{s.list.map((x) => <li key={x}>{x}</li>)}</ul>}
    </section>
  ));
}

export default function PrivacyPage() {
  const { t: ui, lang } = useT();
  const t = TEXT[lang];
  useTitle(t.title);
  const { consent, openSettings } = useConsent();
  const site = useQuery({ queryKey: ["site"], queryFn: () => fetch("/site").then((r) => r.json() as Promise<SiteInfo>) }).data;
  const p = site?.privacy;
  const updated = p?.updated && new Date(p.updated + "T12:00:00Z").toLocaleDateString(ui.locale, { dateStyle: "long", timeZone: "UTC" });

  return (
    <>
      <Hero title={t.title} />
      <main className="wrap statement">
        <p>{t.intro(site?.name ?? "SPOTX")}</p>
        <Sections list={t.sections} />
        <StorageTable />
        <p><button className="c-btn" onClick={openSettings}>{t.manage}</button></p>
        <Sections list={t.sections2} />

        <h2>{t.contactTitle}</h2>
        <p>{t.contact}</p>
        {p?.email ? (
          <ul className="contact">
            <li>{t.email}: <HiddenEmail code={p.email} /></li>
          </ul>
        ) : (
          site && <p className="muted">{t.contactMissing}</p>
        )}
        {consent && <p className="muted">{t.consentId} <code dir="ltr">{consent.id}</code></p>}

        {updated && <p className="muted">{t.updated} {updated}.</p>}
        <p><Link to="/">{t.back}</Link></p>
      </main>
    </>
  );
}
