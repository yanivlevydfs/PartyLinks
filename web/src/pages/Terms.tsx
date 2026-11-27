// Terms of use (תנאי שימוש): SPOTX only aggregates listings sold on other sites, so the terms make
// clear it is not a party to any purchase and is not responsible for events, content or prices.
// The contact address is the privacy one (site.privacy in config) via GET /site.
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Hero, HiddenEmail, useT, useTitle } from "../ui";

const UPDATED = "2026-11-27";

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
          "האתר ובעליו אינם בעלי האירועים, המסיבות או המקומות, אינם בעלי הכרטיסים, ואינם סוכן, נציג, משווק או שלוח של מארגן, מקום או אתר כרטיסים כלשהו. כל האחריות לאירוע, לכרטיסים ולמידע עליהם היא של האתרים שמוכרים את הכרטיסים ושל המארגנים בלבד.",
          "הצגת אירוע באתר, מיקומו ברשימה או בדף הבית, וסימונו כ\"פופולרי\" או \"מומלץ\" אינם המלצה, אישור או התחייבות מצדנו לגבי האירוע, המארגן או אתר הכרטיסים.",
        ],
      },
      {
        title: "מידע שמסודר ומסווג באופן אוטומטי",
        body: [
          "את המידע שנאסף אנחנו מעבדים ומסדרים באופן אוטומטי, בלי בדיקה ידנית של כל אירוע, ולכן ייתכנו טעויות בכל אחד מאלה:",
        ],
        list: [
          "סיווג האירוע לקטגוריות ולז'אנרים, כולל ז'אנר מוזיקלי, סוג האירוע ואופיו.",
          "התאמה לגיל ולקהל יעד: סימון אירוע כמתאים לילדים, למשפחות, לנוער או למבוגרים בלבד. ההגבלות הקובעות הן של המארגן ואתר הכרטיסים.",
          "מחירים: הסורק מציג מחיר אחד או טווח מחירים, ואינו מבחין בהכרח בין מחיר לפי גיל, הנחת סטודנט, חייל, אזרח ותיק או מנוי, מחיר מוקדם, דמי שירות או מחיר בדלת. המחיר הנמוך המוצג אינו בהכרח המחיר שישולם.",
          "מקום, עיר, כתובת והנקודה במפה: הם מחושבים מהכתובת או משם המקום, ועלולים להיות לא מדויקים.",
          "איחוד אירועים: אותו אירוע שמופיע בכמה אתרים מוצג פעם אחת, ולפעמים שני אירועים שונים יאוחדו בטעות או שאירוע אחד יופיע פעמיים.",
          "אמנים, קישורים לאמנים, תמונות, טקסטים שמסכמים מידע (למשל דף אמן), סידור התיאור לפסקאות, תרגום, תחזית מזג אוויר ומוני צפיות ושמירות.",
        ],
      },
      {
        title: "תוכן ממומן",
        body: [
          "חלק מהאירועים מוצגים בתשלום, ומסומנים כ\"ממומן\". הקידום אינו המלצה שלנו, ומי שמקדם אירוע אחראי לו ולכל פרטיו כמו כל מארגן.",
        ],
      },
      {
        title: "אירועים למבוגרים וקטינים",
        body: [
          "האתר מציג גם אירועים שמיועדים למבוגרים בלבד (למשל מסיבות 18+, אירועים עם אלכוהול או תוכן מיני). השימוש באתר על ידי קטינים הוא באחריות הוריהם, וההחלטה אם להשתתף באירוע, ובדיקת התאמתו לגיל, הן של המשתתף, הוריו, המארגן ואתר הכרטיסים.",
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
        title: "חשבון משתמש, התראות ומיילים",
        body: [
          "החשבון אינו חובה: כל האתר פתוח לשימוש בלי הרשמה. מי שמתחבר עם Google או Facebook יכול לשמור אירועים, ליצור התראות ולבקש עדכון שבועי בדוא\"ל.",
        ],
        list: [
          "החשבון מיועד לבני 16 ומעלה, חשבון אחד לכל אדם, ולשימוש אישי בלבד.",
          "התראות ועדכונים נשלחים כמיטב יכולתנו (best effort): אירוע עלול שלא להופיע בהתראה, להגיע באיחור או להשתנות לאחר השליחה. הפרטים הקובעים הם אלה שבאתר הכרטיסים.",
          "מותר לנו להשעות או לחסום חשבון שמשמש לפגיעה באתר, לאיסוף מידע אוטומטי או בניגוד לתנאים אלה.",
          "אפשר למחוק את החשבון בכל עת בעמוד \"החשבון שלי\", ולהפסיק מיילים בקישור שבכל מייל. השימוש במידע מתואר במדיניות הפרטיות.",
        ],
      },
      {
        title: "אירועים שמשתמשים מפרסמים",
        body: [
          "משתמש מחובר יכול לפרסם אירוע בעמוד \"הוספת אירוע\". כל אירוע נבדק ומאושר על ידינו לפני שהוא מופיע באתר, ואנו רשאים לסרב לאירוע, לערוך את הצגתו או להסירו בכל עת ובלי לנמק.",
          "מי שמפרסם אירוע מצהיר ומתחייב כי:",
        ],
        list: [
          "הוא המארגן, המפיק או בעל המקום, או מורשה מטעמם, וכל הפרטים שמסר נכונים, מלאים ומעודכנים.",
          "יש לו את כל הזכויות בתוכן ובתמונות שהעלה, והם אינם מפרים זכויות יוצרים, סימני מסחר, פרטיות או זכות אחרת של אדם כלשהו.",
          "האירוע חוקי, מתקיים בפועל, ובעל כל הרישיונות, ההיתרים, הביטוחים ואישורי הבטיחות הנדרשים.",
          "האחריות לאירוע כולו היא שלו בלבד: קיומו, ביטולו או שינויו, הכניסה אליו ומדיניות הסלקציה, מחירים, החזרים ותשלומים, הבטיחות והאבטחה, איכות האירוע והתוכן, הקהל והאנשים הנוכחים בו, וכל מה שקורה בו.",
          "SPOTX ובעליו אינם מארגנים, מפיקים, מוכרים או מפקחים על האירוע, ואינם נושאים בכל אחריות כלפיו, כלפי המשתתפים בו או כלפי צד שלישי לכל נזק, הפסד, תביעה או הוצאה שייגרמו בקשר אליו.",
          "הוא ישפה את SPOTX ובעליו על כל נזק, הוצאה או תביעה (כולל שכר טרחת עורך דין) שייגרמו להם בשל האירוע, התוכן שפורסם או הפרת התנאים.",
          "הוא מעניק ל-SPOTX רישיון חינם להציג את פרטי האירוע ואת התמונות באתר, בשיתופים, בהתראות ובמיילים, כל עוד האירוע מופיע באתר.",
        ],
      },
      {
        title: "השירות ניתן כמו שהוא (AS IS)",
        body: [
          "האתר ניתן ללא תשלום וכמו שהוא, ללא כל התחייבות או מצג לגבי דיוק, שלמות, עדכניות או זמינות. ייתכנו תקלות, הפסקות שירות, טעויות ואירועים חסרים. ככל שהדין מתיר, האתר ובעליו לא יישאו בכל אחריות לנזק שייגרם משימוש באתר או מחוסר יכולת להשתמש בו.",
          "התנאים האלה חלים על כל ערוצי השירות: האתר, בוט הטלגרם, ההתראות והמיילים.",
        ],
      },
      {
        title: "דיווח על טעות או בקשת הסרה",
        body: [
          "מצאתם פרט שגוי, אירוע שבוטל, סיווג לא מתאים או תוכן שפוגע בזכויותיכם? כתבו לנו בכתובת שלמטה עם קישור לאירוע, ונתקן או נסיר אותו בהקדם. תיקון פרט באתר אינו מחליף את בדיקתו באתר המוכר.",
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
          "אם ייקבע שסעיף כלשהו בתנאים אינו תקף, שאר הסעיפים יעמדו בתוקפם. הימנעות שלנו מלהפעיל זכות אינה ויתור עליה.",
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
          "The site and its owners do not own the events, parties or venues, do not own the tickets, and are not an agent, representative, reseller or proxy of any organizer, venue or ticketing site. All responsibility for an event, its tickets and the information about them lies solely with the sites selling the tickets and the organizers.",
          "Showing an event on the site, its place in a list or on the home page, and marking it \"popular\" or \"recommended\" are not a recommendation, endorsement or undertaking by us about the event, the organizer or the ticketing site.",
        ],
      },
      {
        title: "Information sorted and classified automatically",
        body: [
          "We process and sort the collected information automatically, without checking each event by hand, so any of the following may be wrong:",
        ],
        list: [
          "The event's categories and genres, including its music genre, type and character.",
          "Age and audience: marking an event as suitable for children, families, teens or adults only. The organizer's and the ticketing site's restrictions are the ones that count.",
          "Prices: the site shows one price or a price range, and does not necessarily tell apart prices by age, student, soldier, senior or member discounts, early-bird prices, service fees or door prices. The lowest price shown is not necessarily the price you will pay.",
          "Venue, town, address and map pin: worked out from the address or the venue's name, and may be inaccurate.",
          "Merging: one event listed on several sites is shown once, and sometimes two different events are merged by mistake or one event appears twice.",
          "Artists, artist links, pictures, texts that summarize information (such as an artist page), splitting descriptions into paragraphs, translation, weather forecasts and view and save counts.",
        ],
      },
      {
        title: "Sponsored content",
        body: [
          "Some events are shown for payment and are marked \"Sponsored\". Promotion is not our recommendation, and whoever promotes an event is responsible for it and all its details like any organizer.",
        ],
      },
      {
        title: "Adult events and minors",
        body: [
          "The site also lists events for adults only (such as 18+ parties, events with alcohol or sexual content). Minors' use of the site is their parents' responsibility, and deciding whether to attend an event, and checking it suits one's age, is up to the attendee, their parents, the organizer and the ticketing site.",
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
        title: "Accounts, alerts and emails",
        body: [
          "An account is optional: the whole site works without signing up. If you sign in with Google or Facebook you can save events, create alerts and ask for a weekly email update.",
        ],
        list: [
          "Accounts are for people aged 16 and over, one account per person, for personal use only.",
          "Alerts and updates are sent on a best-effort basis: an event may be missed, arrive late or change after it was sent. The ticketing site's details are the ones that count.",
          "We may suspend or block an account used to harm the site, to collect data automatically, or against these terms.",
          "You can delete your account at any time on \"My account\", and stop emails with the link in every email. How we use the data is described in the privacy policy.",
        ],
      },
      {
        title: "Events posted by users",
        body: [
          "A signed-in user can post an event on \"Add an event\". We review and approve every event before it appears on the site, and we may refuse, edit the display of, or remove any event at any time without giving a reason.",
          "Whoever posts an event declares and undertakes that:",
        ],
        list: [
          "They are the organizer, producer or venue owner, or authorized on their behalf, and every detail they gave is true, complete and up to date.",
          "They hold all rights to the content and pictures they uploaded, which infringe no copyright, trademark, privacy or other right of anyone.",
          "The event is lawful, actually takes place, and holds every licence, permit, insurance and safety approval it needs.",
          "They alone are responsible for the whole event: whether it happens, is cancelled or changed, entry and door policy, prices, refunds and payments, safety and security, the quality of the event and its content, the audience and the people attending, and everything that happens there.",
          "SPOTX and its owners do not organize, produce, sell or supervise the event, and bear no responsibility toward it, its attendees or any third party for any damage, loss, claim or expense arising in connection with it.",
          "They will indemnify SPOTX and its owners for any damage, expense or claim (including legal fees) arising from the event, the posted content or a breach of these terms.",
          "They grant SPOTX a free licence to show the event's details and pictures on the site, in shares, alerts and emails, for as long as the event is listed.",
        ],
      },
      {
        title: "Provided as is",
        body: [
          "The site is free and provided as is, with no warranty or representation of accuracy, completeness, timeliness or availability. There may be faults, outages, errors and missing events. To the extent permitted by law, the site and its owners bear no liability for any damage arising from using, or being unable to use, the site.",
          "These terms apply to every channel of the service: the site, the Telegram bot, alerts and emails.",
        ],
      },
      {
        title: "Reporting a mistake or asking for removal",
        body: [
          "Found a wrong detail, a cancelled event, an unsuitable category or content that infringes your rights? Write to us at the address below with a link to the event, and we will correct or remove it promptly. A correction here does not replace checking the detail on the selling site.",
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
          "If any clause of these terms is held invalid, the rest remain in force. Our not enforcing a right is not a waiver of it.",
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
              <li>{t.email}: <HiddenEmail code={email} /></li>
            </ul>
          </>
        )}
        <p className="muted">{t.updated} {updated}.</p>
        <p><Link to="/privacy">{t.privacy}</Link> · <Link to="/">{t.back}</Link></p>
      </main>
    </>
  );
}
