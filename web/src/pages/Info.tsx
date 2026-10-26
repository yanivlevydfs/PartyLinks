// About, FAQ, contact and partnerships. Crawlers without JavaScript get a summary from seo.py (INFO_PAGES).
// The contact address comes from config (site.email, else site.privacy.email) via GET /site.
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import type { Lang } from "../i18n";
import { Hero, HiddenEmail, useT, useTitle } from "../ui";

// Footer links, in this order.
export const INFO_LINKS: { path: string; label: Record<Lang, string> }[] = [
  { path: "/about", label: { he: "אודות", en: "About" } },
  { path: "/faq", label: { he: "שאלות נפוצות", en: "FAQ" } },
  { path: "/contact", label: { he: "צור קשר", en: "Contact us" } },
  { path: "/partners", label: { he: "שיתופי פעולה", en: "Partnerships" } },
  { path: "/stores", label: { he: "חנויות הכרטיסים", en: "Ticket stores" } },
];

const title = (path: string, lang: Lang) => INFO_LINKS.find((l) => l.path === path)!.label[lang];

function useEmail() {
  return useQuery({ queryKey: ["site"], queryFn: () => fetch("/site").then((r) => r.json() as Promise<{ email?: string }>) }).data?.email;
}

function Mail({ subject }: { subject?: string }) {
  const { lang } = useT();
  const email = useEmail();
  if (!email) return <p className="muted">{lang === "he" ? "כתובת הדוא\"ל תפורסם בקרוב." : "Our email address will be published soon."}</p>;
  return (
    <ul className="contact">
      <li>{lang === "he" ? "דוא\"ל" : "Email"}: <HiddenEmail code={email} subject={subject} /></li>
    </ul>
  );
}

function Back() {
  const { lang } = useT();
  return <p><Link to="/">{lang === "he" ? "לכל האירועים" : "All events"}</Link></p>;
}

const ABOUT = {
  he: {
    intro: "SPOTX הוא המקום אחד לכל מה שקורה בערב: מסיבות, הופעות, סטנדאפ, תיאטרון, הרצאות ומה שביניהם, בכל הארץ.",
    whyTitle: "למה SPOTX",
    why: "האירועים בישראל מפוזרים בין עשרות אתרי כרטיסים, עמודי מפיקים ורשתות חברתיות. כדי לדעת מה יש הערב צריך לפתוח עשר לשוניות. ריכזנו הכול במקום אחד, עם חיפוש וסינון לפי תאריך, עיר, ז׳אנר, מחיר ומרחק.",
    howTitle: "איך זה עובד",
    how: [
      "המערכת סורקת כל הזמן את אתרי הכרטיסים והמפיקים, ומעדכנת תאריכים, ליינאפ ומחירים.",
      "אירוע שמופיע בכמה אתרים מאוחד לכרטיס אחד, עם כל האפשרויות לקנייה.",
      "לחיצה על \"כרטיסים\" מעבירה ישירות לאתר המוכר. אנחנו לא מוכרים כרטיסים ולא גובים עמלה מכם.",
    ],
    freeTitle: "חינם ובלי הרשמה",
    free: "אין חשבונות, אין הרשמה ואין פרסומות מעקב. נכנסים, מוצאים, יוצאים לבלות.",
  },
  en: {
    intro: "SPOTX is one place for everything happening tonight: parties, concerts, stand-up, theatre, talks and everything in between, all over Israel.",
    whyTitle: "Why SPOTX",
    why: "Events in Israel are spread across dozens of ticketing sites, promoter pages and social networks; finding out what's on tonight takes ten tabs. We put it all in one place, searchable and filterable by date, city, genre, price and distance.",
    howTitle: "How it works",
    how: [
      "We continuously scan ticketing and promoter sites, keeping dates, lineups and prices up to date.",
      "An event listed on several sites is merged into one card, with every place to buy.",
      "\"Tickets\" takes you straight to the seller. We don't sell tickets and charge you nothing.",
    ],
    freeTitle: "Free, no sign-up",
    free: "No accounts, no sign-up and no tracking ads. Come in, find something, go out.",
  },
};

export function AboutPage() {
  const { lang } = useT();
  const t = ABOUT[lang];
  useTitle(title("/about", lang));
  return (
    <>
      <Hero title={title("/about", lang)} />
      <main className="wrap statement">
        <p>{t.intro}</p>
        <h2>{t.whyTitle}</h2>
        <p>{t.why}</p>
        <h2>{t.howTitle}</h2>
        <ul>{t.how.map((x) => <li key={x}>{x}</li>)}</ul>
        <h2>{t.freeTitle}</h2>
        <p>{t.free}</p>
        <p>
          <Link to="/faq">{title("/faq", lang)}</Link>{" · "}
          <Link to="/contact">{title("/contact", lang)}</Link>{" · "}
          <Link to="/partners">{title("/partners", lang)}</Link>
        </p>
      </main>
    </>
  );
}

const FAQ: Record<Lang, [string, string][]> = {
  he: [
    ["מה זה SPOTX?", "אתר שמרכז את כל האירועים, המסיבות וההופעות בישראל מעשרות אתרי כרטיסים, עם חיפוש לפי תאריך, עיר, ז׳אנר, מחיר ומרחק ממך."],
    ["אתם מוכרים כרטיסים?", "לא. הכפתור \"כרטיסים\" מעביר לאתר שמוכר את הכרטיס, והרכישה נעשית שם. אנחנו לא צד לעסקה."],
    ["כמה זה עולה?", "השימוש באתר חינם לגמרי, בלי הרשמה ובלי חשבון."],
    ["המחירים מדויקים?", "המחירים נלקחים מאתרי הכרטיסים ומתעדכנים כל הזמן, אבל הם יכולים להשתנות. המחיר הקובע הוא זה שבאתר המוכר."],
    ["כל כמה זמן האתר מתעדכן?", "הסריקה רצה כל הזמן לאורך היום, כך שאירועים חדשים, שינויים וביטולים מופיעים תוך זמן קצר."],
    ["איך מוצאים אירועים קרובים אליי?", "בחרו \"לידי\" בסינון המיקום, או פתחו את המפה כדי לראות מה קורה סביבכם."],
    ["יש אירועים בחינם?", "כן. סננו לפי \"כניסה חופשית\" כדי לראות רק אירועים בלי עלות."],
    ["האירוע שלי לא מופיע. מה עושים?", "אם הוא נמכר באחד מאתרי הכרטיסים הגדולים, הוא כנראה יופיע בקרוב. אם לא, כתבו לנו דרך עמוד צור קשר, ומפיקים מוזמנים לעמוד שיתופי הפעולה."],
    ["מצאתי טעות באירוע.", "פרטי האירוע מגיעים מאתר הכרטיסים. ספרו לנו דרך עמוד צור קשר ונתקן."],
    ["יש אירועים בחו״ל?", "כן, בעמוד \"חו״ל\" יש כרטיסים וחבילות להופעות ולפסטיבלים בעולם."],
  ],
  en: [
    ["What is SPOTX?", "A site that brings together every event, party and show in Israel from dozens of ticketing sites, searchable by date, city, genre, price and distance from you."],
    ["Do you sell tickets?", "No. The \"Tickets\" button takes you to the site that sells them, and you buy there. We are not a party to the purchase."],
    ["What does it cost?", "Using the site is completely free, with no sign-up and no account."],
    ["Are the prices accurate?", "Prices come from the ticketing sites and are refreshed constantly, but they can change. The seller's price is the one that counts."],
    ["How often is the site updated?", "Scanning runs throughout the day, so new events, changes and cancellations show up quickly."],
    ["How do I find events near me?", "Choose \"Near me\" in the location filter, or open the map to see what's on around you."],
    ["Are there free events?", "Yes. Filter by \"Free entry\" to see only events that cost nothing."],
    ["My event isn't listed. What can I do?", "If it's sold on one of the major ticketing sites it will probably show up soon. If not, write to us through the contact page; promoters are welcome on the partnerships page."],
    ["I found a mistake in an event.", "Event details come from the ticketing site. Let us know through the contact page and we'll fix it."],
    ["Are there events abroad?", "Yes, the \"Abroad\" page has tickets and packages for concerts and festivals around the world."],
  ],
};

export function FaqPage() {
  const { lang } = useT();
  useTitle(title("/faq", lang));
  return (
    <>
      <Hero title={title("/faq", lang)} />
      <main className="wrap statement">
        {FAQ[lang].map(([q, a]) => (
          <section key={q}>
            <h2>{q}</h2>
            <p>{a}</p>
          </section>
        ))}
        <p><Link to="/contact">{lang === "he" ? "לא מצאתם תשובה? צרו קשר" : "Didn't find an answer? Contact us"}</Link></p>
      </main>
    </>
  );
}

const CONTACT = {
  he: {
    intro: "יש שאלה, הצעה, טעות באירוע או אירוע שחסר? נשמח לשמוע. כדי שנוכל לעזור מהר, צרפו קישור לאירוע או לעמוד הרלוונטי.",
    more: "מפיקים, מקומות ואתרי כרטיסים:",
    partners: "לעמוד שיתופי הפעולה",
    privacy: "בקשות בנושא פרטיות ונגישות:",
  },
  en: {
    intro: "A question, a suggestion, a wrong detail or a missing event? We'd love to hear from you. To help us answer quickly, include a link to the event or page.",
    more: "Promoters, venues and ticketing sites:",
    partners: "see Partnerships",
    privacy: "Privacy and accessibility requests:",
  },
};

export function ContactPage() {
  const { lang } = useT();
  const t = CONTACT[lang];
  useTitle(title("/contact", lang));
  return (
    <>
      <Hero title={title("/contact", lang)} />
      <main className="wrap statement">
        <p>{t.intro}</p>
        <Mail />
        <p>{t.more} <Link to="/partners">{t.partners}</Link></p>
        <p className="muted">
          {t.privacy} <Link to="/privacy">{lang === "he" ? "מדיניות פרטיות" : "Privacy policy"}</Link>
          {" · "}<Link to="/accessibility">{lang === "he" ? "הצהרת נגישות" : "Accessibility statement"}</Link>
        </p>
        <Back />
      </main>
    </>
  );
}

const PARTNERS = {
  he: {
    intro: "SPOTX מגיע לאנשים שמחפשים בדיוק עכשיו לאן לצאת. אם אתם מייצרים אירועים, מארחים אותם או מוכרים להם כרטיסים, בואו נדבר.",
    who: [
      ["מפיקים ואמנים", "ודאו שהאירועים שלכם מופיעים עם כל הפרטים: ליינאפ, תמונות, מחירים וקישור ישיר לקנייה."],
      ["מקומות ומועדונים", "עמוד מקום מסודר עם כל האירועים הקרובים שלכם, על המפה ובחיפוש לפי עיר."],
      ["אתרי כרטיסים", "חיבור ישיר (פיד או API) כדי שהאירועים שלכם יתעדכנו מהר ובדיוק, ומעקב אחרי ההפניות שאנחנו שולחים אליכם."],
      ["מותגים ומדיה", "תוכן משותף, כתבות בבלוג ומדריכי בילוי לקהל שיוצא לבלות."],
    ],
    how: "כתבו לנו כמה מילים על עצמכם ועל מה שמעניין אתכם, ונחזור אליכם.",
    subject: "שיתוף פעולה עם SPOTX",
  },
  en: {
    intro: "SPOTX reaches people who are looking for somewhere to go right now. If you create events, host them or sell their tickets, let's talk.",
    who: [
      ["Promoters and artists", "Make sure your events appear with every detail: lineup, images, prices and a direct link to buy."],
      ["Venues and clubs", "A proper venue page with all your upcoming events, on the map and in city search."],
      ["Ticketing sites", "A direct connection (feed or API) so your events update quickly and accurately, with tracking of the traffic we send you."],
      ["Brands and media", "Joint content, blog features and going-out guides for people heading out."],
    ],
    how: "Write us a few words about yourself and what interests you, and we'll get back to you.",
    subject: "Partnership with SPOTX",
  },
};

export function PartnersPage() {
  const { lang } = useT();
  const t = PARTNERS[lang];
  useTitle(title("/partners", lang));
  return (
    <>
      <Hero title={title("/partners", lang)} />
      <main className="wrap statement">
        <p>{t.intro}</p>
        {t.who.map(([h, p]) => (
          <section key={h}>
            <h2>{h}</h2>
            <p>{p}</p>
          </section>
        ))}
        <h2>{lang === "he" ? "יוצרים קשר" : "Get in touch"}</h2>
        <p>{t.how}</p>
        <Mail subject={t.subject} />
        <Back />
      </main>
    </>
  );
}
