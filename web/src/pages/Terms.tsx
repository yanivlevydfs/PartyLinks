// Terms of use (תנאי שימוש): SPOTX only aggregates listings sold on other sites, so the terms make
// clear it is not a party to any purchase and is not responsible for events, content or prices.
// The contact address is the privacy one (site.privacy in config) via GET /site.
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Hero, useT, useTitle } from "../ui";

const UPDATED = "2026-10-26";

type Section = { title: string; body: string[]; list?: string[] };

const TEXT = {
  he: {
    title: "תנאי שימוש",
    intro: (name: string) =>
      `ברוכים הבאים ל${name} ("האתר", "אנחנו"). השימוש באתר כפוף לתנאים אלה. גלישה באתר או שימוש בו מהווים הסכמה לתנאים; אם אינכם מסכימים להם, אל תשתמשו באתר. התנאים מנוסחים בלשון רבים ומתייחסים לכל המגדרים.`,
    sections: [
      {
        title: "מה האתר עושה",
        body: [
          "האתר הוא מנוע חיפוש ואיסוף בלבד: הוא אוסף באופן אוטומטי מידע על אירועים שמתפרסמים ונמכרים באתרי כרטיסים ובאתרים אחרים של צדדים שלישיים, ומציג אותו במקום אחד עם קישור לאתר המקורי.",
          "האתר אינו מפיק, אינו מארגן ואינו מוכר כרטיסים לאף אירוע, ואינו צד לעסקה כלשהי בינכם לבין מארגן אירוע או אתר כרטיסים.",
        ],
      },
      {
        title: "אין אחריות לאירועים, לתוכן ולמחירים",
        body: ["האתר ובעליו אינם אחראים, בין היתר, ל:"],
        list: [
          "קיום האירועים, ביטולם, דחייתם, שינוי מועדם, מיקומם, הליינאפ או כל פרט אחר בהם.",
          "התוכן, התיאורים, התמונות והמידע המופיעים באתר, שמקורם באתרים אחרים ועשויים להיות שגויים, חלקיים או לא מעודכנים.",
          "המחירים, דמי השירות, שינויי מחיר, זמינות כרטיסים, כניסה חופשית ושעות הכניסה. המידע מתעדכן כמה פעמים ביום ועלול להשתנות בכל רגע.",
          "הרכישה עצמה, התשלום, ההחזרים, הביטולים ושירות הלקוחות, שהם באחריות אתר הכרטיסים והמארגן בלבד.",
          "כל נזק, הפסד או הוצאה, ישירים או עקיפים, שייגרמו בקשר לאירוע או להסתמכות על מידע מהאתר.",
        ],
      },
      {
        title: "לפני שקונים",
        body: [
          "המחיר, התאריך, השעה ותנאי הכניסה הקובעים הם אלה שמופיעים באתר המוכר ברגע הרכישה. בדקו אותם שם לפני התשלום, וקראו את תנאי השימוש ומדיניות הביטולים של אותו אתר.",
          "מגבלות גיל, קוד לבוש, מדיניות כניסה ובטיחות באירוע נקבעות על ידי המארגן והמקום, והאחריות לעמידה בהן היא שלהם ושלכם.",
        ],
      },
      {
        title: "קישורים לאתרים אחרים",
        body: [
          "האתר מקשר לאתרים חיצוניים (כמו Go-Out, Eventer, Zygo ואחרים). אתרים אלה אינם בשליטתנו, ואיננו אחראים לתוכנם, לזמינותם, למדיניות שלהם או לעסקאות שתבצעו בהם.",
        ],
      },
      {
        title: "השירות ניתן כמו שהוא (AS IS)",
        body: [
          "האתר ניתן ללא תשלום וכמו שהוא, ללא כל התחייבות או מצג לגבי דיוק, שלמות, עדכניות או זמינות. ייתכנו תקלות, הפסקות שירות, טעויות ואירועים חסרים. ככל שהדין מתיר, האתר ובעליו לא יישאו בכל אחריות לנזק שייגרם משימוש באתר או מחוסר יכולת להשתמש בו.",
        ],
      },
      {
        title: "קניין רוחני",
        body: [
          "שמות האירועים, תיאוריהם וכרזותיהם שייכים למארגנים ולאתרים שפרסמו אותם, ומוצגים לצורך הפניה למקור בלבד. בעלי זכויות שמבקשים להסיר או לתקן תוכן מוזמנים לפנות אלינו, ונטפל בפנייה בהקדם.",
          "אין להעתיק את מאגר האירועים של האתר או לאסוף ממנו מידע באופן אוטומטי ובהיקף רחב בלי אישור.",
        ],
      },
      {
        title: "שינויים בתנאים",
        body: [
          "אנו רשאים לעדכן את התנאים מעת לעת. הנוסח המחייב הוא זה שמופיע בעמוד זה, ותאריך העדכון מופיע בתחתיתו.",
        ],
      },
      {
        title: "דין וסמכות שיפוט",
        body: [
          "על התנאים ועל השימוש באתר חלים דיני מדינת ישראל בלבד, וסמכות השיפוט הבלעדית נתונה לבתי המשפט המוסמכים בתל אביב-יפו.",
        ],
      },
    ] as Section[],
    contactTitle: "יצירת קשר",
    contact: "לשאלות, לבקשות להסרת תוכן או לכל פנייה:",
    email: "דוא\"ל",
    updated: "התנאים עודכנו לאחרונה בתאריך",
    privacy: "מדיניות הפרטיות",
    back: "לכל האירועים",
  },
  en: {
    title: "Terms of use",
    intro: (name: string) =>
      `Welcome to ${name} ("the site", "we"). Use of the site is subject to these terms. By browsing or using the site you accept them; if you do not, please do not use the site.`,
    sections: [
      {
        title: "What the site does",
        body: [
          "The site is a search and aggregation service only: it automatically collects information about events published and sold on ticketing sites and other third-party sites, and shows it in one place with a link to the original site.",
          "The site does not produce, organize or sell tickets for any event, and is not a party to any transaction between you and an event organizer or ticketing site.",
        ],
      },
      {
        title: "No responsibility for events, content or prices",
        body: ["The site and its owners are not responsible for, among other things:"],
        list: [
          "Whether events take place, cancellations, postponements, changes of date, place, lineup or any other detail.",
          "The content, descriptions, images and information shown on the site, which come from other sites and may be wrong, incomplete or out of date.",
          "Prices, service fees, price changes, ticket availability, free entry and entry times. The information is updated several times a day and may change at any moment.",
          "The purchase itself, payment, refunds, cancellations and customer service, which are solely the responsibility of the ticketing site and the organizer.",
          "Any damage, loss or expense, direct or indirect, arising in connection with an event or from relying on information on the site.",
        ],
      },
      {
        title: "Before you buy",
        body: [
          "The price, date, time and entry terms that count are those shown on the selling site at the moment of purchase. Check them there before paying, and read that site's terms and cancellation policy.",
          "Age limits, dress code, entry policy and safety at an event are set by the organizer and the venue, and complying with them is their responsibility and yours.",
        ],
      },
      {
        title: "Links to other sites",
        body: [
          "The site links to external sites (such as Go-Out, Eventer, Zygo and others). They are not under our control, and we are not responsible for their content, availability, policies or any transaction you make on them.",
        ],
      },
      {
        title: "Provided as is",
        body: [
          "The site is free and provided as is, with no warranty or representation of accuracy, completeness, timeliness or availability. There may be faults, outages, errors and missing events. To the extent permitted by law, the site and its owners bear no liability for any damage arising from using, or being unable to use, the site.",
        ],
      },
      {
        title: "Intellectual property",
        body: [
          "Event names, descriptions and posters belong to the organizers and the sites that published them, and are shown only to refer you to the source. Rights holders who want content removed or corrected are welcome to contact us, and we will handle it promptly.",
          "Do not copy the site's event database or harvest it automatically at scale without permission.",
        ],
      },
      {
        title: "Changes to these terms",
        body: ["We may update these terms from time to time. The binding version is the one on this page, dated at its foot."],
      },
      {
        title: "Governing law and jurisdiction",
        body: [
          "These terms and the use of the site are governed solely by the laws of the State of Israel, and the competent courts of Tel Aviv-Jaffa have exclusive jurisdiction.",
        ],
      },
    ] as Section[],
    contactTitle: "Contact",
    contact: "For questions, content removal requests or anything else:",
    email: "Email",
    updated: "These terms were last updated on",
    privacy: "Privacy policy",
    back: "All events",
  },
};

export default function TermsPage() {
  const { t: ui, lang } = useT();
  const t = TEXT[lang];
  useTitle(t.title);
  const site = useQuery({
    queryKey: ["site"],
    queryFn: () => fetch("/site").then((r) => r.json() as Promise<{ name: string; privacy: { email: string } }>),
  }).data;
  const email = site?.privacy.email;
  const updated = new Date(UPDATED + "T12:00:00Z").toLocaleDateString(ui.locale, { dateStyle: "long", timeZone: "UTC" });

  return (
    <>
      <Hero title={t.title} />
      <main className="wrap statement">
        <p>{t.intro(site?.name ?? "SPOTX")}</p>
        {t.sections.map((s) => (
          <section key={s.title}>
            <h2>{s.title}</h2>
            {s.body.map((p) => <p key={p}>{p}</p>)}
            {s.list && <ul>{s.list.map((x) => <li key={x}>{x}</li>)}</ul>}
          </section>
        ))}
        {email && (
          <>
            <h2>{t.contactTitle}</h2>
            <p>{t.contact}</p>
            <ul className="contact">
              <li>{t.email}: <a href={`mailto:${email}`} dir="ltr">{email}</a></li>
            </ul>
          </>
        )}
        <p className="muted">{t.updated} {updated}.</p>
        <p><Link to="/privacy">{t.privacy}</Link> · <Link to="/">{t.back}</Link></p>
      </main>
    </>
  );
}
