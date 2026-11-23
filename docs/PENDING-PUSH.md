# PartyLinks: רשימת משימות

עודכן: 23.11.2026, 11:20 UTC

## ✅ בוצע

- master תוקן: `config/settings.yaml` נטען שוב (`feef4a9`), נדחף ל-master ב-22.11 (`602116d`).
- **`/categories`**: נפל ב-500 כל כמה דקות כל 22.11 (`IdleInTransactionSessionTimeout`). תוקן (`104a8af`): כל הקריאות בטרנזקציה קצרה אחת, הספירה אחריה. מאז אין שגיאות.
- **503 על `/e/`** (`defdd21`): כל עמוד אירוע סרק את כל 217 אלף האירועים (`venue_name ILIKE`). עכשיו `lower(venue_name)` עם האינדקס `ix_events_venue_name_lower` (נבנה ב-CONCURRENTLY, VALID, 2.8MB): 0.05ms במקום 112–390ms.
- **503 על `/venue/`** (`c43d210`, `5965808`): הפרופילים נשמרים בזיכרון 5 דקות, טבלת האיותים נבנית פעם אחת. 10–26ms לעמוד במקום 0.9–2.9 שניות.
- **Statement timeout של העובד** (`1eca4d3`, `1c6c433`): חיפוש התמונות בהפעלה וה-regroup קוראים את הטבלה בעמודים (`yield_per`), כך שעובד עמוס לא נתקע על 120 שניות.
- **מיקומים** (`83314ce`, `d048c01`, `4244919`): 36 פינים למקומות (~230 אירועים), ושני האולמות של matnastc. תוקן באג ב-`geo._bad` שדחה פינים נכונים כשבטקסט הופיעה מילת מקום (424 אירועים). שם רחוב כבר לא נחשב לעיר (Smarticket, basalon), ואולמות "placeholder" לא נחשבים מקום.
- **TikTik** (`c5435a1`): הקטלוג עבר את 40 העמודים (4,114 מוצרים), וכ-100 המוצרים החדשים ביותר לא נקראו מאז 19.11. המגבלה עכשיו 80.
- **סעיף 2**: 11 מקורות חדשים בייצור, עם קטגוריות, WebP, cron משלהם, `sourceNames.json` ו-`Stores.tsx` (פירוט בטבלה).
- **סעיף 3**: 26 קופות Smarticket חדשות (פירוט בסעיף 3).
- **דיסק ב-Railway** (23.11): הנפח גדל מאז 19.11 בקצב של 1–3.7GB ליום, ועמד על 17GB מתוך 46GB. נבדק: אין קבצים יתומים (ה-sweep מצא 0), וה-retention עובד (נשארו 11 אירועים שפג תוקפם). הגידול נובע מהקטלוג העולמי (DICE, Eventbrite, Skiddle, Xceed): 225 אלף אירועים ו-129 אלף תמונות. מה שתוקן:
  - **IndexNow** (`4c45250`): `indexnow-sent.txt` שמר כל URL שנשלח אי פעם. עכשיו נשארים בו רק URLs שעדיין במפת האתר. ירד מ-113MB ל-18MB (704,678 שורות נמחקו).
  - **תמונת עמוד האירוע ברוחב 960 במקום 1280** (`c41c7a8`): העותקים ברוחב 1280 תפסו 11.2GB. בכל הפעלה, עבודת רקע מקטינה אותם ל-960 ומוחקת את הישנים. היא ממשיכה מהנקודה שבה עצרה. עד שהעותק מוקטן, מוגש קובץ ה-1280, וכתובות `/1280.webp` ישנות מגישות את ה-960. העותק החדש קטן בכ-27%. הצפי: הדיסק יירד לכ-13GB.
  - **אירועים שהוסרו מהאתר שלהם** (`c41c7a8`, `retention.keep_unlisted_days: 14`): אירוע שריצה מלאה כבר לא מוצאת נמחק, עם התמונות, 14 יום אחרי שנראה לאחרונה. בריצה הראשונה ירדו כ-3,684 אירועים.
  - **הוחלט: כל התמונות נשמרות אצלנו**, גם של אירועים רחוקים. לא מציגים תמונות ישירות מאתרי הכרטיסים.

## 1. לבדוק בפרודקשן

- [x] הדיפלוי עבר (`1c6c433`, SUCCESS).
- [x] **mugics**: 278 הופעות קרובות. לכולן תאריך, פין ולינק.
- [x] קופות Smarticket מ-22.11: mvhr 4, pastoral 3, migvanim 43, matnastc 11, kikar-bialik 14, mofa 16. השלוש הריקות (pnay, betgabriel, docaviv) הוסרו (`b1d2441`).
  - [x] **matnastc**: רק לאירוע 1 מתוך 11 היה פין, כי שני האולמות (מרכז אומנויות, אולם מפעל הפיס) לא היו מוכרים. נוספו פינים (`4244919`, ממתין לדחיפה).
  - [x] **pastoral**: כל 3 האירועים קיימים גם ב-ogo, וה-dedup ממזג. הקופה לא מוסיפה אירועים, אבל גם לא מפריעה.
- [x] 11 המקורות החדשים רצו בייצור (23.11, 01:26 UTC): glatticket 59, hamesila 7, jerusalemarts 15, yadvashem 145, havanaclub 6, beithagefen 77, newacropolis 8, parties247 71, ticket4u 363, salsavida 237, arthurmurray 1.
- [ ] **קופות Smarticket החדשות (סעיף 3)**: עוד אין להן אירועים במסד. ריצת smarticket של 04:14 UTC מחכה בתור, כי כל 8 המקומות תפוסים בריצות ארוכות (התור בפיגור של כ-2.5 שעות). לבדוק אחרי הריצה.
- [ ] **cinematheque_tlv**: להסיר את Soundtrack TLV 2026 מ-`festivals` אחרי 28.11.
- [ ] **הקטנת התמונות ל-960**: לוודא שהסתיימה. בלוג צריכה להופיע השורה `artwork copies: done ... shrunk to 960 wide`. בדיסק לא אמורים להישאר קבצי `*.1280.webp`, חוץ מבודדים שלא הצליחו להיות מוקטנים. נפח הדיסק אמור להיות כ-13GB. ב-11:06 UTC הוקטנו 25 אלף מתוך 99 אלף.

## ⏳ ממתין לדחיפה

- אין. `c5435a1` (TikTik) ו-`4244919` (פינים ל-matnastc) כבר ב-master. הדחיפה האחרונה: `7219c92` (23.11, 11:15 UTC).

## ⚠️ נמצא בדרך, להחלטה

- **חוות בוטים** (23.11, 04:00–04:36 UTC): בערך חצי מהתעבורה הגיעה מדפדפנים ישנים (Chrome 118–121, Firefox 120/121) מ-575 כתובות IP שונות, רובן ביתיות. התעבורה קפצה מ-123 ל-~550 בקשות לדקה, והיו התפרצויות קצרות של 503 (02:30, 04:09, 04:29–04:33: ~17). הבוטים מריצים את כל האתר, כולל `/go`, ולכן גם מנפחים את ספירת הקליקים. הגבלה לפי IP לא תופסת אותם. אפשרויות: (א) לחסום Chrome/Firefox מתחת לגרסה 125 כמו `blocked_bots`; (ב) לא לספור קליקים של `/go` מהם; (ג) Cloudflare/challenge. לא בוצע כלום עד שתחליט.
  | חלון (UTC) | חלק מכל הבקשות | חלק מקליקי `/go` | 503 |
  |---|---|---|---|
  | 04:20–04:38 | 63% | 22% (63 מתוך 278) | 26 |
  | 04:43–05:08 | 29% | 16% (84 מתוך 503) | 0 |
  | 05:08–05:56 | 41% | 15% (67 מתוך 423) | 1 |
- **ticket4u**: ה-API של Eventim נותן `location.city = "חיפה והצפון"` (אזור), וזה נשמר כעיר חיפה. אובחן, לא תוקן.
- **תור הריצות**: אחרי דיפלוי, ובזמן ריצות ארוכות (ogo, makore, eventer, eventbrite), מקורות מחכים שעות למקום פנוי. גם ריצה שסיימה לקרוא נשארת ב-running עד שה-regroup וה-geocode שלה מסתיימים.
- **ticket4u**: עמודי האירועים נחסמים דרך כל ה-proxies ("access denied by the site"). הריצות מסתיימות partial, אבל נתוני הרשימה נשמרים (363 אירועים, אף אחד לא חסר).
- **holon** מושהה: הבדיקה שלו ב-02:38 נכשלה ב-ProxyError 500 דרך כל ה-proxies, והאתר עדיין נראה למטה.
- **regroup: database busy** בערך פעמיים ביום: טרנזקציית התכנון נסגרת אחרי 30 דקות בלי פעילות כשהעובד עמוס. ה-retry מסדר את זה.
- **~1,800 אירועים** שיש להם רק עיר או אזור, בלי מקום, ולכן אין להם פין.
- **ogo בזמן דיפלוי**: עמודים שבאמצע קריאה נכתבים בלוג כ-`OgoScraper must be used as an async context manager`. הריצה ממשיכה אחרי האתחול ושום דבר לא אובד. זה רעש בלוג.
- **בדיקה תלוית שעה**: `tests/test_bot_daily.py` מחשב את `TONIGHT` בזמן ה-import, וריצה שחוצה חצות נכשלת.

## 2. מקורות חדשים לבנות

סימון "כבר נשלח" = לינק שהמשתמש שלח. כל השאר מהמחקר של 22.11.
לא נפתחו (רשת חסומה בסשן), אז דרך הקריאה היא השערה עד שבודקים.

| # | עדיפות | אתר | מה זה | הערה |
|---|---|---|---|---|
| 1 | ✅ | ticket4u.co.il | שפע הפקות, קופה על Eventim | בייצור (`f872baa`): 363 אירועים. כמעט כולם גם ב-zappa/eventim, ו-dedup ממזג |
| 2 | ✅ | parties247.co.il | מדריך מסיבות ארצי | בייצור (`f872baa`): 71 מסיבות, רובן גם ב-goout |
| 3 | ✅ | basalon.co.il | הרצאות, סטנדאפ, סדנאות | בוצע: `sources.basalon` (`028b9e6`) |
| 4 | ✅ | havanaclub.co.il (כבר נשלח) | Havana Music Club, ת"א | בייצור (`f872baa`): 6 ערבים, עם banner המועדון כתמונה |
| 5 | ✅ | newacropolis.org.il (כבר נשלח) | אקרופוליס החדשה: הרצאות, קורס פילוסופיה | בייצור (`f872baa`): 8 אירועים. שיעורי הקורס מסוננים |
| 6 | ✅ | hamesila.org.il (כבר נשלח) | המסילה, הרצאות | בייצור (`3b546e3`): 7 אירועים, מהם 3 הרצאות שאין במקור אחר |
| 7 | ✅ | jerusalemarts.co.il (כבר נשלח) | Jerusalem Arts | בייצור (`3b546e3`): 15 מופעים, מהם 6 חינמיים שאין במקום אחר |
| 8 | ✅ | arthurmurray.co.il (כבר נשלח) | Arthur Murray, ערבי ריקוד (The Salon) | בייצור (`59d3bb4`): ה-Salon הבא (5.12) |
| 9 | ✅ | online-reservations.yadvashem.org (כבר נשלח) | הזמנות יד ושם | בייצור (`59d3bb4`): 145 סיורים ציבוריים לפי תאריך, 8 חודשים קדימה |
| 10 | ⏳ | standupfactory.co.il | מועדון סטנדאפ, ת"א | נבדק שוב 23.11: עונה ישיר (200, גם מגרמניה). נבנה `standupfactory`: 98 הופעות מלוח ההופעות (6 עמודים, עד פברואר 2027), 100% מפה, ז'אנר, עיר ולינק לקנייה. כל עמוד הופעה מטמיע את הקנייה של GoShow, והלינק הזה הוא ה-purchase_url, כך ש-dedup ממזג עם `goshow`. אין מחיר ותיאור באתר (הם ב-GoShow). ממתין לדחיפה |
| 11 | ⛔ דילוג | tarbut.events | אתר תרבות עם עמודי מקומות | נבדק 23.11: מאגד שמעתיק אירועים מאתרי המקומות (בערך 4,300 כרטיסים). במדגם של 720, 94.6% כבר במסד, ורוב השאר לא אירועים. ה-robots.txt חוסם את ClaudeBot. לעתיד: מקורות ישירים ל-t-g.co.il (תיאטרון גבעתיים) ול-park.co.il, דרך ה-skill |
| 12 | ✅ | glatticket.co.il | כרטוס למגזר החרדי/דתי | בייצור (`3b546e3`): 59 אירועים, מהם 7 חדשים |
| 13 | ⛔ דילוג | ticketsi.co.il | מגזין + כרטיסים | מגזין SEO שמעתיק כותרות. 57 מתוך 57 כבר במסד |
| 14 | ✅ | בית הגפן חיפה | פסטיבל "חג החגים" 10–20.12.2026, חינם | בייצור (`3b546e3`, `beithagefen`): 77 הופעות, מהן 24 חדשות. תוקן הפין של הבית, שהיה 27 ק"מ דרומה |
| 15 | ⏳ | bimot.co.il → bimotglobal.co.il | בימות גלובל: מפיק מחול, מוזיקה, אופרה | נבדק 23.11: bimot.co.il מת מכל הכיוונים (ישיר, הפרוקסי, ה-VPN). האתר עבר ל-bimotglobal.co.il, שעונה ישיר. נבנה `bimot`: 14 מופעים ב-5 הפקות, 100% מפה, ז'אנר ועיר. הקופות הן PRES של האופרה ו-Barak, ו-dedup ממזג. ממתין לדחיפה |
| 16 | ⛔ דילוג | cluberim.co.il | מדריך מסיבות לפי ז'אנר | 9 אירועים, וכל ה-9 כבר במסד |
| 17 | ⛔ דילוג | tickster.co.il | מאגד | 52 מתוך 52 כבר במסד (kupatbravo/funzing) |
| 18 | ⏳ | israelshow.co.il | מאגד, חזית של Kupat Bravo | נבדק 23.11: עונה ישיר. נבנה `israelshow`: כ-50 עמודי ז'אנר ותיאטרון נותנים כ-500 מופעים, והמועדים נקראים מאותו מזהה ב-kupatbravo.co.il (ה-robots של ה-white label חוסם הכל). 1,938 מועדים, 100% מפה, ז'אנר ועיר. חופף ל-kupatbravo, ו-dedup ממזג לפי מזהה המועד. ממתין לדחיפה |
| 18 | ⏳ בבדיקה | ishow.co.il, kzat-tarbut.com, jeroozevents.co.il | מאגדים | jerooz: אתר מושבת. ishow: Cloudflare חוסם. kzat-tarbut: חזית של Kupat Bravo |
| 19 | ✅ | salsavida.com, where-to-dance-salsa.com | סלסה/בצ'אטה שבועי | salsavida בייצור (`59d3bb4`): 237 ערבים מ-8 סדרות. where-to-dance: רק Havana, שכבר נקרא |
| 20 | ⛔ דילוג | kolsadna.co.il | השוואת סדנאות | אין מועדים: הכל בתיאום |
| 21 | ⏳ | park.co.il | גני יהושע: אירועי פארק הירקון, פארק בגין, גני הטבע וראש ציפור | נבדק 23.11: עונה ישיר. נבנה `ganeyyehoshua`: 45 מפגשים מ-18 אירועים (סיורי צפרות וטבע, ימי משפחה, ספורט, Lumagica), 100% מפה (פין לכל נקודת מפגש), ז'אנר ועיר. Lumagica נמכר בקופת ת"א, ו-dedup ממזג. דילוג: מפגשי דיגיטף לתושבים, הקורס העונתי, הפילאטיס השבועי, ופריטים בלי תאריך. ממתין לדחיפה |

## 3. עוד קופות Smarticket ✅

כל הכתובות במאגר ה-Wayback (`web.archive.org/cdx`, הרשימה של כל הדפים שנשמרו תחת `*.smarticket.co.il`) נבדקו, מ-a עד z.

- **נוספו 26 קופות** (`59d3bb4`, `bb210bc`, `d048c01`, `f2144f5`): knesset, knesset-museum (רק סדרת ההרצאות), lgbtqcenter, reneshani, poetryslam, shachar, rclub, sci-park, rakiamission, policemuseum, studioact, to-mix, tziporela, visitashdod, wikimedia-il, tiptaf ועוד.
- **לא נוספו**: 14 כתובות עונות 403 עם גוף ריק גם דרך ה-VPN, כלומר קופות שנסגרו. 7 כבר לא קיימות. סיורי מוזיאון בשעות קבועות ותחרויות ריצה הושארו בחוץ (רק אירועים ציבוריים).

**סבב שלישי (23.11, `cbe7b80`, ממתין לדחיפה).** מקורות: ה-Wayback שוב, DNS פסיבי (subdomain.center, hackertarget, rapiddns), urlscan, כ-3,300 שמות מנוחשים (ערים × תבניות כמו ht/matnas/tarbut, ומקומות), וכל דומיין ישראלי שמופיע בריפו, כולל `tickets.` ו-`kupa.`. crt.sh ו-Common Crawl לא ענו. ב-CT יש רק תעודת wildcard, בלי שמות קופות. כתובת שעונה 403 נבדקה מהייצור בארבע דרכים: ישיר, VPN, evomi והפרוקסים הישראליים החינמיים.

- **נוספו 6 קופות** (ריצה יבשה מקומית, אירועים קרובים): act-b7, גודמן בית ספר למשחק, באר שבע (11, ההפקות של הסטודנטים, בלי אודישנים והרשמה) · bjhc, מרכז מורשת יהדות בבל, אור יהודה (2) · deadsea, מועצה אזורית מגילות ים המלח (2: מצוקי דרגות וקליה, בלי הסיור בנחל) · dida, תיאטרון דידה, הרצליה (3, בלי הצגה "סגור לילדי...") · donkeyland, מופע בידור בבית ציוני אמריקה, ת"א (2, חופף ל-`zoatlv` ו-dedup ממזג) · hasifriya, תיאטרון הספרייה בבית צבי, רמת גן (4, בלי "תראו אותי" לכיתות ד-ו).
- **תוקנו**: ל-sci-park (פארק קרסו למדע, באר שבע) ול-policemuseum (בית מורשת משטרת ישראל, בית שמש) נוספו מקום ועיר. rakiamission ו-wikimedia-il הוסרו: אין להן מופעים ואין להן אולם.
- **פינים**: גודמן (בית העם הישן), מורשת יהדות בבל, מצוקי דרגות, מועדון קליה, תיאטרון דידה, בית צבי, פארק קרסו, בית מורשת המשטרה. כל האולמות של הקופות החדשות מקבלים פין. `check_source smarticket` אומר OK: 16,010 אירועים, 0 כשלים.
- **כפילויות, לא נוספו** (אותם מזהי מופעים כמו קופה שכבר קיימת): bhm = tickets.anumuseum, heichalbair, htyavne, raanana, nalagaat, nz = tarbut-nz, matnaso = ofektivi, mediatheque-kids-cinema ו-mediatheque-main-library = mediatheque, midbarium = kivunimb7, tickets.tziporela.com, tickets.mishkan7.co.il, tickets.act-b7.co.il.
- **נדחו**: imj ו-tamuseum (ה-robots.txt חוסם הכל) · histadrut (סמינרים לחברי ועד) · dorledor (מועדון ותיקים בעפולה: טיולים, חוגים ויציאות לחברים) · la-zug (טיולים לחו"ל) · outsider (מרוץ) · freshpaint (סיור של מועדון אמנות במוזיאון) · lunada ו-ikesher (משבצות כניסה) · convention-ashkelon (טרום בכורות בלי שם) · demo ו-training (הדגמות של Smarticket).
- **סגורות** (403 עם גוף ריק בכל ארבע הדרכים): beresheet, betgabriel, chabad, galileemusic, givatzeev, hastam, hay-kef, hod-hasharon, hof-hasharon, kannot, keshet, m-hagilboa, mariaarberevents, nidev, ronenfoundation, tlv-beitbialik, youtheater, bgu, tarbuta. האתרים של youtheater ו-hof-hasharon עדיין מקשרים לקופה, אבל היא סגורה.
- **ריקות עכשיו** (אין מופעים, גם דרך ה-VPN): agfestival, anoluna, bac2, bomba-circus, danceacademy, docaviv, enerbox, eposproduction, flcircus, ganeytikva, ganrave, hatarbut, hbks, hodbr7, inbar, jcmf, lilach, lotan, mojo, muza, olla, oryehuda, pnay, studiobalance, tarbutmodiin, team. כל שאר השמות עונים "לא נמצאה מערכת".
- **להחלטה (מחוץ לבלוק הקופות)**: הקטגוריה "ילדידה" של דידה לא ממופה לז'אנר (2 הצגות מקבלות רק culture). צריך להוסיף "ילדידה" ל-`enrichment.genres.kids`. בנוסף, "ישראלית" בתיאור של "תפוחים מן המדבר" מסמן את ההצגה גם כ-israeli. ב-46 קופות ותיקות אין עיר בכוונה (להקות ומועצות שמופיעות בכמה מקומות: האולם קובע את העיר). כשהאולם לא מזכיר עיר, האירוע נשאר בלי עיר: 700 אירועים, רובם מ-t-hazafon (104), htc (57), eyz (45), yadal, menashe ו-megido.

## 4. הרשאות רשת לאשר (סשן ענן)

כדי לבנות את סעיף 2 בסשן ענן צריך לאשר גישה לאתרים האלה (או לעבוד מהמחשב, שם הרשת פתוחה):

```
www.ticket4u.co.il  ticket4u.co.il
www.parties247.co.il  parties247.co.il
basalon.co.il  www.basalon.co.il
www.havanaclub.co.il  havanaclub.co.il
www.newacropolis.org.il  newacropolis.org.il
hamesila.org.il  www.hamesila.org.il
jerusalemarts.co.il  www.jerusalemarts.co.il
www.arthurmurray.co.il  arthurmurray.co.il
online-reservations.yadvashem.org
www.standupfactory.co.il  standupfactory.co.il
www.tarbut.events  tarbut.events
home.glatticket.co.il  glatticket.co.il
www.ticketsi.co.il  ticketsi.co.il
web.archive.org
```

## 5. כבר נסרק, לא צריך כלום

ksaba (כפר סבא), yotzim, cinema.co.il / Soundtrack TLV, tcj (בית מזיא), yadvashem (Smarticket), mugics,
kupatbravo, leaan, tickchak, mevalim, muzi, hypd, makore, plusparty, airdrop, zygo, popit, eventim, zappa,
kupat (קופת ת"א), comedybar.

## 6. לא ברור

- לינק פייסבוק `facebook.com/share/1FCNdDWfNW`: לא נפתח. צריך שם אירוע או מקום.

## בדיקה לפני כל push

```bash
python -c "import yaml; yaml.safe_load(open('config/settings.yaml'))"
```
