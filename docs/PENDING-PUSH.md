# PartyLinks: רשימת משימות

עודכן: 22.11.2026

## ✅ בוצע

- master תוקן: `config/settings.yaml` נטען שוב (`feef4a9`), נדחף ל-master ב-22.11 (`602116d`).
- 9 קופות Smarticket חדשות ב-master: mvhr, pastoral, migvanim, matnastc, kikar-bialik, pnay, betgabriel, mofa, docaviv.
- **Basalon** (basalon.co.il, סעיף 2 #3): מקור חדש, נדחף ב-22.11 (`028b9e6`).
- **`/categories`**: נפל ב-500 כל כמה דקות כל 22.11 (`IdleInTransactionSessionTimeout`: טרנזקציית קריאה פתוחה בזמן דקות של ספירה). תוקן ונדחף (`104a8af`). מאז אין שגיאות.

## 1. לבדוק בפרודקשן (אחרי הדיפלוי)

נבדק ב-22.11, 23:10 (שאילתות קריאה בפרודקשן):

- [x] הדיפלוי עבר (`028b9e6`, SUCCESS).
- [x] **mugics**: ארבע הריצות האחרונות הצליחו (279–294 אירועים). 278 הופעות קרובות: לכולן תאריך, פין, קישור קצר ולינק, ול-273 מהן מקום. 42 מוצגות ככרטיס משלהן, והשאר מוזגו עם אותה הופעה במקור אחר.
- [x] קופות Smarticket: כל ששת הקופות שנשארו מחזירות אירועים: mvhr 4, pastoral 3, migvanim 43, matnastc 11, kikar-bialik 14, mofa 16. השלוש הריקות (pnay, betgabriel, docaviv) הוסרו ב-`b1d2441`.
  - [ ] **matnastc**: רק ל-1 מתוך 11 אירועים יש פין. צריך לבדוק את המקומות שלה (כל הופעה באולם אחר).
  - [ ] **pastoral**: 3 אירועים ואף אחד לא מוצג ככרטיס משלו, כולם מוזגו עם מקור אחר. לבדוק אם הקופה מוסיפה משהו.
- [ ] **cinematheque_tlv**: להסיר את Soundtrack TLV 2026 מ-`festivals` אחרי 28.11.

- **503 על `/e/`** (`defdd21`, נדחף ב-22.11 23:17, הדיפלוי SUCCESS): כל עמוד אירוע חיפש את המקום שלו ב-`venue_name ILIKE`, סריקה של כל 217 אלף האירועים (200–390ms, פעמיים בכל עמוד). בהתפרצות של סורקים נגמרו מקומות הבנייה והעמודים קיבלו 503. עכשיו `lower(venue_name)` עם האינדקס `ix_events_venue_name_lower`.
  - האינדקס נבנה בפרודקשן ב-CONCURRENTLY והוא VALID (2.8MB). השאילתה עוברת דרכו: 0.05ms במקום 112–390ms.
  - ב-15 הדקות אחרי הדיפלוי: 0 תשובות 503 על `/e/`. 31 עמודים, ממוצע 617ms.

## ⏳ ממתין לדחיפה

- [ ] **503 בהתפרצויות של עמודי `/venue/`** (22.11, 23:42: 13 תשובות 503 בתוך 4 שניות). כל עמוד מקום בנה מחדש את כל 19,280 פרופילי המקומות ואת טבלת האיותים שלהם, פעמיים (עברית ואנגלית), והריץ סריקה על `venue_name IN`. זה לקח 0.9–2.9 שניות לעמוד, ומקומות הבנייה התמלאו. התיקון: הפרופילים נשמרים בזיכרון 5 דקות לכל מסד, טבלת האיותים נבנית פעם אחת לכל רשימה, והשאילתות עוברות דרך `lower()` והאינדקס. נמדד בפרודקשן עם הקוד החדש: 10–26ms לעמוד.
- [ ] **ticket4u** (סעיף 2 #1): כתוב ונבדק מקומית, עוד לא נדחף. ה-dry run מחזיר 364 אירועים: ז'אנר 100%, תמונה 96%, מחיר 96%. כמעט הכל כפילויות של zappa/eventim, שה-dedup ממזג. לאחר הדחיפה צריך להריץ בפרודקשן ולבדוק את הפינים (26% מגיעים מה-API, השאר מקבלים מיקום בייבוא לפי שם המקום והעיר).

- [ ] **havanaclub** (סעיף 2 #4): כתוב ונבדק. ב-dry run: 6 ערבים, פין, ז'אנר ושעה 100%. אין תמונה או מחיר באתר.
- [ ] **parties247** (סעיף 2 #2): כתוב ונבדק. ב-dry run: 66 מסיבות, ז'אנר, תמונה, לינק ושעה 100%, עיר 92%.
- [ ] **newacropolis** (סעיף 2 #5): כתוב ונבדק. ב-dry run: 8 אירועים, מקום, עיר, תמונה, ז'אנר ושעה 100%.
- havanaclub: נוספה תמונה, ה-banner של המועדון מה-`og:image` של עמוד הכרטיסים. כיסוי התמונות עלה מ-0% ל-100%.
- סעיף 2 #6–#20: 6 סוכנים בודקים ובונים במקביל.
- [ ] אחרי הדחיפה: להריץ כל אחד מהשלושה בפרודקשן, ולבדוק את הספירות במסד, את ה-`640.webp`, עמוד אירוע ועמוד מקום.

## ⚠️ נמצא בדרך

- **ogo בזמן דיפלוי**: ב-23:18:47, כשהקונטיינר הישן נסגר, עמודים שהיו באמצע קריאה נכתבו בלוג כ-`skipped ...: RuntimeError: OgoScraper must be used as an async context manager`, כי הסורק כבר נסגר. הריצה חודשה אחרי האתחול (`resumed: 3424 pages read since the interrupted run`), כך שלא אבדו אירועים. זה רעש בלוג שחוזר בכל דיפלוי שקוטע ריצה של ogo.
- **parties247** (סעיף 2 #2): מדריך שבנוי על Go-Out. 16 מתוך 20 עמודים מקשרים ל-Go-Out, ו-goout כבר שומר 14 מהם. ה-sitemap של האירועים ריק, אז הקריאה היא דרך 23 הרשימות שבעמוד הבית. ביקשת לבנות אותו בכל זאת, והוא נבנה.
- **havanaclub** (סעיף 2 #4): האתר בנוי ב-Wix, ועמוד `רכישת-כרטיסים` מציג את 6 הערבים הקרובים (שלישי, חמישי ושבת: סגנונות ריקוד ותאריך), עם קישור `/event/<uuid>` לכל ערב. עמוד הערב עצמו נבנה בדפדפן ואין בו מידע. השעות לפי לו"ז קבוע: דלתות 21:00, שיעורים 21:15–21:30, מסיבה 22:30. כרטיסים 60–70 ₪ דרך `personal.havanaclub.co.il`, ואין מחיר או תמונה בעמוד הכרטיסים.
- **בדיקה תלוית שעה**: `tests/test_bot_daily.py` מחשב את `TONIGHT` בזמן ה-import. ריצה שחוצה חצות נכשלת, וכשמריצים אותה לבד היא עוברת.

- **ה-regroup** (`dedup._tier_counts`) מחזיק טרנזקציית קריאה פתוחה ובלי פעילות עד 30 דקות (`regroup_idle_timeout_ms`). זה חסם את בניית האינדקס 20 דקות, וזה גם מעכב את ה-vacuum. מאותו סוג כמו התקלה ב-`/categories`: הקריאות צריכות להסתיים ולסגור את הטרנזקציה לפני החישוב.

## 2. מקורות חדשים לבנות

סימון "כבר נשלח" = לינק שהמשתמש שלח. כל השאר מהמחקר של 22.11.
לא נפתחו (רשת חסומה בסשן), אז דרך הקריאה היא השערה עד שבודקים.

| # | עדיפות | אתר | מה זה | הערה |
|---|---|---|---|---|
| 1 | ⏳ | ticket4u.co.il | שפע הפקות, קופה על Eventim | נכתב (`sources.ticket4u`, קורא Eventim על ה-sitemap של T4U ועל ה-API של Eventim). 527 אירועים, כולם גם ב-Eventim, 520 כבר אצלנו. ממתין לדחיפה |
| 2 | ⏳ | parties247.co.il | מדריך מסיבות ארצי | נכתב (`sources.parties247`): 66–71 מסיבות מ-23 רשימות. ז'אנר, תמונה, לינק ושעה 100%, מחיר ומקום 95%, עיר 92%: ל-5 מסיבות האתר נותן רק רחוב, בלי עיר. כמעט הכל גם ב-goout (`purchase_url` = עמוד ה-Go-Out). ממתין לדחיפה |
| 3 | ✅ | basalon.co.il | הרצאות, סטנדאפ, סדנאות | בוצע: `sources.basalon` (`028b9e6`) |
| 4 | ⏳ | havanaclub.co.il (כבר נשלח) | Havana Music Club, ת"א | נכתב (`sources.havanaclub`): 6 ערבים קרובים מעמוד הכרטיסים, עם פין, ז'אנר (latin_dance, salsa) ושעה. ממתין לדחיפה |
| 5 | ⏳ | newacropolis.org.il (כבר נשלח) | אקרופוליס החדשה: הרצאות, קורס פילוסופיה | נכתב (`sources.newacropolis`): ה-sitemap של Wix Events, רק עמודים שעודכנו ב-180 הימים האחרונים, וה-JSON-LD של כל עמוד. 8 אירועים, וכולם עם מקום, עיר, תמונה, ז'אנר ושעה. שיעורי הקורס והתנדבות מסוננים, מפגשי ההיכרות הפתוחים נשארים. ממתין לדחיפה |
| 6 | בינונית | hamesila.org.il (כבר נשלח) | המסילה, הרצאות | יש `/events/` |
| 7 | בינונית | jerusalemarts.co.il (כבר נשלח) | Jerusalem Arts | |
| 8 | בינונית | arthurmurray.co.il (כבר נשלח) | Arthur Murray, ערבי ריקוד (The Salon) | |
| 9 | בינונית | online-reservations.yadvashem.org (כבר נשלח) | הזמנות יד ושם | פעילויות שלא דרך Smarticket `yadvashem` |
| 10 | ⛔ דילוג | standupfactory.co.il | מועדון סטנדאפ, ת"א | נבדק 23.11, מכוסה: כל עמוד מופע מטמיע את הקנייה של GoShow. כל 18 המופעים בלוח שלהם כבר במסד, 15 מהם דרך `goshow` באותה שעה. אין אירועים חדשים. האתר מגיש 202 של Cloudflare, ורק ה-VPN מקבל 200 |
| 11 | ⛔ דילוג | tarbut.events | אתר תרבות עם עמודי מקומות | נבדק 23.11: מאגד שמעתיק אירועים מאתרי המקומות (בערך 4,300 כרטיסים). במדגם של 720, 94.6% כבר במסד, ורוב השאר לא אירועים. ה-robots.txt חוסם את ClaudeBot. לעתיד: מקורות ישירים ל-t-g.co.il (תיאטרון גבעתיים) ול-park.co.il, דרך ה-skill |
| 12 | בינונית | glatticket.co.il | כרטוס למגזר החרדי/דתי | קהל שאין לנו |
| 13 | בינונית | ticketsi.co.il | מגזין + כרטיסים | כנראה כפילויות |
| 14 | לבדוק | בית הגפן חיפה | פסטיבל "חג החגים" 10–20.12.2026, חינם | יש רק פין |
| 15 | לבדוק | bimot.co.il | מפיק וקופה | לא ברור אם פעיל |
| 16 | נמוכה | cluberim.co.il | מדריך מסיבות לפי ז'אנר | אולי לא מתעדכן |
| 17 | נמוכה | tickster.co.il | מאגד | ברובו כפילויות |
| 18 | נמוכה | israelshow.co.il, ishow.co.il, kzat-tarbut.com, jeroozevents.co.il | מאגדים | נראים לא מעודכנים |
| 19 | נמוכה | salsavida.com, where-to-dance-salsa.com | סלסה/בצ'אטה שבועי | אירועים חוזרים |
| 20 | נמוכה | kolsadna.co.il | השוואת סדנאות | רובן לפי הזמנה |

## 3. עוד קופות Smarticket

חיפוש הרשת מוצה (~50 חיפושים, הכל כבר ברשימה; יש 220 מתוך "350+").
להריץ מהמחשב ולהעלות את הקובץ, ואני אשווה מול הקונפיג:

```bash
curl "https://web.archive.org/cdx/search/cdx?url=*.smarticket.co.il&fl=original&collapse=urlkey&output=txt" | sed -E 's#https?://([^/:]+).*#\1#' | sort -u > smarticket-hosts.txt
```

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
