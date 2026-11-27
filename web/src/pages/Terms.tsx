// Terms of use (תנאי שימוש): SPOTX only aggregates listings sold on other sites, so the terms make
// clear it is not a party to any purchase and is not responsible for events, content or prices.
// Events users post (/submit) go live only after an admin approves them, and their ticket links are
// the poster's own: no ticket is issued or sold on SPOTX.
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
      `ברוכים הבאים ל${name} ("האתר", "השירות", "אנחנו"). השימוש באתר כפוף לתנאים אלה ולמדיניות הפרטיות, שהיא חלק מהם. גלישה באתר או שימוש בו מהווים הסכמה מלאה לתנאים; אם אינכם מסכימים להם, אל תשתמשו באתר. התנאים מנוסחים בלשון רבים ומתייחסים לכל המגדרים.`,
    sections: [
      {
        title: "הגדרות",
        body: [],
        list: [
          "\"בעלי האתר\": מפעילי SPOTX, בעליו, עובדיו, שותפיו ומי מטעמם.",
          "\"מארגן\": מי שמפיק, מארגן או מקיים אירוע, וכן המקום שבו הוא מתקיים.",
          "\"אתר מוכר\": כל אתר, אפליקציה או גורם של צד שלישי שמוכר או מנפיק כרטיסים, או שמפרסם את האירוע במקור (כמו Go-Out, Eventer, Zygo ואחרים).",
          "\"מפרסם\": משתמש שמפרסם אירוע בעצמו באתר.",
        ],
      },
      {
        title: "מה האתר עושה, ומה הוא לא עושה",
        body: [
          "האתר הוא מנוע חיפוש ואיסוף מידע בלבד: הוא אוסף באופן אוטומטי מידע על אירועים שמתפרסמים ונמכרים באתרים מוכרים ובאתרים אחרים של צדדים שלישיים, ומציג אותו במקום אחד עם קישור למקור.",
          "האתר אינו מפיק, אינו מארגן, אינו מוכר, אינו מנפיק ואינו מחזיק כרטיסים לאף אירוע. אין באתר רכישה, תשלום או סליקה, וכסף אינו עובר דרכנו. האתר אינו צד לעסקה כלשהי בינכם לבין מארגן או אתר מוכר.",
          "בעלי האתר אינם בעלי האירועים, המסיבות או המקומות, אינם בעלי הכרטיסים, ואינם סוכן, נציג, משווק, מתווך או שלוח של מארגן, מקום או אתר מוכר כלשהו. כל האחריות לאירוע, לכרטיסים ולמידע עליהם היא של האתרים המוכרים ושל המארגנים בלבד.",
          "הצגת אירוע באתר, מיקומו ברשימה או בדף הבית, וסימונו כ\"פופולרי\" או \"מומלץ\" אינם המלצה, אישור, בדיקה או התחייבות מצדנו לגבי האירוע, המארגן או האתר המוכר.",
        ],
      },
      {
        title: "אין אחריות לאירועים, לתוכן ולמחירים",
        body: ["האתר ובעליו אינם אחראים, בין היתר, ל:"],
        list: [
          "קיום האירועים, ביטולם, דחייתם, שינוי מועדם, מיקומם, הליינאפ או כל פרט אחר בהם.",
          "התוכן, התיאורים, התמונות והמידע המופיעים באתר, שמקורם באתרים אחרים ועשויים להיות שגויים, חלקיים או לא מעודכנים.",
          "המחירים, דמי השירות, שינויי מחיר, זמינות כרטיסים, כניסה חופשית ושעות הכניסה. המידע מתעדכן כמה פעמים ביום ועלול להשתנות בכל רגע.",
          "הרכישה עצמה, התשלום, הנפקת הכרטיס, ההחזרים, הביטולים ושירות הלקוחות, שהם באחריות האתר המוכר והמארגן בלבד.",
          "מה שקורה באירוע עצמו: הבטיחות, האבטחה, הבריאות, הכניסה והסלקציה, אלכוהול וחומרים אחרים, פציעה, אובדן או נזק לרכוש, הגעה וחנייה, איכות האירוע וכל התנהגות של המארגן, הצוות או המשתתפים. ההשתתפות באירוע היא על אחריותכם.",
          "כל נזק, הפסד, אובדן רווח או הוצאה, ישירים או עקיפים, שייגרמו בקשר לאירוע, לרכישה או להסתמכות על מידע מהאתר.",
        ],
      },
      {
        title: "מידע שמסודר ומסווג באופן אוטומטי",
        body: [
          "את המידע שנאסף אנחנו מעבדים ומסדרים באופן אוטומטי, בלי בדיקה ידנית של כל אירוע. לכן עלולות להיות טעויות בכל אחד מאלה, והאתר ובעליו אינם אחראים להן:",
        ],
        list: [
          "סיווג האירוע לקטגוריות ולז'אנרים, כולל ז'אנר מוזיקלי, סוג האירוע ואופיו.",
          "התאמה לגיל ולקהל יעד: סימון אירוע כמתאים לילדים, למשפחות, לנוער או למבוגרים בלבד. ההגבלות הקובעות הן של המארגן והאתר המוכר.",
          "מחירים: האתר מציג מחיר אחד או טווח מחירים, ואינו מבחין בהכרח בין מחיר לפי גיל, הנחת סטודנט, חייל, אזרח ותיק או מנוי, מחיר מוקדם, דמי שירות או מחיר בדלת. המחיר הנמוך המוצג אינו בהכרח המחיר שתשלמו.",
          "מקום, עיר, כתובת והנקודה במפה: הם מחושבים מהכתובת או משם המקום, ועלולים להיות לא מדויקים.",
          "איחוד אירועים: אותו אירוע שמופיע בכמה אתרים מוצג פעם אחת, ולפעמים שני אירועים שונים יאוחדו בטעות או שאירוע אחד יופיע פעמיים.",
          "סדר האירועים, העימוד והחלוקה לרשימות ולדפים.",
          "אמנים, קישורים לאמנים, תמונות, טקסטים שמסכמים מידע (למשל דף אמן), סידור התיאור לפסקאות, תרגום, תחזית מזג אוויר ומוני צפיות ושמירות.",
        ],
      },
      {
        title: "לפני שקונים",
        body: [
          "המחיר, התאריך, השעה, המקום ותנאי הכניסה הקובעים הם אלה שמופיעים באתר המוכר ברגע הרכישה. בדקו אותם שם לפני התשלום, וקראו את תנאי השימוש ומדיניות הביטולים של אותו אתר.",
          "מגבלות גיל, קוד לבוש, מדיניות כניסה ובטיחות באירוע נקבעות על ידי המארגן והמקום, והאחריות לעמידה בהן היא שלהם ושלכם.",
        ],
      },
      {
        title: "קישורים לאתרים אחרים",
        body: [
          "האתר מקשר לאתרים חיצוניים. אתרים אלה אינם בשליטתנו, ואיננו אחראים לתוכנם, לזמינותם, לאבטחתם, למדיניות שלהם או לעסקאות שתבצעו בהם. מעבר לאתר חיצוני הוא על אחריותכם.",
        ],
      },
      {
        title: "תוכן ממומן",
        body: [
          "חלק מהאירועים מוצגים בתשלום ומסומנים כ\"ממומן\". הקידום אינו המלצה שלנו, ומי שמקדם אירוע אחראי לו ולכל פרטיו כמו כל מארגן. בקשת קידום אינה מחייבת אותנו לקבל אותה, ואיננו מתחייבים לחשיפה, לכמות צפיות או לתוצאות כלשהן.",
        ],
      },
      {
        title: "אירועים למבוגרים וקטינים",
        body: [
          "האתר מציג גם אירועים שמיועדים למבוגרים בלבד (למשל מסיבות 18+, אירועים עם אלכוהול או תוכן מיני). השימוש באתר על ידי קטינים הוא באחריות הוריהם. ההחלטה אם להשתתף באירוע, ובדיקת התאמתו לגיל, הן של המשתתף, הוריו, המארגן והאתר המוכר.",
        ],
      },
      {
        title: "פרסום מסיבה או אירוע באופן עצמאי",
        body: [
          "משתמש מחובר יכול לפרסם מסיבה או אירוע בעצמו בעמוד \"הוספת אירוע\". כך זה עובד:",
        ],
        list: [
          "אישור מנהל: כל אירוע, וכל עריכה שלו, נשמרים כממתינים לבדיקה, ומופיעים באתר רק אחרי שמנהל האתר אישר אותם. ההחלטה אם לאשר, לדחות, להשהות, לערוך את התצוגה או להסיר היא שלנו בלבד, בכל עת ובלי חובת הנמקה. איננו אחראים לעיכוב באישור, לדחייה או להסרה.",
          "אישור אינו בדיקה: אישור האירוע אינו בדיקה או אימות של פרטיו, של חוקיותו, של המפרסם או של קישורי הכרטיסים, ואינו המלצה עליו.",
          "המכירה לא מתבצעת אצלנו: האתר אינו יוצר, אינו מנפיק ואינו מוכר כרטיסים לאירוע שפורסם, ואינו גובה תשלום. המפרסם מוסיף בעצמו את הקישורים לרכישה, והרכישה כולה, כולל התשלום, הנפקת הכרטיס, ההחזרים והביטולים, מתבצעת באתר צד שלישי שהמפרסם בחר, בינו לבין הרוכש בלבד.",
          "המפרסם לבדו אחראי לקישורים שהוסיף: שהם נכונים, פעילים ובטוחים, שהם מובילים למכירה אמיתית של כרטיסים לאותו אירוע, ולכל מה שקורה באתר שאליו הם מובילים.",
          "אירוע שפורסם עובר את אותו עיבוד אוטומטי כמו כל אירוע באתר (סיווג, מפה, איחוד עם פרסומים אחרים של אותו אירוע), והסעיף \"מידע שמסודר ומסווג באופן אוטומטי\" חל גם עליו.",
        ],
      },
      {
        title: "הצהרות והתחייבויות המפרסם",
        body: ["מי שמפרסם אירוע מצהיר ומתחייב כי:"],
        list: [
          "הוא המארגן, המפיק או בעל המקום, או מורשה מטעמם, וכל הפרטים שמסר נכונים, מלאים ומעודכנים, והוא יעדכן או יסיר אותם מיד כשהם משתנים (כולל ביטול או דחייה).",
          "יש לו את כל הזכויות בתוכן ובתמונות שהעלה, והם אינם מפרים זכויות יוצרים, סימני מסחר, פרטיות, שם טוב או זכות אחרת של אדם כלשהו.",
          "האירוע חוקי, מתקיים בפועל, ובעל כל הרישיונות, ההיתרים, הביטוחים ואישורי הבטיחות הנדרשים, והתוכן אינו פוגעני, מטעה או בלתי חוקי.",
          "האחריות לאירוע כולו היא שלו בלבד: קיומו, ביטולו או שינויו, הכניסה אליו ומדיניות הסלקציה, מגבלות הגיל, המחירים, ההחזרים והתשלומים, הבטיחות והאבטחה, איכות האירוע והתוכן, הקהל והאנשים הנוכחים בו, וכל מה שקורה בו.",
          "SPOTX ובעליו אינם מארגנים, מפיקים, מוכרים או מפקחים על האירוע, ואינם נושאים בכל אחריות כלפיו, כלפי המשתתפים בו או כלפי צד שלישי לכל נזק, הפסד, תביעה או הוצאה שייגרמו בקשר אליו.",
          "הוא ישפה את SPOTX ובעליו מיד עם דרישה על כל נזק, הוצאה או תביעה (כולל שכר טרחת עורך דין) שייגרמו להם בשל האירוע, התוכן שפורסם, הקישורים שהוסיף או הפרת התנאים.",
          "הוא מעניק ל-SPOTX רישיון חינם להציג את פרטי האירוע ואת התמונות באתר, בשיתופים, ברשתות, בהתראות ובמיילים, כל עוד האירוע מופיע באתר.",
        ],
      },
      {
        title: "חשבון משתמש, התראות ומיילים",
        body: [
          "החשבון אינו חובה: כל האתר פתוח לשימוש בלי הרשמה. מי שמתחבר עם Google או Facebook יכול לשמור אירועים, ליצור התראות, לבקש עדכון שבועי בדוא\"ל ולפרסם אירועים.",
        ],
        list: [
          "החשבון מיועד לבני 16 ומעלה, חשבון אחד לכל אדם, ולשימוש אישי בלבד. אתם אחראים לכל פעולה שנעשית בחשבון שלכם.",
          "התראות ועדכונים נשלחים כמיטב יכולתנו (best effort): אירוע עלול שלא להופיע בהתראה, להגיע באיחור או להשתנות לאחר השליחה. הפרטים הקובעים הם אלה שבאתר המוכר.",
          "מותר לנו להשעות, לחסום או למחוק חשבון שמשמש לפגיעה באתר, לאיסוף מידע אוטומטי, לפרסום כוזב או בניגוד לתנאים אלה, ולהסיר את האירועים שפרסם.",
          "אפשר למחוק את החשבון בכל עת בעמוד \"החשבון שלי\", ולהפסיק מיילים בקישור שבכל מייל. השימוש במידע מתואר במדיניות הפרטיות.",
        ],
      },
      {
        title: "שימוש אסור",
        body: ["אסור, בין היתר:"],
        list: [
          "להעתיק את מאגר האירועים, לסרוק את האתר או לאסוף ממנו מידע באופן אוטומטי ובהיקף רחב בלי אישור בכתב.",
          "לפגוע באתר, בשרתים או באבטחה, לעקוף מגבלות או חסימות, או להעמיס על השירות.",
          "לפרסם אירוע כוזב, מטעה או בלתי חוקי, להתחזות לאדם או לגוף אחר, או להוסיף קישורים להונאה, לפישינג או לתוכנה זדונית.",
          "להשתמש באתר בניגוד לדין או לתנאים אלה.",
        ],
      },
      {
        title: "השירות ניתן כמו שהוא (AS IS)",
        body: [
          "האתר ניתן ללא תשלום וכמו שהוא, ללא כל התחייבות או מצג לגבי דיוק, שלמות, עדכניות, התאמה למטרה מסוימת או זמינות. ייתכנו תקלות, הפסקות שירות, טעויות ואירועים חסרים, ואנו רשאים לשנות, להשהות או להפסיק את השירות או כל חלק ממנו בכל עת.",
          "ככל שהדין מתיר, האתר ובעליו לא יישאו בכל אחריות לנזק, ישיר או עקיף, שייגרם משימוש באתר או מחוסר יכולת להשתמש בו. אם בכל זאת תוטל עלינו אחריות כלשהי, היא לא תעלה על 100 ₪ בסך הכל.",
          "התנאים האלה חלים על כל ערוצי השירות: האתר, בוט הטלגרם, ההתראות והמיילים.",
        ],
      },
      {
        title: "שיפוי",
        body: [
          "אתם מתחייבים לשפות את SPOTX ובעליו על כל נזק, הוצאה או תביעה (כולל שכר טרחת עורך דין) שייגרמו להם בשל שימוש שלכם באתר בניגוד לתנאים אלה או לדין.",
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
          "עיצוב האתר, הקוד, המאגר, הסיווגים והטקסטים שכתבנו שייכים ל-SPOTX.",
        ],
      },
      {
        title: "שינויים בתנאים",
        body: [
          "אנו רשאים לעדכן את התנאים מעת לעת. הנוסח המחייב הוא זה שמופיע בעמוד זה, ותאריך העדכון מופיע בתחתיתו. המשך השימוש באתר אחרי עדכון מהווה הסכמה לנוסח המעודכן.",
        ],
      },
      {
        title: "דין, סמכות שיפוט וכללי",
        body: [
          "על התנאים ועל השימוש באתר חלים דיני מדינת ישראל בלבד, וסמכות השיפוט הבלעדית נתונה לבתי המשפט המוסמכים בתל אביב-יפו.",
          "אם ייקבע שסעיף כלשהו בתנאים אינו תקף, שאר הסעיפים יעמדו בתוקפם. הימנעות שלנו מלהפעיל זכות אינה ויתור עליה. במקרה של סתירה בין הנוסח העברי לנוסח האנגלי, הנוסח העברי קובע.",
        ],
      },
    ] as Section[],
    contactTitle: "יצירת קשר",
    contact: "לשאלות, לדיווח על טעות, לבקשות להסרת תוכן או לכל פנייה:",
    email: "דוא\"ל",
    updated: "התנאים עודכנו לאחרונה בתאריך",
    privacy: "מדיניות הפרטיות",
    back: "לכל האירועים",
  },
  en: {
    title: "Terms of use",
    intro: (name: string) =>
      `Welcome to ${name} ("the site", "the service", "we"). Use of the site is subject to these terms and to the privacy policy, which is part of them. By browsing or using the site you fully accept them; if you do not, please do not use the site.`,
    sections: [
      {
        title: "Definitions",
        body: [],
        list: [
          "\"The site's owners\": the operators of SPOTX, its owners, employees, partners and anyone acting for them.",
          "\"Organizer\": whoever produces, organizes or holds an event, and the venue where it takes place.",
          "\"Selling site\": any third-party site, app or party that sells or issues tickets, or that originally publishes the event (such as Go-Out, Eventer, Zygo and others).",
          "\"Poster\": a user who posts an event on the site themselves.",
        ],
      },
      {
        title: "What the site does, and what it does not",
        body: [
          "The site is a search and aggregation service only: it automatically collects information about events published and sold on selling sites and other third-party sites, and shows it in one place with a link to the source.",
          "The site does not produce, organize, sell, issue or hold tickets for any event. There is no purchase, payment or card processing on the site, and no money passes through us. The site is not a party to any transaction between you and an organizer or a selling site.",
          "The site's owners do not own the events, parties or venues, do not own the tickets, and are not an agent, representative, reseller, broker or proxy of any organizer, venue or selling site. All responsibility for an event, its tickets and the information about them lies solely with the selling sites and the organizers.",
          "Showing an event on the site, its place in a list or on the home page, and marking it \"popular\" or \"recommended\" are not a recommendation, endorsement, check or undertaking by us about the event, the organizer or the selling site.",
        ],
      },
      {
        title: "No responsibility for events, content or prices",
        body: ["The site and its owners are not responsible for, among other things:"],
        list: [
          "Whether events take place, cancellations, postponements, changes of date, place, lineup or any other detail.",
          "The content, descriptions, images and information shown on the site, which come from other sites and may be wrong, incomplete or out of date.",
          "Prices, service fees, price changes, ticket availability, free entry and entry times. The information is updated several times a day and may change at any moment.",
          "The purchase itself, payment, issuing the ticket, refunds, cancellations and customer service, which are solely the responsibility of the selling site and the organizer.",
          "What happens at the event itself: safety, security, health, entry and door policy, alcohol and other substances, injury, loss of or damage to property, getting there and parking, the quality of the event and any conduct of the organizer, staff or attendees. You attend an event at your own risk.",
          "Any damage, loss, loss of profit or expense, direct or indirect, arising in connection with an event, a purchase or relying on information on the site.",
        ],
      },
      {
        title: "Information sorted and classified automatically",
        body: [
          "We process and sort the collected information automatically, without checking each event by hand. Any of the following may therefore be wrong, and the site and its owners are not responsible for it:",
        ],
        list: [
          "The event's categories and genres, including its music genre, type and character.",
          "Age and audience: marking an event as suitable for children, families, teens or adults only. The organizer's and the selling site's restrictions are the ones that count.",
          "Prices: the site shows one price or a price range, and does not necessarily tell apart prices by age, student, soldier, senior or member discounts, early-bird prices, service fees or door prices. The lowest price shown is not necessarily the price you will pay.",
          "Venue, town, address and map pin: worked out from the address or the venue's name, and may be inaccurate.",
          "Merging: one event listed on several sites is shown once, and sometimes two different events are merged by mistake or one event appears twice.",
          "The order of events, paging and how they are split into lists and pages.",
          "Artists, artist links, pictures, texts that summarize information (such as an artist page), splitting descriptions into paragraphs, translation, weather forecasts and view and save counts.",
        ],
      },
      {
        title: "Before you buy",
        body: [
          "The price, date, time, place and entry terms that count are those shown on the selling site at the moment of purchase. Check them there before paying, and read that site's terms and cancellation policy.",
          "Age limits, dress code, entry policy and safety at an event are set by the organizer and the venue, and complying with them is their responsibility and yours.",
        ],
      },
      {
        title: "Links to other sites",
        body: [
          "The site links to external sites. They are not under our control, and we are not responsible for their content, availability, security, policies or any transaction you make on them. Going to an external site is at your own risk.",
        ],
      },
      {
        title: "Sponsored content",
        body: [
          "Some events are shown for payment and are marked \"Sponsored\". Promotion is not our recommendation, and whoever promotes an event is responsible for it and all its details like any organizer. A promotion request does not oblige us to accept it, and we promise no exposure, number of views or any result.",
        ],
      },
      {
        title: "Adult events and minors",
        body: [
          "The site also lists events for adults only (such as 18+ parties, events with alcohol or sexual content). Minors' use of the site is their parents' responsibility. Deciding whether to attend an event, and checking it suits one's age, is up to the attendee, their parents, the organizer and the selling site.",
        ],
      },
      {
        title: "Posting a party or an event yourself",
        body: ["A signed-in user can post a party or an event on \"Add an event\". This is how it works:"],
        list: [
          "Admin approval: every event, and every edit of it, is kept pending review and appears on the site only after a site admin approves it. Whether to approve, reject, suspend, edit the display of or remove it is our decision alone, at any time and without giving a reason. We are not responsible for a delay in approval, a rejection or a removal.",
          "Approval is not a check: approving an event does not check or verify its details, its lawfulness, the poster or its ticket links, and is not a recommendation of it.",
          "The sale does not happen here: the site does not create, issue or sell tickets for a posted event, and collects no payment. The poster adds the purchase links themselves, and the whole purchase, including payment, issuing the ticket, refunds and cancellations, takes place on a third-party site the poster chose, solely between the poster and the buyer.",
          "The poster alone is responsible for the links they added: that they are correct, working and safe, that they lead to a genuine sale of tickets for that event, and for everything that happens on the site they lead to.",
          "A posted event goes through the same automatic processing as every event on the site (classification, map, merging with other listings of the same event), and the section \"Information sorted and classified automatically\" applies to it too.",
        ],
      },
      {
        title: "The poster's declarations and undertakings",
        body: ["Whoever posts an event declares and undertakes that:"],
        list: [
          "They are the organizer, producer or venue owner, or authorized on their behalf, every detail they gave is true, complete and up to date, and they will update or remove it as soon as it changes (including a cancellation or postponement).",
          "They hold all rights to the content and pictures they uploaded, which infringe no copyright, trademark, privacy, reputation or other right of anyone.",
          "The event is lawful, actually takes place, and holds every licence, permit, insurance and safety approval it needs, and the content is not offensive, misleading or unlawful.",
          "They alone are responsible for the whole event: whether it happens, is cancelled or changed, entry and door policy, age limits, prices, refunds and payments, safety and security, the quality of the event and its content, the audience and the people attending, and everything that happens there.",
          "SPOTX and its owners do not organize, produce, sell or supervise the event, and bear no responsibility toward it, its attendees or any third party for any damage, loss, claim or expense arising in connection with it.",
          "They will indemnify SPOTX and its owners on demand for any damage, expense or claim (including legal fees) arising from the event, the posted content, the links they added or a breach of these terms.",
          "They grant SPOTX a free licence to show the event's details and pictures on the site, in shares, on social networks, in alerts and in emails, for as long as the event is listed.",
        ],
      },
      {
        title: "Accounts, alerts and emails",
        body: [
          "An account is optional: the whole site works without signing up. If you sign in with Google or Facebook you can save events, create alerts, ask for a weekly email update and post events.",
        ],
        list: [
          "Accounts are for people aged 16 and over, one account per person, for personal use only. You are responsible for everything done with your account.",
          "Alerts and updates are sent on a best-effort basis: an event may be missed, arrive late or change after it was sent. The selling site's details are the ones that count.",
          "We may suspend, block or delete an account used to harm the site, to collect data automatically, to post false content or against these terms, and remove the events it posted.",
          "You can delete your account at any time on \"My account\", and stop emails with the link in every email. How we use the data is described in the privacy policy.",
        ],
      },
      {
        title: "Prohibited use",
        body: ["Among other things, you may not:"],
        list: [
          "Copy the event database, crawl the site or harvest it automatically at scale without written permission.",
          "Harm the site, its servers or its security, get around limits or blocks, or overload the service.",
          "Post a false, misleading or unlawful event, impersonate a person or body, or add links to fraud, phishing or malware.",
          "Use the site against the law or these terms.",
        ],
      },
      {
        title: "Provided as is",
        body: [
          "The site is free and provided as is, with no warranty or representation of accuracy, completeness, timeliness, fitness for a purpose or availability. There may be faults, outages, errors and missing events, and we may change, suspend or stop the service or any part of it at any time.",
          "To the extent permitted by law, the site and its owners bear no liability for any damage, direct or indirect, arising from using, or being unable to use, the site. If any liability is nevertheless imposed on us, it will not exceed ILS 100 in total.",
          "These terms apply to every channel of the service: the site, the Telegram bot, alerts and emails.",
        ],
      },
      {
        title: "Indemnity",
        body: [
          "You undertake to indemnify SPOTX and its owners for any damage, expense or claim (including legal fees) arising from your use of the site against these terms or the law.",
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
          "The site's design, code, database, classifications and the texts we wrote belong to SPOTX.",
        ],
      },
      {
        title: "Changes to these terms",
        body: [
          "We may update these terms from time to time. The binding version is the one on this page, dated at its foot. Using the site after an update means you accept the updated version.",
        ],
      },
      {
        title: "Governing law, jurisdiction and general",
        body: [
          "These terms and the use of the site are governed solely by the laws of the State of Israel, and the competent courts of Tel Aviv-Jaffa have exclusive jurisdiction.",
          "If any clause of these terms is held invalid, the rest remain in force. Our not enforcing a right is not a waiver of it. If the Hebrew and English versions differ, the Hebrew version prevails.",
        ],
      },
    ] as Section[],
    contactTitle: "Contact",
    contact: "For questions, reporting a mistake, content removal requests or anything else:",
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
