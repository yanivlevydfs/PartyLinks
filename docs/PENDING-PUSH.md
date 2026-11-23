# PartyLinks: רשימת משימות

עודכן: 23.11.2026, 13:20

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
- [x] 11 המקורות החדשים רצו בייצור (23.11, 03:26): glatticket 59, hamesila 7, jerusalemarts 15, yadvashem 145, havanaclub 6, beithagefen 77, newacropolis 8, parties247 71, ticket4u 363, salsavida 237, arthurmurray 1.
- [ ] **קופות Smarticket החדשות (סעיף 3)**: עוד אין להן אירועים במסד. ריצת smarticket של 06:14 מחכה בתור, כי כל 8 המקומות תפוסים בריצות ארוכות (התור בפיגור של כ-2.5 שעות). לבדוק אחרי הריצה.
- [ ] **cinematheque_tlv**: להסיר את Soundtrack TLV 2026 מ-`festivals` אחרי 28.11.
- [ ] **הקטנת התמונות ל-960**: לוודא שהסתיימה. בלוג צריכה להופיע השורה `artwork copies: done ... shrunk to 960 wide`. בדיסק לא אמורים להישאר קבצי `*.1280.webp`, חוץ מבודדים שלא הצליחו להיות מוקטנים. נפח הדיסק אמור להיות כ-13GB. ב-13:06 הוקטנו 25 אלף מתוך 99 אלף.

## ⏳ ממתין לדחיפה

- אין. `c5435a1` (TikTik) ו-`4244919` (פינים ל-matnastc) כבר ב-master. הדחיפה האחרונה: `7219c92` (23.11, 13:15).

## ⚠️ נמצא בדרך, להחלטה

- **חוות בוטים** (23.11, 06:00–06:36): בערך חצי מהתעבורה הגיעה מדפדפנים ישנים (Chrome 118–121, Firefox 120/121) מ-575 כתובות IP שונות, רובן ביתיות. התעבורה קפצה מ-123 ל-~550 בקשות לדקה, והיו התפרצויות קצרות של 503 (04:30, 06:09, 06:29–06:33: ~17). הבוטים מריצים את כל האתר, כולל `/go`, ולכן גם מנפחים את ספירת הקליקים. הגבלה לפי IP לא תופסת אותם. אפשרויות: (א) לחסום Chrome/Firefox מתחת לגרסה 125 כמו `blocked_bots`; (ב) לא לספור קליקים של `/go` מהם; (ג) Cloudflare/challenge. לא בוצע כלום עד שתחליט.
  | חלון (שעון ישראל) | חלק מכל הבקשות | חלק מקליקי `/go` | 503 |
  |---|---|---|---|
  | 06:20–06:38 | 63% | 22% (63 מתוך 278) | 26 |
  | 06:43–07:08 | 29% | 16% (84 מתוך 503) | 0 |
  | 05:08–05:56 | 41% | 15% (67 מתוך 423) | 1 |
- **ticket4u**: ה-API של Eventim נותן `location.city = "חיפה והצפון"` (אזור), וזה נשמר כעיר חיפה. אובחן, לא תוקן.
- **תור הריצות**: אחרי דיפלוי, ובזמן ריצות ארוכות (ogo, makore, eventer, eventbrite), מקורות מחכים שעות למקום פנוי. גם ריצה שסיימה לקרוא נשארת ב-running עד שה-regroup וה-geocode שלה מסתיימים.
- **ticket4u**: עמודי האירועים נחסמים דרך כל ה-proxies ("access denied by the site"). הריצות מסתיימות partial, אבל נתוני הרשימה נשמרים (363 אירועים, אף אחד לא חסר).
- **holon** מושהה: הבדיקה שלו ב-02:38 נכשלה ב-ProxyError 500 דרך כל ה-proxies, והאתר עדיין נראה למטה.
- **regroup: database busy** בערך פעמיים ביום: טרנזקציית התכנון נסגרת אחרי 30 דקות בלי פעילות כשהעובד עמוס. ה-retry מסדר את זה.
- **~1,800 אירועים** שיש להם רק עיר או אזור, בלי מקום, ולכן אין להם פין.
- **ogo בזמן דיפלוי**: עמודים שבאמצע קריאה נכתבים בלוג כ-`OgoScraper must be used as an async context manager`. הריצה ממשיכה אחרי האתחול ושום דבר לא אובד. זה רעש בלוג.
- **בדיקה תלוית שעה**: `tests/test_bot_daily.py` מחשב את `TONIGHT` בזמן ה-import, וריצה שחוצה חצות נכשלת.

## בדיקת כל המקורות (`audit_sources.py`, 23.11) ⏳ ממתין לדחיפה

סקריפט חדש, `scripts/audit_sources.py` (`ed54368`): שורה לכל מקור פעיל, PASS/WARN/FAIL לכל בדיקה. סטטי (בריפו): SCRAPERS, ‏`sourceNames.json`, ‏STORES, ‏alias, שורה ב-`docs/sources.md` והשעות שלה, cron בדקה פנויה, קובץ בדיקה, `skip_title_words`, קופות בלי עיר ובלי מקום. בייצור (`--db`, בתוך הקונטיינר, קריאה בלבד: טרנזקציית READ ONLY ו-stat לקבצים): על האירועים הקרובים, בלי ז'אנר, בלי עיר (וכמה מהם עם פין), בלי קואורדינטות, תמונות שלא נשמרו, `content_type`, הורדות שנכשלו, עותקי 640/960 ב-WebP על הנפח (1280 שעוד לא הוקטן נחשב תקין), הריצה האחרונה, מקומות עם 10+ אירועים בלי פין, קטגוריות שלא ממופות, קופות בלי עיר, כותרות של קורסים. הספים כמו במסמכים וב-`check_source`. ה-skill מחייב עכשיו את שתי הריצות (`234160a`).

**ריצה ראשונה (14:57):** 158 מקורות, ב-64 לפחות FAIL אחד. 4 מקורות של סשנים אחרים שעוד לא נכנסו ל-commit דולגו. **אחרי התיקונים (נתוני 15:36 מול הקונפיג המקומי):** כל הבדיקות הסטטיות PASS. נשארו 28 מקורות עם FAIL: חלקם תוקנו ומחכים לדיפלוי, חלקם באמצע הריצה הראשונה אחרי הדיפלוי של 15:19, והשאר פתוחים (פירוט למטה).

| מה נמצא | מקורות | מה נעשה |
|---|---|---|
| אין alias בחיפוש | leaan, pres, pres_il, teder, makore, drop, yotzim, posh, arenas, holon, festivalim, telaviv, munievents, haifamuni, ramatganmuni, matnasnet, habama, friendshist, poalimatarim, haezor | נוספו (`072b480`). מילים יומיומיות ("לאן", "מה קורה", "יוצאים", "פרס", "הבמה", "האזור") לא נכנסו, כי alias בחיפוש הופך את כל השאילתה ל"נמכר באתר הזה" |
| קטגוריות שלא ממופות לז'אנר | hypd, beatradar, iwant, havanaclub, concertevents, ogo, eventimlight, ticketingo | נוספו ל-`declared_genres` (`b25e10f`): מלודיק, דאנס, אלקטרו, פסיכדליה, אורבן קיז, טברנה, митап ועוד. לאירועים האלה כבר היה ז'אנר מהטקסט, כך שאף מקור לא עבר את ה-5% בלי ז'אנר |
| מקום עם 50+ אירועים בלי פין | smarticket (זומו, קריית גת, 53), makore (הרפת הרובוטית, 50) | פינים (`86cd40b`). בוסתן עברי וניצחה הרוח לא נמצאים ב-OSM, ולכן לא קיבלו פין |
| קופת Smarticket בעיר אחת, בלי עיר | t-hazafon (103), yadal (37), htc (56), symphonette (21), nifgashim, psefas-matnasim | נוספה עיר (`86cd40b`), בערך 250 אירועים. מועצות אזוריות ולהקות שמופיעות בכמה מקומות (eyz, galil-elion, gderot, maianot, megido, eshkol, brener, clipa, ngvth) נשארו בלי עיר |
| 0 אירועים, מושהה | agol, gototlv, klika, tlvjazzfest | `may_be_empty` (`8558196`). כל הריצות הצליחו ומצאו 0: ל-KLIKA יש רק שורות `is_test`, המהדורה של gototlv הסתיימה ב-21.11, הפסטיבל נגמר |
| קורסים כאירועים | zoatlv, ogo (קורסי אסכולות), jerusalemmuni | `skip_title_words` (`3f68663`) |
| תמונות 404 | barak (104 מתוך 198) | התמונה מנתיב ה-teaser (`4592101`). כל כתובות ה-galery מחזירות 404 |
| אין עיר | showshappening (846 מתוך 876) | העיר מהכתובת (`05f72fe`). בריצה יבשה: 98% עם עיר. תוקן גם באג: תאריכים של היום שכבר התחילו כבר לא יוצאים (`is_current`) |
| ריצה שנכשלה | eventpro | אתר אחד שנפל (503, אבן יהודה) הפיל את כל הריצה, ומאז 22.11 לא הייתה ריצה שהסתיימה. עכשיו כל אתר נקרא בנפרד (`304de7e`) |
| שעות שגויות במסמך | easytix | `9f66082` |
| מפתח של צד שלישי ב-fixture | levontin7 (`api_key=ea11y-...` של וידג'ט הנגישות, ב-event.html וב-home.html) | הוחלף ב-REDACTED (`4fae4ed`). לסקריפט נוספה עמודה `keys`, שבודקת את `tests/fixtures` למפתחות `AIza`, JWT, AWS, Stripe, GitHub ו-Slack ולערכי `key=` / `token=`. גם ב-skill יש כלל על זה |

**פתוח:**
- **אירועים עם פין אבל בלי עיר** (תיקון בקוד הליבה, לא בקונפיג): habama 234, secrettelaviv 151, smarticket_il 205, comy 56, parks 245, happytlv 777. בסך הכל כ-1,700 אירועים, ועוד כ-250 של מועצות אזוריות ב-smarticket. העיר לא נגזרת מהפין: לא ב-enrichment, לא ב-geocode, ולא ב-group_fill (שם נלקחת רק עיר של אירוע אחר באותה קבוצה). ההצעה: אירוע בישראל עם פין ובלי עיר יקבל את היישוב שבתוך הרדיוס שלו ב-GeoNames, או את העיר של אירוע אחר באותו `venue_slug`. צריך החלטה, כי זה משנה את דפי הערים
- **cinematheque_tlv, 72 תמונות מתוך 142**: ה-S3 של PRES מחזיר `Content-Encoding: base64` על JPEG רגיל. curl_cffi, שמשמש כש-`impersonate: chrome`, נכשל (error 61), ו-httpx מתעלם מזה. התיקון ב-`media.py`: להוריד תמונות בלי impersonate, או לנסות שוב ב-httpx כשמתקבל error 61
- **geektime, 13 תמונות מתוך 14**: התמונות הן העלאות של טופס יצירת קשר (`wp_dndcf7_uploads`), וה-CDN מחזיר 403 לכל לקוח. לאתר אין תמונה אחרת לאירוע
- **ravhen**: 6 פוסטרים שמחזירים 404 באתר עצמו
- **shotgun** מושהה (429 rate limit). **holon** מושהה (האתר נפל, ידוע)
- **funzing**: 475 מועדים (3.6%) עם "קורס" בכותרת, למשל "קורס רקיחת בשמים" ו"קורס טארוט". אלה סדנאות של מפגש אחד שמוכרות לכל אחד, אבל חלקן "הסמכה". להחלטה: לדלג או להשאיר
- **115 מקורות בלי `skip_title_words`** (WARN): לא נמצאו אצלם כותרות של קורסים, ולכן לא נוסף כלום
- **לבדוק שוב אחרי הריצה הראשונה** שלהם (הדיפלוי של 13:19): israelshow, tarbut, ticketsi, tickster, cluberim, kzattarbut, kolsadna. התמונות, ה-geocode והריצה עוד לא הסתיימו, ולכן art/map/run מסומנים FAIL
- **אחרי הדיפלוי**: להריץ שוב את `--db`. מה שצריך להיעלם: barak art/dlerr, showshappening city, eventpro run, הפינים של smarticket ו-makore, ו-agol/gototlv/klika ב-run

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
| 11 | ⏳ | tarbut.events | אתר תרבות עם עמודי מקומות | נבדק 23.11: עונה ישיר, גם מ-Railway (בלי proxy). ה-robots.txt חוסם רק סורקי AI, ולכל השאר מותר. נבנה `tarbut`: רשימת האירועים (admin-ajax, ~430 עמודים) ועמוד לכל אירוע (~2,700), כ-3,100 בקשות בריצה. 4,132 מועדים, 100% מפה (פין של המקום), ז'אנר, עיר ומקום, 99% לינק לקנייה (הלינק של כל מועד: Mevalim, Smarticket, אתרי התיאטראות, dedup ממזג). אין תמונות באתר. תערוכות נשמרות פעם אחת, מהיום עד סוף התערוכה. ממתין לדחיפה |
| 12 | ✅ | glatticket.co.il | כרטוס למגזר החרדי/דתי | בייצור (`3b546e3`): 59 אירועים, מהם 7 חדשים |
| 13 | ⏳ | ticketsi.co.il | מגזין + כרטיסים | נבדק 23.11: עונה ישיר. הקופה היא ה-white label של Kupat Bravo (2005.kartisim.co.il). נבנה `ticketsi`: 14 לוחות הופעות (JSON-LD, דפי הקטגוריות = הקטגוריות שלו) נותנים ~580 מופעים, וההופעות של כל מופע נקראות מ-kupatbravo.co.il (אותו מזהה). 2,074 הופעות, 100% מפה, ז'אנר, עיר ולינק. לינק הקנייה הוא ה-seance, כך ש-dedup ממזג עם `kupatbravo` ו-`israelshow`. ממתין לדחיפה |
| 14 | ✅ | בית הגפן חיפה | פסטיבל "חג החגים" 10–20.12.2026, חינם | בייצור (`3b546e3`, `beithagefen`): 77 הופעות, מהן 24 חדשות. תוקן הפין של הבית, שהיה 27 ק"מ דרומה |
| 15 | ⏳ | bimot.co.il → bimotglobal.co.il | בימות גלובל: מפיק מחול, מוזיקה, אופרה | נבדק 23.11: bimot.co.il מת מכל הכיוונים (ישיר, הפרוקסי, ה-VPN). האתר עבר ל-bimotglobal.co.il, שעונה ישיר. נבנה `bimot`: 14 מופעים ב-5 הפקות, 100% מפה, ז'אנר ועיר. הקופות הן PRES של האופרה ו-Barak, ו-dedup ממזג. ממתין לדחיפה |
| 16 | ⏳ | cluberim.co.il | מדריך מסיבות לפי ז'אנר | נבדק 23.11: עונה ישיר. נבנה `cluberim`: קריאה אחת ל-wp-json (`events` + התגיות). 8 אירועים, 100% ז'אנר, עיר ולינק (Go-Out/Zygo/Eventer, dedup ממזג). אין קואורדינטות באתר: הכתובות עוברות geocoding. פסטיבל במיקום סודי ("ישראל") לא נכנס. ממתין לדחיפה |
| 17 | ⏳ | tickster.co.il | מאגד | נבדק 23.11: עונה ישיר. ה-API שלו (/api/) חסום ב-robots.txt, אז נבנה `tickster` מה-sitemap (~880 עמודי אירוע) וה-JSON-LD שבכל עמוד. 877 אירועים, 100% ז'אנר, עיר, תמונה, מחיר ולינק (Kupat Bravo / Funzing, dedup ממזג). אין באתר קואורדינטות (אולם + עיר עוברים geocoding) ואין קטגוריה (ז'אנר מהטקסט, ברירת מחדל culture). ממתין לדחיפה |
| 18 | ⏳ | israelshow.co.il | מאגד, חזית של Kupat Bravo | נבדק 23.11: עונה ישיר. נבנה `israelshow`: כ-50 עמודי ז'אנר ותיאטרון נותנים כ-500 מופעים, והמועדים נקראים מאותו מזהה ב-kupatbravo.co.il (ה-robots של ה-white label חוסם הכל). 1,938 מועדים, 100% מפה, ז'אנר ועיר. חופף ל-kupatbravo, ו-dedup ממזג לפי מזהה המועד. ממתין לדחיפה |
| 18 | ⛔ לא נקרא | ishow.co.il | מאגד | נבדק שוב 23.11: Cloudflare JS challenge (403, "Just a moment...") לכל לקוח: ישיר, TLS של Chrome, ה-VPN הישראלי והפרוקסים הישראליים החינמיים. ומאחוריו השרת עצמו מושבת: נתיבים בלי challenge (‏/favicon.ico) מחזירים 522 של Cloudflare ("origin timed out") מאמסטרדם, מפרנקפורט ומ-IP ישראלי. אין מה לסרוק |
| 18 | ⛔ לא נקרא | jeroozevents.co.il | מאגד | נבדק שוב 23.11: 402 `DEPLOYMENT_DISABLED` של Vercel ("Deployment Paused") ישיר, דרך ה-VPN ודרך הפרוקסים החינמיים, עם TLS של Chrome ובלי. זה מתג החיוב של האחסון, לא חסימה. אין עותק ב-Wayback ואין אתר אחר בשם הזה |
| 18 | ⏳ | kzat-tarbut.com | מגזין תרבות, חזית של Kupat Bravo | נבדק 23.11: עונה ישיר. נבנה `kzattarbut`: כ-1,250 כתבות (רובן ישנות) מקשרות למופעים ב-white label של Bravo, והרבה מהם עדיין רצים. המועדים נקראים מאותו מזהה ב-kupatbravo.co.il (ה-robots של ה-white label חוסם הכל). 1,575 מועדים, 100% מפה, ז'אנר ועיר. חופף ל-kupatbravo, ו-dedup ממזג. ממתין לדחיפה |
| 19 | ✅ | salsavida.com, where-to-dance-salsa.com | סלסה/בצ'אטה שבועי | salsavida בייצור (`59d3bb4`): 237 ערבים מ-8 סדרות. where-to-dance: רק Havana, שכבר נקרא |
| 20 | ⏳ | kolsadna.co.il | השוואת סדנאות | נבדק 23.11: עונה ישיר. רוב הסדנאות בתיאום לקבוצות, אבל יש גם מועדים פתוחים לכל אחד: הם יושבים בסקריפט ההזמנה של כל עמוד סדנה (`a_partici_type` can_join, `a_schedule`). נבנה `kolsadna`: נקראים העמודים שהשתנו ב-180 הימים האחרונים. 40 מועדים פתוחים, 100% ז'אנר, 95% עיר. אין קואורדינטות באתר: המקום הוא טקסט חופשי, והגיאוקודר ממקם אותו. ממתין לדחיפה |
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

## 7. מוזיאונים (סקר 23.11)

נבדקו 60 מוזיאונים: הקונפיג (`git grep` על הדומיין והשם), ובייצור, בקריאה בלבד, האירועים הקרובים לפי `venue_name`, מקובצים לפי מקור. אתר שלא עונה מכאן נבדק מהייצור בשלוש דרכים: ישיר, ה-VPN, והפרוקסים הישראליים החינמיים. "ספירה" = אירועים קרובים בכל המקורות יחד, לפני dedup.

| מוזיאון | כיסוי עכשיו (מקורות, ספירה) | החלטה |
|---|---|---|
| מוזיאון תל אביב לאמנות | `tamuseum` 140, makore 159, ogo 88, kupatbravo 81 (648) | מכוסה |
| מוזיאון ארץ ישראל | PRES `muza` 80, Smarticket `katedra`, ogo, makore (611) | מכוסה |
| מגדל דוד | `towerofdavid` 433, jerusalemmuni 52 (499) | מכוסה |
| יד ושם | `yadvashem` 145, Smarticket 3 (158) | מכוסה |
| אנו - מוזיאון העם היהודי | Smarticket `tickets.anumuseum` 77, ogo 10 (90) | מכוסה |
| מוזיאון ישראל (כולל רוקפלר ובית טיכו) | makore 98, ogo 42, happytlv 6, tickchak 3 (149) | ⛔ האתר מאחורי אתגר JS של Reblaze (סטטוס 247, `winsocks()`) בכל הדרכים: ישיר, VPN, ותשעת הפרוקסים החינמיים לא מתחברים. ה-robots של קופת ה-Smarticket (`imj`) חוסם הכל. נשאר דרך המאגדים |
| מוזיאון העיצוב חולון | Smarticket `mediatheque-dmh` 99, makore 26 (131) | מכוסה |
| מוזיאוני חיפה (חיפה לאמנות, טיקוטין, מאנה-כ"ץ, העיר, הימי, שטרוק) | Smarticket `hms` + ogo, makore, eventer: חיפה לאמנות 40, טיקוטין 158, הימי 26 | מכוסה: כל פריט באתר (~35, הרצאות, קולנוע, סדנאות) מוכר ב-`hms.smarticket` |
| מדעטק | Smarticket `tickets.madatech` 75 | מכוסה |
| מוזיאון המדע בלומפילד | Smarticket `mada` 25, ogo, makore (49) | מכוסה |
| מוזיאון הרצליה לאמנות עכשווית | Smarticket `herzliyamuseum` 3 | מכוסה: באתר 2 אירועים קרובים, המוזיאון סגור לשיפוץ |
| מוזיאון פתח תקווה לאמנות | ogo 5 (תערוכות) | אין באתר לוח אירועים. הכרטיסים בשוק העירוני, ש-`petahtikvamuni` קורא |
| משכן לאמנות עין חרוד | Smarticket `ehm` 23, ogo, makore (76) | מכוסה |
| מוזיאון ארצות המקרא | ogo 12, jerusalemmuni 10, makore 6 (30) | מכוסה: אותם אירועים כמו באתר (נבדק לפי כותרות) |
| מוזיאון לאמנות האסלאם | Smarticket `islamicart` 63, makore 41 (119) | מכוסה |
| מוזיאון ראובן רובין | Smarticket `rubinmuseum` 3, happytlv 6 | מכוסה |
| מוזיאון נחום גוטמן | Smarticket `gutmanmuseum` 17, ogo 16, makore 7 (45) | מכוסה |
| מוזיאון אילנה גור | makore 2, ogo 2, happytlv 7 (11) | מכוסה. הדומיין ilanagoor.com הוא היום בלוג ספאם |
| מוזיאון ינקו דאדא, עין הוד | 0 | The Events Calendar: ה-API עונה, אבל אין אירוע קרוב (האחרון ב-6.11). אין מה לקרוא עכשיו |
| מוזיאון הפלמ"ח | 0 | רק סיורים בשעות קבועות, לא נכנס |
| המוזיאון הישראלי במרכז יצחק רבין | 0 | לבנות: מופעים ומפגשים (בנאי-אלוני, וילנסקי-דמארי, ששון גבאי, צדי צרפתי, קורין אלאל) |
| מוזיאון חיל האוויר | makore 9 (16) | iaf.org.il: ‏403 של Akamai בכל הדרכים. כניסה למוזיאון בלבד |
| מוזיאון אשדוד לאמנות (מונארט) | Smarticket 48, ogo 11 (60) | מכוסה |
| מוזיאון ראשון לציון | eventbuzz 15, makore 15, ogo 9 (39) | מכוסה: הכרטיסים ב-Eventbuzz |
| מוזיאון וילפריד ישראל, הזורע | makore 2, ogo 1 | אין באתר לוח אירועים |
| MOBY מוזיאוני בת ים | Smarticket `moby` 9, ogo 6 (18) | מכוסה |
| מוזיאון הנגב לאמנות | Smarticket `tickets.negev-museum` 12 | מכוסה |
| מוזיאון הילדים חולון | happytlv 7 | משבצות כניסה (דיאלוג בחשכה), לא נכנס |
| מוזיאון ידידי ציון | Smarticket `fozmuseum` 524, makore 327 (964) | מכוסה |
| מוזיאון הטבע שטיינהרדט | Smarticket `smnh` 42, makore 27 (82) | מכוסה |
| מוזיאון רמת גן לאמנות ישראלית | Smarticket `rgma` 30, ogo 10, makore 10 (52) | מכוסה |
| מוזיאון הכנסת | Smarticket `knesset-museum` 12 (14) | מכוסה |
| מרכז מורשת מנחם בגין | Smarticket `begincenter` 626, makore 413 (1,056) | מכוסה |
| הספרייה הלאומית | Smarticket `nli` 457, jerusalemmuni 16 (479) | מכוסה |
| מוזיאון הסובלנות ירושלים | Smarticket `motj` 1, makore 1 | מכוסה (הקופה כמעט ריקה עכשיו) |
| מוזיאון העיר תל אביב-יפו | Smarticket `tlv-citymuseum` 60, ogo 37 (151) | מכוסה |
| בית ביאליק | Smarticket `tlv-bialik` 1 | מכוסה |
| המוזיאון לקריקטורה וקומיקס, חולון | Smarticket `mediatheque-cartoon` | מכוסה (אין עכשיו אירוע קרוב) |
| מוזיאון יעקב אגם | Smarticket `yama` | מכוסה (אין עכשיו אירוע קרוב) |
| מוזיאון אלי כהן | makore 335, Smarticket 4 (352) | מכוסה |
| מוזיאון מורשת החשמונאים, מודיעין | Smarticket 35, ogo 25 (90) | מכוסה |
| בית אהרונסון - מוזיאון ניל"י | Smarticket 210 (216) | מכוסה |
| הקריה הלאומית לארכיאולוגיה | jerusalemmuni 99, Smarticket 82 (197) | מכוסה |
| מוזיאון האדם והחי | Smarticket 4 | מכוסה |
| מוזיאון הטבע התנ"כי | Smarticket 244 | מכוסה |
| גן המדע, מכון ויצמן | Smarticket 42, makore 42 (90) | מכוסה |
| פלנתניה | Smarticket 57 (72) | מכוסה |
| חזון לדורות - בית החזון איש | Smarticket 338 | מכוסה |
| מוזיאון הכט, אוניברסיטת חיפה | eventer 31, makore 15, ogo 15, goshow 13 (86) | לבנות: הקונצרטים מכוסים, אבל סדרת ההרצאות "ארכיאולוגיה עכשיו" (8 מועדים עד יוני 2027) נמצאת ב-eventer רק במועד הקרוב |
| בית עגנון, ירושלים | 0 | לבנות: הרצאות וערבי ספרות (בלי ההרצאות המקוונות) |
| מוזיאון על התפר | ogo 11, jerusalemmuni 4 (15) | מכוסה: אותם סיורים, קולנוע ושיחות כמו ברשימה של Wix |
| בית לוחמי הגטאות | ogo 1 | 2 אירועי חנוכה באתר, אחד מהם ב-ogo. קטן מאוד |
| משואה | 0 | בעמוד "אירועים קרובים" אין אירוע עדכני |
| יד לשריון, לטרון | ogo 4, kupatbravo 2, makore 2 (8) | מכוסה |
| בית בן גוריון, ת"א | happytlv 5 | כניסה בלבד |
| מוזיאון חיל הים וההעפלה, מוזיאון האצ"ל | happytlv 5, ‏12 | מוזיאוני צה"ל: כניסה בלבד |
| מוזיאון בית אורי ורמי נחושתן | basalon 2, makore 1 | מכוסה |
| המוזיאון לאמנות יהודית איטלקית | 0 | ⛔ ijamuseum.org: ‏403 של Cloudflare ("Attention Required") ישיר, דרך ה-VPN ודרך הפרוקסים |
| המוזיאון הפתוח תפן | makore 2 | omuseums.org.il מוכר דרך ticketsi (נבנה במקביל). באתר tefen.co.il רק כנסים של הפארק התעשייתי |
| הגלריה לאמנות אום אל-פחם | 0 | ⛔ הדומיין לא נפתר ב-DNS, גם מהייצור |
| בית שלום עליכם, ת"א | 0 | ⛔ shalom-aleichem.org.il לא נפתר ב-DNS |

## 8. הייטק על הבר ומפגשים על הבר (סקר 23.11)

בייצור, אירועים קרובים בישראל: hi_tech 150 (luma 86, geektime 22, meetup 20), startups 108, ai 119, meetups 7,057 (funzing 5,695). באולם ששמו בר / פאב: hi_tech 0, startups 0, ai 1, meetups 14. סדרות לפי כותרת: "startup grind" 0, "women in tech" 0, "hackers & founders" 0, "language exchange" 0, סדרות "על הבר" 1 (Haifa BeerTech, ב-meetup).

| סדרה / אתר | פלטפורמה | כיסוי עכשיו | החלטה |
|---|---|---|---|
| Meetup: כל הקבוצות בישראל | meetup.com GraphQL | `meetup` 55 (שאילתה אחת, עד ~30 יום קדימה) | ✅ הורחב: חלונות של 30 יום עד 120 יום, ועוד נקודות בחיפה, באר שבע וירושלים. 55 ← 63, hi_tech 22 ← 27 (`75d2643`, `07f0861`) |
| Luma: לוחות הקהילות (Claude Community, LGBT Olim, The Table, Web3 Devs, Fusion VC, Yazamut360, "מדע על הבר" של רובע החדשנות ב"ש) | api.lu.ma | `luma` 112 (רק מה ש-Luma מקדמת ב-discover) | ✅ הורחב: גם האירועים הקרובים של כל לוח שמופיע ב-discover (~90 לוחות). 112 ← 128, hi_tech 88 ← 96 (`a3bdde7`) |
| Nefesh B'Nefesh: לוח הקהילה לעולים דוברי אנגלית (מפגשים חברתיים, young professionals, נטוורקינג, ערבי רווקים, חילופי שפות, הרצאות) | nbn.org.il, MEC iCal | 0 | ✅ מקור חדש `nbn`: 190 אירועים (meetups 122, english 190, singles 3), עיר 98%, ישיר (`7eda086`) |
| "חדשנות על הבר" של רשות החדשנות (ברים בת"א, ירושלים, חיפה, הרצליה, ב"ש) | innovationisrael.org.il (WordPress) | 0 | ⏸ לא נבנה: העונה של 2026 נגמרה ב-10.11 (ירושלים). באתר עכשיו 2 אירועים קרובים, ושניהם וובינרים. גישה: מפת האתר עונה ל-`impersonate: chrome` (גם מכאן), `wp-json` חסום ב-Cloudflare בכל הדרכים. לבנות כשתפורסם עונת 2027 (`event_series/barevents2027`) |
| Startup Grind Tel Aviv | startupgrind.com (Bevy) + meetup.com | 0 קרובים | ⏸ לא נבנה: ~1 אירוע בחודש, ומתפרסם גם בקבוצת ה-Meetup שלהם (`startup-grind-tel-aviv`), ש-`meetup` קורא |
| GDG / Angular TLV | gdg.community.dev (Bevy) + meetup.com | ב-`meetup` (Angular TLV) | מכוסה דרך Meetup |
| Haifa BeerTech, ChessBar (Masaryk), Fluent language exchange (Abraham), Game Club TLV, TAMI | meetup.com | `meetup` | מכוסה |
| Geektime, אנשים ומחשבים, HappyTLV | אתרים שלהם | `geektime` 22, `peoplecomputers` 8, `happytlv` | מכוסה |
| dev.events (כנסי פיתוח: Reversim, DevFest, Devopsdays TLV) | dev.events, JSON-LD | חלקי | ⏸ לא נבנה: אגרגטור של ~5 כנסים בישראל, מקשר לאתרי הכנסים. לא ברים |
| Polyglot Club (חילופי שפות בת"א) | polyglotclub.com | 0 | ⛔ אתגר JS של Cloudflare ("Just a moment") ישיר, ב-Chrome, ודרך ה-VPN |
| Product Tank / Women in Tech / Hackers & Founders | Meetup / Luma | 0-1 | אין עכשיו אירועים קרובים בשום פלטפורמה. אם יחזרו, יגיעו דרך `meetup` / `luma` |
| ESRA, AACI, JTLV, The Right Time (רווקים) | האתרים שלהם | דרך `nbn` | רוב האירועים שלהם מתפרסמים בלוח של NBN. לא נבנה מקור נפרד |
| InterNations, Couchsurfing hangouts, קבוצות פייסבוק | — | 0 | ⛔ דורשים התחברות |
| Calcalist / TheMarker | כנסים בתשלום | — | לא ברים ולא מפגשים, לא נבנה |
| טריוויה / פאב קוויז בברים | אינסטגרם ופייסבוק של הברים | quiz_games 122 (ogo, smarticket, happytlv) | אין פלטפורמה פתוחה לקרוא. מה שנמכר ב-Eventer / Tickchak כבר נקרא |

## בדיקה לפני כל push

```bash
python -c "import yaml; yaml.safe_load(open('config/settings.yaml'))"
```
