// Accessibility statement (הצהרת נגישות), per Israeli regulations / IS 5568.
// Contact details come from config (site.accessibility) via GET /site.
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Hero, useT, useTitle } from "../ui";

interface SiteInfo {
  name: string;
  accessibility: { coordinator: string; email: string; phone: string; updated: string | null };
}

const TEXT = {
  he: {
    title: "הצהרת נגישות",
    intro: (name: string) =>
      `${name} רואה חשיבות רבה במתן שירות שוויוני לכלל הגולשים, ובכלל זה לאנשים עם מוגבלות. אנו משקיעים מאמצים כדי שהאתר יהיה נוח, ידידותי ונגיש לכולם.`,
    standardTitle: "רמת הנגישות",
    standard:
      "האתר נבנה במטרה לעמוד בדרישות תקנות שוויון זכויות לאנשים עם מוגבלות (התאמות נגישות לשירות), התשע\"ג-2013, ובתקן הישראלי ת\"י 5568 המבוסס על הנחיות WCAG 2.0 ברמה AA. הבדיקות נערכו בדפדפנים Chrome, Edge, Firefox ו-Safari העדכניים, במחשב ובנייד.",
    doneTitle: "מה עשינו באתר",
    done: [
      "תפריט נגישות (בפינת המסך): הגדלת טקסט, ניגודיות גבוהה, גווני אפור, גופן ידידותי לדיסלקציה, הדגשת קישורים, עצירת אנימציות וסמן מוגדל.",
      "ניווט מלא במקלדת, עם סימון בולט של הרכיב הפעיל.",
      "מבנה עמודים סמנטי: כותרות, אזורי ניווט ורשימות, לתמיכה בקוראי מסך.",
      "תמיכה מלאה בעברית (מימין לשמאל) ובאנגלית, עם הגדרת שפה נכונה לכל עמוד.",
      "כיבוד הגדרת המערכת \"הפחתת תנועה\": האנימציות נעצרות אוטומטית.",
      "טקסט חלופי לתמונות, ותוויות לכל כפתור ושדה חיפוש.",
      "התוכן המרכזי של כל עמוד זמין גם כטקסט רגיל, ללא תלות ב-JavaScript.",
      "האתר מותאם לכל גודל מסך, וניתן להגדלה עד 200% בלי לאבד תוכן.",
    ],
    limitsTitle: "מגבלות ידועות",
    limits: [
      "פרטי האירועים, הכרזות והתמונות מגיעים מאתרי הכרטיסים של המפיקים. ייתכן שכרזות מכילות טקסט בתוך התמונה ללא תיאור מלא; פרטי האירוע עצמם מוצגים תמיד גם כטקסט.",
      "המפה היא רכיב חזותי. אותו מידע זמין גם ברשימות המקומות והאירועים.",
      "רכישת הכרטיסים נעשית באתרים חיצוניים, שהנגישות שלהם באחריות המפעילים שלהם.",
    ],
    contactTitle: "פניות בנושא נגישות",
    contactIntro:
      "נתקלתם בבעיית נגישות? נשמח לשמוע ולתקן. כדי שנוכל לטפל בפנייה, נבקש לציין את העמוד, תיאור הבעיה, ואת הדפדפן וטכנולוגיית העזר שבהן השתמשתם.",
    coordinator: "רכז/ת הנגישות",
    email: "דוא\"ל",
    phone: "טלפון",
    contactMissing: "פרטי רכז/ת הנגישות יפורסמו בקרוב.",
    updated: "ההצהרה עודכנה לאחרונה בתאריך",
    back: "לכל האירועים",
  },
  en: {
    title: "Accessibility statement",
    intro: (name: string) =>
      `${name} is committed to giving everyone equal access to its service, including people with disabilities. We work to make this website easy, friendly and accessible for all.`,
    standardTitle: "Accessibility level",
    standard:
      "The site was built to meet the Israeli Equal Rights for Persons with Disabilities (Service Accessibility Adjustments) Regulations, 2013, and Israeli Standard IS 5568, based on WCAG 2.0 level AA. It was checked in current Chrome, Edge, Firefox and Safari, on desktop and mobile.",
    doneTitle: "What we did",
    done: [
      "An accessibility menu (screen corner): larger text, high contrast, grayscale, dyslexia-friendly font, highlighted links, stop animations and a bigger cursor.",
      "Full keyboard navigation with a clearly visible focus indicator.",
      "Semantic page structure (headings, navigation landmarks, lists) for screen readers.",
      "Full Hebrew (right-to-left) and English support, with the correct language set on every page.",
      "The system \"reduce motion\" setting is respected: animations stop automatically.",
      "Alternative text for images and labels for every button and search field.",
      "The main content of every page is also available as plain text, without JavaScript.",
      "Responsive on every screen size, and zoomable to 200% without losing content.",
    ],
    limitsTitle: "Known limitations",
    limits: [
      "Event details, posters and images come from the organizers' ticketing sites. Posters may contain text inside the image without a full description; the event details themselves are always also shown as text.",
      "The map is visual. The same information is available in the venue and party lists.",
      "Tickets are bought on external sites, whose accessibility is the responsibility of their operators.",
    ],
    contactTitle: "Accessibility contact",
    contactIntro:
      "Found an accessibility problem? We want to hear about it and fix it. Please include the page, a description of the problem, and the browser and assistive technology you used.",
    coordinator: "Accessibility coordinator",
    email: "Email",
    phone: "Phone",
    contactMissing: "The accessibility coordinator's details will be published soon.",
    updated: "This statement was last updated on",
    back: "All events",
  },
};

export default function AccessibilityPage() {
  const { t: ui, lang } = useT();
  const t = TEXT[lang];
  useTitle(t.title);
  const site = useQuery({ queryKey: ["site"], queryFn: () => fetch("/site").then((r) => r.json() as Promise<SiteInfo>) }).data;
  const a = site?.accessibility;
  const hasContact = !!(a?.coordinator && (a.email || a.phone));
  const updated = a?.updated && new Date(a.updated + "T12:00:00Z").toLocaleDateString(ui.locale, { dateStyle: "long", timeZone: "UTC" });

  return (
    <>
      <Hero title={t.title} />
      <main className="wrap statement">
        <p>{t.intro(site?.name ?? "PartyLinks")}</p>

        <h2>{t.standardTitle}</h2>
        <p>{t.standard}</p>

        <h2>{t.doneTitle}</h2>
        <ul>{t.done.map((x) => <li key={x}>{x}</li>)}</ul>

        <h2>{t.limitsTitle}</h2>
        <ul>{t.limits.map((x) => <li key={x}>{x}</li>)}</ul>

        <h2>{t.contactTitle}</h2>
        <p>{t.contactIntro}</p>
        {hasContact ? (
          <ul className="contact">
            <li>{t.coordinator}: <b>{a!.coordinator}</b></li>
            {a!.email && <li>{t.email}: <a href={`mailto:${a!.email}`} dir="ltr">{a!.email}</a></li>}
            {a!.phone && <li>{t.phone}: <a href={`tel:${a!.phone.replace(/[^\d+]/g, "")}`} dir="ltr">{a!.phone}</a></li>}
          </ul>
        ) : (
          site && <p className="muted">{t.contactMissing}</p>
        )}

        {updated && <p className="muted">{t.updated} {updated}.</p>}
        <p><Link to="/">{t.back}</Link></p>
      </main>
    </>
  );
}
