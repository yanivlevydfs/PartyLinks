# תוכנית SEO / GEO / AEO ל-SPOTX

> נכתב: 2026-10-30. מסמך תכנון בלבד, הקוד לא שונה.
> כלל הבית: כל שינוי SEO נשען על התיעוד של Google (developers.google.com/search) ונבדק מולו (Rich Results Test, URL Inspection, Search Console) לפני ואחרי. לכל סעיף מצורף קישור למקור.
> כל שינוי שעובר על כל הטבלה (sitemap, דפי נחיתה, llms.txt) נמדד בגודל של פרודקשן (~28 אלף אירועים) לפני push.

מקרא: **מאמץ** S = עד חצי יום · M = יום עד שלושה · L = שבוע ומעלה. **השפעה** גבוהה / בינונית / נמוכה.

---

## 0. תקציר: חמשת הממצאים הכי חשובים

1. **ב-Google, תוצאות אירועים עשירות (Event rich results) לא זמינות בישראל ולא בעברית.** לפי התיעוד, החוויה זמינה רק באוסטרליה, ברזיל, קנדה, גרמניה, הודו, אמריקה הלטינית, ספרד, בריטניה וארה״ב ([Event structured data, עודכן 2026-09-08](https://developers.google.com/search/docs/appearance/structured-data/event)). ה-JSON-LD שלנו עדיין שווה הרבה: הוא עוזר להבנת הדף, ל-Bing ולמנועי AI, ולדפים באנגלית שגולשים מחו״ל רואים. אבל הצמיחה ב-Google בעברית תבוא מדפי נחיתה, מתוכן ומקישורים נכנסים, ולא מכרטיסי אירוע בתוצאות החיפוש.
2. **סיכון לאכיפה על נתונים מובנים:** מקור HappyTLV הופך שעות הפי האוור ו"מקומות" (חדרי בריחה, עגלות קפה, עמותות, חללי עבודה) ל"אירוע" אחד לכל יום פתיחה, ומסמן כל אחד מהם כ-`Event`/`MusicEvent`. Google אוסר במפורש לסמן כאירוע "שעות פעילות של עסק", "קופונים" ו"מבצעים לזמן קצר" ([Event: guidelines](https://developers.google.com/search/docs/appearance/structured-data/event#guidelines), [מדיניות נתונים מובנים](https://developers.google.com/search/docs/appearance/structured-data/sd-policies)). הרשומות האלה גם ממלאות את דפי `/tonight` ("קלוב התעופה לישראל", "שקיפות בינ״ל ישראל ע״ר" בשעה 00:00) ופוגעות באיכות הדף שהכי מחפשים.
3. **ה-`lastmod` במפת האתר הוא `last_seen_at`**, כלומר הוא משתנה בכל סריקה גם כשהדף לא השתנה. Google משתמש ב-lastmod רק אם הוא "מדויק באופן עקבי וניתן לאימות" ([Build a sitemap](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap)). כנראה שהוא כבר מתעלם מאות העדכון שלנו בכל ~125 אלף ה-URL-ים.
4. **תשובות ה-FAQ בדפי הנחיתה מציגות מספרים שגויים.** בדף `/tonight` כתוב "כרגע יש 500 אירועים הערב", כי השאילתה מוגבלת ל-`limit=500`. מנועי תשובות מצטטים בדיוק את המשפטים האלה, אז הם חייבים להיות נכונים. בנוסף, ה-rich result של FAQ [הוסר מ-Google ב-7 במאי 2026](https://developers.google.com/search/updates): לסימון FAQPage אין יותר ערך תצוגתי ב-Google. ה-Q&A הגלוי בדף נשאר שימושי ל-AI.
5. **אין אותות ישות (entity) למותג:** ל-`Organization` אין `sameAs`, אין `contactPoint` ואין פרופילים חברתיים מקושרים, ואין ערך ב-Wikidata. בלי אלה, Google ומנועי AI לא יודעים ש-"SPOTX" הוא מותג אחד ומוגדר ([Organization structured data](https://developers.google.com/search/docs/appearance/structured-data/organization)).

---

## 1. מצב קיים

### 1.1 חוזקות (עם ראיות)

| תחום | מה יש | ראיה |
| --- | --- | --- |
| רינדור בצד השרת | כל דף ציבורי מוגש מ-FastAPI עם `<head>` מלא ותוכן HTML בתוך `#root`, כך שבוטים בלי JS רואים הכל | `seo.py: render()`; דף אירוע חי מכיל את הטקסט המלא ב-`.ssr` |
| JSON-LD לאירוע | `Event`/`MusicEvent` עם startDate באזור הזמן של המקום, endDate, Offer לכל סוג כרטיס (InStock/SoldOut/PreOrder), validFrom, organizer, performer, geo | `event_jsonld()`, `_offers()`; נבדק בדף חי |
| 410 לאירוע שעבר | `/event/<לא-קיים>` מחזיר 410 + noindex, שהוא האות הנכון לדף שהוסר לתמיד ([Crawl budget](https://developers.google.com/search/docs/crawling-indexing/large-site-managing-crawl-budget)) | בדיקה חיה: `gone 410` |
| hreflang | he/en/x-default בכל דף ובמפת האתר; אנגלית ב-`?lang=en` (מותר: "URL parameters" הם אחת הדרכים שGoogle מציין, [Localized versions](https://developers.google.com/search/docs/specialty/international/localized-versions)) | head של דף הבית החי |
| מפת אתר | אינדקס + 13 חלקים של 10 אלף URL (~6MB לחלק, מתחת למגבלה של 50MB / 50 אלף), במטמון לשעה | `/sitemap.xml` חי |
| IndexNow | שולח URL-ים חדשים ל-Bing/Yandex. Bing מזין את ChatGPT search ואת Copilot | `indexnow.py` |
| robots.txt | פתוח למנועי חיפוש ולבוטים של AI (OAI-SearchBot, ChatGPT-User, GPTBot, Claude-*, PerplexityBot, Google-Extended); חוסם כלי SEO וקוצרי נתונים | `/robots.txt` חי |
| דפי נחיתה פרוגרמטיים | tonight/weekend, ז׳אנר, ז׳אנר×זמן, עיר, עיר×זמן, עיר×ז׳אנר, אזור, חו״ל לפי מדינה, עם סף מינימום אירועים, intro לכל עיר (`city_intros.yaml`), FAQ מחושב מהנתונים וקישורים קשורים | `_landing_paths()`, `resolve_landing()` |
| בלוג | 52 פוסטים בעברית + 52 באנגלית, עם רשימות אירועים חיות (`<!--events-->`), FAQ, BlogPosting, RSS | `content/blog`, `blog.py` |
| ביצועים בסיסיים | Cloudflare בחזית, HTML במטמון שרת, תמונות WebP, `max-image-preview:large` | headers חיים: `server: cloudflare`; זמן תגובה 0.2–0.4 שנ׳ לדף שנמצא במטמון |
| אבטחה/אמון | HTTPS + HSTS, CSP, דף נגישות, פרטיות, תנאים | headers חיים |

### 1.2 פערים (עם ראיות)

| # | פער | ראיה | מקור Google |
| --- | --- | --- | --- |
| G1 | Event markup על דברים שאינם אירועים (הפי האוור, מקומות, עסקים שפתוחים כל יום) | docstring של `scrapers/happytlv.py`; ב-`/tonight` החי רואים "פארק הרצליה - החלק הצפוני · 00:00", "סדנאות בישול · 00:00" | [Event guidelines](https://developers.google.com/search/docs/appearance/structured-data/event#guidelines), [Spam policies: scaled content](https://developers.google.com/search/docs/essentials/spam-policies) |
| G2 | `@type` שגוי: כל אירוע עם ז׳אנר כלשהו מקבל `MusicEvent`, כולל פסטיבל קולנוע (`keywords: cinema`) | דף חי `pashoot-filmfest-…` | [sd-policies: "accurately represent the page"](https://developers.google.com/search/docs/appearance/structured-data/sd-policies) |
| G3 | `performer` = המקום (Organization "Hameretz 2") כשאין ליינאפ | אותו דף | [Event: performer = Person/PerformingGroup](https://developers.google.com/search/docs/appearance/structured-data/event) |
| G4 | `address` עם `streetAddress: null`, `addressLocality: null` (ה-null-ים המקוננים לא מנוקים) | אותו דף | [Event: location.address required](https://developers.google.com/search/docs/appearance/structured-data/event) |
| G5 | תמונה אחת בלי יחסי 16:9 / 4:3 / 1:1, ובלי וידוא רוחב של 720px ומעלה | `"image": ["https://spotx.fun/images/16058"]` | [Event: image](https://developers.google.com/search/docs/appearance/structured-data/event) |
| G6 | `eventStatus` תמיד `EventScheduled`; אין Cancelled/Postponed/Rescheduled | `event_jsonld()` | אותו דף, סעיף eventStatus |
| G7 | `inLanguage: "he"` קבוע, גם כשהתיאור באנגלית וגם בגרסת `?lang=en` | `event_jsonld()` | sd-policies (התאמה לתוכן הגלוי) |
| G8 | lastmod לא אמין (last_seen_at) | `sitemap_urls()` | [Build a sitemap](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap) |
| G9 | חלקי ה-sitemap מעורבבים (אירועים, דפי נחיתה, מקומות ואמנים יחד), ולכן אי אפשר לראות ב-Search Console כמה מכל סוג אונדקס | `sitemap-1.xml` מתחיל ב-`/`, `/map`... | [Sitemaps report](https://support.google.com/webmasters/answer/7451001) |
| G10 | אין ETag / Last-Modified / 304 לדפי HTML (רק לתמונות) | `api.py:1336-1347`; headers של דף הבית בלי ETag | [Crawl budget: support 304](https://developers.google.com/search/docs/crawling-indexing/large-site-managing-crawl-budget) |
| G11 | תוכן דק / משוכפל בדף אירוע: התיאור הוא טקסט המוכר מילה במילה (אותו טקסט קיים באתר הכרטיסים ובאתרים מתחרים), ואין תוכן ייחודי משלנו (מזג אוויר, איך מגיעים, אירועים דומים, ז׳אנר) ב-SSR | `.ssr` של דף אירוע חי | [Helpful content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content) |
| G12 | FAQ מחושב עם מספרים לא נכונים (500) ו"הכרטיס הכי זול הערב" שהוא יוגה בבוקר | `/tonight` חי | [Helpful content: accuracy](https://developers.google.com/search/docs/fundamentals/creating-helpful-content) |
| G13 | Organization בלי `sameAs`, `contactPoint`, `description`, `foundingDate`; אין שם אתר חלופי | `_website()` | [Organization](https://developers.google.com/search/docs/appearance/structured-data/organization), [Site names](https://developers.google.com/search/docs/appearance/site-names) |
| G14 | פוסטים קצרים מדי (למשל `what-to-do-this-weekend`: wordCount 200, "1 דקות קריאה"), author = Organization ו-dateModified קבוע | דף בלוג חי | [Helpful content: E-E-A-T, "who/how/why"](https://developers.google.com/search/docs/fundamentals/creating-helpful-content) |
| G15 | `llms.txt` שוקל 405KB ו-2,543 שורות (400 אירועים). Google לא משתמש בו בכלל ("You don't need to create new machine readable files, AI text files"), והתקן של llmstxt.org מכוון לסיכום קצר | `/llms.txt` חי | [AI features](https://developers.google.com/search/docs/appearance/ai-features), [llmstxt.org](https://llmstxt.org) |
| G16 | אין נתוני Core Web Vitals מתועדים (CrUX/RUM) ואין מעקב; דף הבית לא במטמון לקח 3.9 שנ׳ ל-TTFB; origin באמסטרדם (Railway ams1) | בדיקה חיה | [Core Web Vitals](https://developers.google.com/search/docs/appearance/core-web-vitals) |
| G17 | דף 502 רגעי בזמן deploy (נמדד 2026-10-30 01:06 UTC). אם Googlebot פוגש 5xx לעתים קרובות, הוא מאט את הסריקה | בדיקה חיה | [HTTP status codes and Googlebot](https://developers.google.com/search/docs/crawling-indexing/http-network-errors) |
| G18 | אין אותות מחוץ לאתר: Wikidata, Google Business Profile (לא רלוונטי, ראו 4.4), כמעט אין backlinks, אין ערוצים חברתיים מקושרים | Organization ללא sameAs | [Organization](https://developers.google.com/search/docs/appearance/structured-data/organization) |

---

## 2. מפת דרכים מתועדפת

### 2.1 Quick wins (השבוע)

#### Q1. להוציא "לא-אירועים" מ-Event markup ומהאינדקס
- **מה:** קטגוריות `happy_hour` ו"מקומות" של HappyTLV (וכל מקור שמייצר "אירוע ליום פתיחה"):
  1. בלי `Event` JSON-LD;
  2. `noindex` לדפים `/event/` שלהם, והוצאה מה-sitemap ומ-IndexNow;
  3. החרגה מספירות ה-FAQ ומ-`/tonight` (או אזור "מקומות פתוחים הערב" נפרד, בלי Event).
  אם רוצים להשאיר אותם גלויים לחיפוש, דף מקום אחד לכל עסק עם `LocalBusiness`/`BarOrPub` ושעות פתיחה (`openingHoursSpecification`), ולא דף ליום.
- **למה:** Google מונה שעות פעילות, קופונים ומבצעים כאסורים לסימון אירוע ([Event guidelines](https://developers.google.com/search/docs/appearance/structured-data/event#guidelines)). הפרה יכולה להוביל ל-manual action שמוריד את כל ה-rich results של האתר ([sd-policies](https://developers.google.com/search/docs/appearance/structured-data/sd-policies)). אלפי דפים כמעט זהים ליום פתיחה הם "scaled content" ([Spam policies](https://developers.google.com/search/docs/essentials/spam-policies)).
- **מאמץ:** S–M · **השפעה:** גבוהה (מגן מפני עונש, משפר את איכות `/tonight`)
- **מדידה:** Search Console > Manual actions (נקי); Page indexing: ירידה מתוכננת ב-"Indexed" של `/event/` ועלייה ב-"Excluded by noindex"; CTR של `/tonight` ב-Performance.

#### Q2. לתקן את ה-FAQ שמחושב מהנתונים
- **מה:** ספירה אמיתית (`count()` ולא `len(rows[:500])`). "הכרטיס הכי זול הערב" רק מאירועים שמתחילים אחרי 17:00 ושהם אירועים אמיתיים. אין משפט אם הנתון ריק.
- **למה:** תוכן מדויק וברור הוא הבסיס ([Helpful content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content)), ומנועי AI מצטטים בדיוק את המשפטים האלה.
- **מאמץ:** S · **השפעה:** בינונית
- **מדידה:** בדיקה ידנית של 10 דפי נחיתה; מעקב ציטוטים (סעיף 3).

#### Q3. לתקן את Event JSON-LD לפי המסמך
- **מה:**
  - `@type` לפי קטגוריה: `MusicEvent` (מוזיקה/מסיבות), `ComedyEvent` (סטנדאפ), `TheaterEvent`, `ScreeningEvent` (קולנוע), `Festival`, `EducationEvent` (הרצאות/כנסים), `ChildrensEvent`, `SportsEvent`, אחרת `Event`;
  - `performer` רק מליינאפ אמיתי (`Person`/`PerformingGroup`), ולא המקום;
  - ניקוי null-ים מקוננים ב-`address`; `addressLocality` מהעיר המזוהה (geo.py) כשחסר;
  - `inLanguage` לפי שפת התיאור;
  - `eventStatus` מהמקור כשהוא קיים (cancelled/postponed), ואם אירוע בוטל: להשאיר את הדף עם `EventCancelled` עד התאריך, ולא 410 מיידי. התיעוד אומר "change only the status, don't remove startDate".
- **למה:** [Event structured data](https://developers.google.com/search/docs/appearance/structured-data/event); [sd-policies: התאמה לתוכן](https://developers.google.com/search/docs/appearance/structured-data/sd-policies). גם בלי rich result בישראל, זה מה ש-Bing, Gemini ו-ChatGPT קוראים. Bing תומך ב-Event ([Bing markup](https://www.bing.com/webmasters/help/marking-up-your-site-with-structured-data-3a93e731)).
- **מאמץ:** S–M · **השפעה:** בינונית
- **מדידה:** לפי כלל הבית, כל האירועים עוברים ולידציה: סקריפט שמריץ על כל ~28 אלף האירועים את הבדיקות (שדות חובה, אין null, type תואם קטגוריה), ועוד [Rich Results Test](https://search.google.com/test/rich-results) על 20 דוגמאות מכל מקור. Search Console > Enhancements > Events (בעיקר דפי `?lang=en`).

#### Q4. lastmod אמיתי
- **מה:** עמודה `content_changed_at` שמתעדכנת רק כשמשתנה שדה שמוצג בדף (כותרת, תאריך, מקום, מחירים, זמינות, תמונה, ליינאפ). lastmod = הערך הזה. לדפי נחיתה: התאריך שבו רשימת האירועים השתנתה בפועל, ולא "היום" קבוע.
- **למה:** [Build a sitemap: lastmod](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap) ("consistently and verifiably accurate").
- **מאמץ:** M (עמודה + עדכון ב-upsert + עדכון `docs/postgres-migration.md` לפי הכלל) · **השפעה:** גבוהה לקצב הגילוי של אירועים חדשים ועדכונים
- **מדידה:** Settings > Crawl stats: יחס "Refresh" ל-"Discovery", זמן מפרסום אירוע עד אינדוקס (URL Inspection על מדגם).

#### Q5. לפצל את ה-sitemap לפי סוג
- **מה:** `sitemap-events-N.xml`, `sitemap-landing.xml`, `sitemap-venues.xml`, `sitemap-artists.xml`, `sitemap-blog.xml` תחת אותו אינדקס. בלי שינוי בתוכן.
- **למה:** Search Console מציג אינדוקס לכל sitemap בנפרד ([Sitemaps report](https://support.google.com/webmasters/answer/7451001)), וכך רואים איזה סוג דף לא נכנס לאינדקס. הגשה אוטומטית אפשרית דרך [Search Console Sitemaps API](https://developers.google.com/webmaster-tools/v1/sitemaps).
- **מאמץ:** S · **השפעה:** בינונית (מדידה)
- **מדידה:** Sitemaps report, Discovered/Indexed לכל קובץ.

#### Q6. Organization ושם האתר
- **מה:** להוסיף ל-`Organization`: `sameAs` (Instagram, TikTok, Facebook, WhatsApp channel, LinkedIn, Wikidata כשיהיה), `contactPoint` עם `contact@spotx.fun` (דרך הצורה המוסתרת הקיימת), `description`, `alternateName: ["ספוטאקס", "SpotX"]`; ול-`WebSite` להוסיף `alternateName`.
- **למה:** [Organization](https://developers.google.com/search/docs/appearance/structured-data/organization); [Site names](https://developers.google.com/search/docs/appearance/site-names) (WebSite + alternateName בדף הבית).
- **מאמץ:** S · **השפעה:** בינונית (ישות/מותג, Knowledge Panel בעתיד)
- **מדידה:** שם האתר שמוצג בתוצאות; חיפוש "spotx" / "ספוטאקס" ב-GSC.

#### Q7. Search Console + Bing Webmaster: לוודא את הבסיס
- **מה:** לאמת את `spotx.fun` כ-Domain property (DNS); להגיש את ה-sitemap; לפתוח ב-Bing Webmaster Tools (ייבוא מ-GSC) ולהפעיל את דוח **AI Performance**, שמציג ציטוטים ב-Copilot ובסיכומי AI של Bing (public preview מפברואר 2026, [Search Engine Land](https://searchengineland.com/bing-webmaster-tools-testing-new-ai-performance-report-468039)). ה-meta של Bing (`msvalidate.01`) ריק כרגע בדף החי.
- **למה:** [AI features: verify in Search Console](https://developers.google.com/search/docs/appearance/ai-features); תנועת AI Overviews / AI Mode נספרת תחת "Web" ב-Performance.
- **מאמץ:** S · **השפעה:** גבוהה (בלי זה אין מדידה)

#### Q8. llms.txt רזה
- **מה:** לקצר ל-~10–20KB: תיאור, דפים מרכזיים, דפי נחיתה, בלוג ו-API. את רשימת האירועים להעביר ל-`/llms-full.txt` (קונבנציה של llmstxt.org) או להשאיר ל-sitemap.
- **למה:** Google לא משתמש ב-llms.txt ([AI features](https://developers.google.com/search/docs/appearance/ai-features)); התקן ([llmstxt.org](https://llmstxt.org)) מגדיר "concise". זה תחזוקה זולה, לא מנוע צמיחה. לא להשקיע בזה יותר.
- **מאמץ:** S · **השפעה:** נמוכה

### 2.2 תוך 30 יום

#### M1. דף אירוע עם תוכן ייחודי
- **מה:** בתוך ה-SSR של כל אירוע להוסיף בלוקים שלנו, מנתונים שכבר יש:
  - **סיכום קצר בעברית** (מי, מה, איפה, מתי, כמה, גיל), בנוי מהשדות, בראש הדף. זו פסקת התשובה ש-AI מצטט;
  - **מזג אוויר** (`weather.py` קיים, ולפי הכלל כל אירוע עם מקום מציג מזג אוויר);
  - **"איך מגיעים"** (כתובת, מפה, אזור/שכונה);
  - **אירועים דומים** (אותו ז׳אנר/מקום/ערב) וקישורים לדף העיר×ז׳אנר ולדף המקום;
  - **השוואת מחירים** כשאותו אירוע נמכר ביותר מאתר אחד (dedup כבר מקשר). זה ערך ייחודי שאין למוכרים עצמם;
  - את תיאור המוכר לקצר לתקציר עם "המשך במוכר", במקום 2,000 תווים מועתקים.
- **למה:** [Helpful content: "provide original information… substantial value compared to other pages"](https://developers.google.com/search/docs/fundamentals/creating-helpful-content); [Links crawlable](https://developers.google.com/search/docs/crawling-indexing/links-crawlable) לקישור פנימי.
- **מאמץ:** M · **השפעה:** גבוהה (זה ההבדל בין אגרגטור דק לבין מקור)
- **מדידה:** GSC Performance על `/event/` (impressions/clicks), Page indexing ("Crawled - currently not indexed" אמור לרדת), GA4 engagement ו-click-out ל-`/go/`.

#### M2. דפי נחיתה חסרים לשאילתות עם כוונה ברורה
מומלץ להוסיף (רק עם סף מינימום אירועים ו-intro ייחודי לכל דף, כדי לא להיחשב scaled content):

| דף מוצע | שאילתה בעברית | הערות |
| --- | --- | --- |
| `/tomorrow`, `/city/<c>/tomorrow` | "מה עושים מחר", "מסיבות מחר בתל אביב" | מתבסס על WHENS הקיים |
| `/thursday`, `/friday`, `/saturday` (+עיר) | "מסיבות שישי בתל אביב", "מה עושים במוצאי שבת" | חמישי ושישי הם לילות השיא |
| `/this-week`, `/genre/live/this-week` | "הופעות השבוע", "מה קורה השבוע" | |
| `/free`, `/city/<c>/free` | "אירועים בחינם", "מה עושים בחינם בתל אביב" | יש פוסט, אין דף נחיתה חי |
| `/city/tel-aviv/area/<florentin…>` | "מסיבות בפלורנטין", "ברים ביפו" | `area` כבר קיים ב-API |
| `/kids`, `/city/<c>/kids` | "מה עושים עם ילדים בסופ״ש" | נפח חיפוש עונתי גבוה |
| `/month/<yyyy-mm>` | "אירועים בנובמבר 2026" | במקום פוסט חודשי ידני |
| חגים: `/holiday/<hanukkah-2026>` | "מסיבות חנוכה", "מסיבות סילבסטר 2026" | הפוסטים קיימים; לחבר לדף נחיתה חי ולקשר ביניהם, כדי שלא יתחרו זה בזה על אותה שאילתה |

- **למה:** [Helpful content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content); [Spam policies: scaled content abuse](https://developers.google.com/search/docs/essentials/spam-policies), ולכן הסף והטקסט הייחודי. ניווט פנים (faceted) רק בדפים שהוגדרו, לא כל צירוף פילטרים ([Faceted navigation](https://developers.google.com/search/docs/crawling-indexing/crawling-managing-faceted-navigation)).
- **שינוי בכל המשטחים:** כל דף חדש מגיע גם לפילטר, לחידון, למפה, לבלוג, ל-i18n ול-llms.txt.
- **מאמץ:** M–L · **השפעה:** גבוהה
- **מדידה:** GSC queries לכל דף חדש; Position/CTR אחרי 4–8 שבועות.

#### M3. מחקר מילות מפתח בעברית מנתונים אמיתיים
- **מה:** לא לנחש נפחים. (1) ייצוא שאילתות מ-GSC (16 חודשים), סינון של impressions גבוהים ב-position 8–30, ואלה ההזדמנויות; (2) [Google Keyword Planner](https://ads.google.com/home/tools/keyword-planner/) לישראל/עברית; (3) [Google Trends](https://trends.google.com/trends/explore?geo=IL) לעונתיות (חגים, קיץ, פסטיבלים). צריך לכסות וריאנטים: "סופ״ש / סופש / סוף שבוע", "מסיבות / מסיבה / ליינים", "הופעות / הופעה חיה", "היום / הערב / הלילה".
- **מועמדים לבדיקה:** מה עושים הערב · מה עושים בסופ״ש · מסיבות היום בתל אביב · הופעות השבוע · מסיבות טכנו · מסיבות טבע · סטנדאפ הערב · אירועים בחינם · מה עושים עם ילדים · מסיבות סילבסטר 2026 · מסיבות חנוכה · הופעות בקיסריה · מה עושים בחיפה/ירושלים/באר שבע · מסיבות 18+ · מסיבות להט״ב · ליין חמישי.
- **מאמץ:** S (חוזר כל חודש) · **השפעה:** גבוהה (מכוון את M2 ואת הבלוג)
- **מדידה:** טבלת מילים מול דף יעד מול מיקום, פעם בחודש.

#### M4. בלוג: עומק, מחבר, רעננות
- **מה:**
  - להאריך פוסטים "דקים" (פחות מ-600 מילים) עם ידע אמיתי: מחירים טיפוסיים, שעות, קוד לבוש, תחבורה, מקומות מומלצים לפי שכונה;
  - **מחבר אנושי** עם דף מחבר (`/about/<name>`) ו-`author: Person` בפוסטים;
  - `dateModified` אמיתי כשהטקסט נערך (לא כשרשימת האירועים מתעדכנת לבד);
  - דף `/about` שמסביר מי אנחנו, איך נאספים הנתונים, באיזו תדירות ומאילו אתרים. זה ה-"how" שGoogle מבקש.
  כל פוסט גם באנגלית, לפי הכלל.
- **למה:** [Helpful content: Who / How / Why, E-E-A-T](https://developers.google.com/search/docs/fundamentals/creating-helpful-content); [Article structured data: author](https://developers.google.com/search/docs/appearance/structured-data/article).
- **מאמץ:** M (תוכן) · **השפעה:** בינונית–גבוהה
- **מדידה:** GSC לכל פוסט; ציטוטים ב-AI.

#### M5. 304 / ETag לדפי HTML
- **מה:** ETag = hash של ה-HTML שבמטמון (`html_cache` כבר מחזיק bytes), ו-304 ב-`If-None-Match`.
- **למה:** [Crawl budget: "support 304"](https://developers.google.com/search/docs/crawling-indexing/large-site-managing-crawl-budget); זה חוסך משאבי סריקה ב-~125 אלף URL.
- **מאמץ:** S · **השפעה:** בינונית
- **מדידה:** Crawl stats > By response: חלק ה-304 עולה.

#### M6. Core Web Vitals: למדוד ואז לתקן
- **מה:**
  - שליחת [web-vitals](https://github.com/GoogleChrome/web-vitals) ל-GA4 (LCP/INP/CLS לפי סוג דף), כלומר נתוני RUM;
  - דוח CWV ב-GSC ו-[PageSpeed Insights](https://pagespeed.web.dev/) לדפים `/`, `/event/`, `/tonight`, `/city/tel-aviv`;
  - בדיקת מסך ה-boot: הוא מכסה את התוכן עד שה-React מצייר. אם LCP נדחה בגללו, לקצר אותו או להציג את ה-SSR במקומו;
  - שקילת cache ב-Cloudflare לדפי HTML לכמה דקות (כרגע `cf-cache-status: DYNAMIC`, origin באמסטרדם) כדי לקצר את ה-TTFB לגולשים בישראל.
- **למה:** [Core Web Vitals](https://developers.google.com/search/docs/appearance/core-web-vitals), [Page experience](https://developers.google.com/search/docs/appearance/page-experience). CWV הם חלק מאותות הדירוג, לא העיקר: לתקן רק אם הם "Poor/Needs improvement".
- **מאמץ:** S למדידה, M לתיקון · **השפעה:** בינונית
- **מדידה:** GSC Core Web Vitals report (URL groups), CrUX.

#### M7. תמונות אירוע ל-Google
- **מה:** להגיש לכל אירוע שלושה חיתוכים (16:9, 4:3, 1:1) ברוחב 1200px ומעלה, מאותה תמונה מקורית (לא artwork שנוצר, לפי הכלל), עם סיומת `.webp` ב-URL; ב-`image` ב-JSON-LD רק אם המקור גדול מספיק.
- **למה:** [Event: image](https://developers.google.com/search/docs/appearance/structured-data/event); [Google Images best practices](https://developers.google.com/search/docs/appearance/google-images); Discover דורש תמונות של 1200px ומעלה ([Discover](https://developers.google.com/search/docs/appearance/google-discover)).
- **מאמץ:** M · **השפעה:** בינונית (Images, Discover, אירועים באנגלית)

#### M8. יציבות: אפס 5xx בזמן deploy
- **מה:** health check ו-zero-downtime ב-Railway (שירות ישן נשאר עד שהחדש עונה), וניטור 5xx לדפים ציבוריים.
- **למה:** [HTTP status codes and Googlebot](https://developers.google.com/search/docs/crawling-indexing/http-network-errors): 5xx חוזרים מורידים את קצב הסריקה.
- **מאמץ:** S–M · **השפעה:** בינונית
- **מדידה:** Crawl stats > Host status (Server connectivity).

### 2.3 תוך 90 יום

#### L1. דפי מקום ואמן כישויות אמיתיות
- **מה:** דף מקום עם כתובת מלאה, מפה, שעות (כשיש), "מה בדרך כלל יש פה" (ז׳אנרים, מחיר ממוצע, גיל), אירועים קרובים וקישור לאתר הרשמי (`sameAs`); `@type` מדויק (`NightClub`, `BarOrPub`, `MusicVenue`, `PerformingArtsTheater`). דף אמן עם `MusicGroup`/`Person`, ז׳אנר, `sameAs` ל-Spotify/Instagram (`artistdb.py` קיים) והופעות קרובות. URL-ים בסגנון slug (`/venue/barby`) ולא `Hameretz%202`, עם 301 מהישן.
- **למה:** [Helpful content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content); [URL structure](https://developers.google.com/search/docs/crawling-indexing/url-structure) ("simple, descriptive words"); [Redirects](https://developers.google.com/search/docs/crawling-indexing/301-redirects).
- **מאמץ:** L · **השפעה:** גבוהה (שאילתות "<מועדון> אירועים", "<אמן> הופעות", שהן נפח חיפוש גדול בעברית)

#### L2. GEO/AEO: תוכן שמצוטט
מה מצוטט בפועל במנועי תשובות לשאילתות "מה עושים…": דף שעונה בפסקה הראשונה, עם נתונים מספריים טריים, תאריך עדכון גלוי ורשימה מובנית. ב-SPOTX זה אומר:
- **פסקת תשובה** בראש כל דף נחיתה ("הערב בתל אביב: 84 אירועים; הבולטים: X ב-Y (22:00, ₪80), Z…; 6 בכניסה חופשית"), מחושבת ונכונה (Q2);
- **"עודכן לאחרונה: 30/10 14:05"** גלוי בכל דף נחיתה ובכל פוסט חי;
- **טבלאות** (שעה · אירוע · מקום · מחיר) ב-SSR, שהן מבנה קל לחילוץ;
- **דפי "מדריך"** עם עובדות יציבות (שכונות, קוד לבוש, תחבורה בלילה, גילאי כניסה), כי AI מצטט עובדות כאלה;
- **נתונים מקוריים:** דוח חודשי "מדד חיי הלילה" (כמה אירועים, מחיר ממוצע לכרטיס לפי ז׳אנר/עיר, ערים צומחות). זה גם חומר יחסי ציבור (L4).
- **למה:** Google: "no special optimizations… SEO fundamentals apply; keep important content in text form" ([AI features](https://developers.google.com/search/docs/appearance/ai-features)). ChatGPT search נשען על OAI-SearchBot ועל אינדקס Bing ([OpenAI bots](https://platform.openai.com/docs/bots)), ו-Perplexity על PerplexityBot ([Perplexity bots](https://docs.perplexity.ai/guides/bots)). כולם פתוחים ב-robots, וכדאי לוודא שהם לא נחסמים ב-rate-limit (ראו הערה בהמשך).
- **מאמץ:** M–L · **השפעה:** גבוהה
- **מדידה:** סעיף 3.

> **הערה טכנית ל-GEO:** ה-rate limit (300 בקשות/דקה, ו-429 על `/go/`) צריך להבחין בין בוטים מאומתים (Googlebot, Bingbot, OAI-SearchBot, PerplexityBot, Claude-SearchBot) לבין קוצרי נתונים, באימות לפי reverse DNS / טווחי IP שהספקים מפרסמים ([Verify Googlebot](https://developers.google.com/search/docs/crawling-indexing/verifying-googlebot)). אסור שבוט חיפוש יקבל 403 על דפים.

#### L3. ישות מותג ו-Knowledge Panel
- **מה:**
  1. ערך ב-**Wikidata** ל-SPOTX (אתר, מייסד, תאריך, תחום, פרופילים), ולקשר אותו ב-`sameAs`;
  2. פרופילים עקביים באותו שם ולוגו בכל רשת;
  3. אזכורים בתקשורת (L4), שהם מה שבונה ישות;
  4. Knowledge Panel יופיע מעצמו כשיש מספיק אותות. אחרי שמופיע, אפשר לתבוע אותו (claim).
- **Google Business Profile: לא רלוונטי.** GBP מיועד לעסקים עם מגע פנים-אל-פנים עם לקוחות. עסק אונליין בלבד לא זכאי ([Business Profile guidelines](https://support.google.com/business/answer/3038177)), ופתיחה בכל זאת מסתכנת בהשעיה.
- **מאמץ:** M (מתמשך) · **השפעה:** בינונית–גבוהה לטווח ארוך
- **מדידה:** חיפושי מותג ב-GSC ("spotx", "ספוטאקס"); הופעת Knowledge Panel.

#### L4. קישורים נכנסים ויחסי ציבור (בלי לקנות קישורים)
- **מה:**
  - **מדיה:** פיץ׳ ל-Time Out תל אביב, Ynet בילויים, Mako, הארץ גלריה, Walla תרבות, Secret Tel Aviv, ערוצי טלגרם וחדשות עירוניות, עם "מדד חיי הלילה" החודשי (נתון מקורי שעיתונאים מצטטים ומקשרים) ועם "מה עושים בסופ״ש" כמקור קבוע לטורים;
  - **מקומות ומפיקים:** "Featured on SPOTX" / ווידג׳ט "האירועים הבאים שלנו" שמקום יכול להטמיע (iframe או JS עם קישור חזרה). ערך אמיתי למקום, קישור טבעי לנו;
  - **עיריות ומתנ״סים** (כבר מקורות: munievents, matnasnet): להציע להם עמוד "כל האירועים בעיר" שמקשר;
  - **אוניברסיטאות / אגודות סטודנטים:** לוח אירועי סטודנטים;
  - **אסור:** קניית קישורים, החלפות קישורים בהיקף, PBN ([Spam policies: link spam](https://developers.google.com/search/docs/essentials/spam-policies#link-spam)). קישורים ממומנים או משותפים מסמנים `rel="sponsored"` ([Qualify outbound links](https://developers.google.com/search/docs/crawling-indexing/qualify-outbound-links)).
- **מאמץ:** L (מתמשך) · **השפעה:** גבוהה. זה החסם הגדול ביותר לדירוג בעברית מול המתחרים הוותיקים.
- **מדידה:** GSC > Links (Top linking sites, נספר כל חודש); referral traffic ב-GA4; אזכורי מותג.

#### L5. רשתות חברתיות ו-WhatsApp
- **מה:**
  - **ערוץ WhatsApp** "מה עושים הסופ״ש" (שלישי/חמישי) עם 5–8 אירועים וקישור לדף הנחיתה (UTM לפי הכלל, לייחוס בלבד);
  - **Instagram/TikTok:** סרטון 20 שנ׳ "3 מסיבות לסופ״ש בתל אביב", מנתונים שכבר יש. אפשר להפיק אוטומטית רשימה ותמונות (ה-artwork הקיים) ולערוך ידנית;
  - **טלגרם:** ערוץ לכל עיר/ז׳אנר, אוטומטי;
  - **שיתוף מהאתר:** ה-Groups ("מי בא?") כבר מייצרים תצוגה מקדימה ל-WhatsApp, שהיא לולאה ויראלית. למדוד אותה.
- **למה:** אלה לא אותות דירוג ישירים, אבל הם מביאים חיפושי מותג, אזכורים ו-`sameAs`, וטראפיק שלא תלוי ב-Google.
- **מאמץ:** M (תפעול שבועי) · **השפעה:** בינונית–גבוהה
- **מדידה:** GA4 לפי source/medium (UTM); עוקבים; חיפושי מותג.

#### L6. ניוזלטר
- **מה:** המייל השבועי (alerts קיים) כמוצר עצמאי: הרשמה בלי חשבון (רק מייל, double opt-in), "הסופ״ש שלך" לפי עיר וז׳אנר, ארכיון ציבורי של גיליונות כדפים ניתנים לאינדוקס (`/newsletter/2026-10-29`).
- **מאמץ:** M · **השפעה:** בינונית (שימור, חיפושי מותג)
- **מדידה:** נרשמים, open/click, חוזרים ב-GA4.

#### L7. שותפויות עם אתרי כרטיסים
- **מה:** פנייה לאתרי הכרטיסים (Go-Out, Eventer, Tixwise, Leaan, Eventim…): פיד רשמי (API/feed) במקום סריקה, ובתמורה תנועה מתויגת, קישור "Listed on SPOTX" ובהמשך שותפים (affiliate, כרגע UTM לייחוס בלבד). פיד רשמי גם פותר את מגבלות החסימה (Chillz, MFW).
- **מאמץ:** L · **השפעה:** בינונית–גבוהה (נתונים טובים יותר, קישורים, הכנסה)

#### L8. אנגלית: קהל תיירים
- **מה:** הגרסה האנגלית היא הזדמנות אמיתית: תיירים ותושבים דוברי אנגלית מחפשים "things to do in Tel Aviv tonight", "Tel Aviv techno parties". כאן Event rich results **כן** יכולים להופיע (משתמשים בארה״ב/בריטניה שמתכננים טיול). צריך: כותרות ותיאורים באנגלית מלאים, ותרגום של שדות האירוע (כותרת, תיאור) כשהם בעברית.
- **למה:** [Managing multilingual sites](https://developers.google.com/search/docs/specialty/international/managing-multi-regional-sites): "translate the content, not only the template". דף באנגלית שכולו טקסט בעברית נחשב כמעט כפילות.
- **מאמץ:** M–L · **השפעה:** בינונית
- **מדידה:** GSC Performance, פילטר Country ≠ Israel ו-`?lang=en`; Enhancements > Events.

---

## 3. מדידה: ציטוטים ב-AI

| ערוץ | איך מודדים |
| --- | --- |
| Google AI Overviews / AI Mode | נכלל ב-GSC Performance > Web ([AI features](https://developers.google.com/search/docs/appearance/ai-features)); אין פילוח נפרד. מעקב ידני: 30 שאילתות קבועות (סעיף M3), פעם בשבוע, לרשום אם SPOTX מצוטט |
| Bing Copilot / AI summaries | Bing Webmaster Tools > **AI Performance** (ציטוטים לפי דף ולפי grounding query) |
| ChatGPT | GA4: referral מ-`chatgpt.com` (ChatGPT מוסיף `utm_source=chatgpt.com`); לוגים: בקשות `OAI-SearchBot` ו-`ChatGPT-User` לכל דף |
| Perplexity | GA4 referral `perplexity.ai`; לוגים `PerplexityBot` / `Perplexity-User` |
| Claude | לוגים `Claude-SearchBot` / `Claude-User`; GA4 referral `claude.ai` |
| Gemini | GA4 referral `gemini.google.com` |

- **בניית "AI Visibility panel" פנימי:** סקריפט שבועי שמריץ את 30 השאילתות (עברית ואנגלית) מול ה-API של המנועים שמחזירים מקורות (Perplexity, OpenAI web search, Gemini עם grounding), ושומר האם `spotx.fun` מצוטט ובאיזה URL. כפיצ׳ר, בתיקייה משלו (לפי הכלל), למשל `scripts/ai_visibility/`.
- **ספירת בוטי AI בלוגים** (כבר יש `logs.py`): טבלה יומית לפי user-agent ולפי סוג דף, אחרי אימות IP.
- **GA4:** ערוץ מותאם "AI Assistants" (Channel group) שמאגד את ה-referrers שלמעלה.

---

## 4. KPI

| KPI | מקור | בסיס (למלא עכשיו) | יעד 30 יום | יעד 90 יום |
| --- | --- | --- | --- | --- |
| קליקים אורגניים מ-Google (שבועי) | GSC Performance | | +20% | ×2 |
| Impressions לשאילתות לא-מותג | GSC (סינון "spotx/ספוטאקס" החוצה) | | +30% | ×2.5 |
| מספר שאילתות בטופ 10 | GSC | | +25% | ×2 |
| חיפושי מותג (spotx / ספוטאקס) | GSC | | +20% | ×2 |
| אחוז דפי `/event/` מאונדקסים מתוך ה-sitemap | GSC Sitemaps (אחרי Q5) | | +10 נק׳ | ≥70% |
| זמן מפרסום אירוע עד אינדוקס (חציון) | URL Inspection על מדגם של 20 | | ≤3 ימים | ≤24 שעות |
| שגיאות Event ב-Enhancements | GSC | | 0 | 0 |
| Manual actions | GSC | 0 | 0 | 0 |
| 5xx ב-Crawl stats | GSC Host status | | <0.1% | <0.1% |
| CWV "Good" (נייד) | GSC CWV / CrUX | | ≥75% מה-URL-ים | ≥90% |
| ציטוטים ב-Bing Copilot | Bing AI Performance | | בסיס | ×3 |
| ציטוט ב-30 השאילתות הקבועות | סקריפט AI visibility | | בסיס | ≥10 מתוך 30 |
| סשנים מ-AI referrers | GA4 ערוץ AI Assistants | | בסיס | ×3 |
| דומיינים מקשרים (referring domains) | GSC Links | | +10 | +40 |
| click-out ל-`/go/` מתנועה אורגנית | GA4 + `clicks.py` | | +20% | ×2 |
| נרשמים לערוץ WhatsApp / ניוזלטר | WhatsApp / mail | 0 | 500 | 3,000 |

---

## 5. סדר ביצוע מוצע

1. **השבוע:** Q7 (מדידה) → Q1 (לא-אירועים) → Q2 (FAQ) → Q3 (JSON-LD + ולידציה על כל האירועים) → Q5 (פיצול sitemap) → Q6 (Organization) → Q4 (lastmod) → Q8.
2. **חודש:** M3 (מחקר מילים) → M2 (דפי נחיתה) → M1 (דף אירוע ייחודי) → M5 / M8 (304, יציבות) → M6 (CWV) → M4 (בלוג) → M7.
3. **רבעון:** L4 + L5 + L6 במקביל (שיווק, מתמשך) · L1 (מקומות/אמנים) · L2 (GEO) · L3 (ישות) · L7 · L8.

**מה לא לעשות:**
- לא להשקיע ב-FAQPage markup בשביל Google (הוסר ממאי 2026);
- לא להרחיב את llms.txt;
- לא לייצר אלפי דפי צירופים בלי סף ובלי טקסט ייחודי;
- לא לקנות קישורים;
- לא לפתוח Google Business Profile לעסק אונליין;
- לא לצפות לכרטיסי אירוע עשירים בעברית ב-Google.

## מקורות עיקריים
- Event structured data: https://developers.google.com/search/docs/appearance/structured-data/event
- Structured data policies: https://developers.google.com/search/docs/appearance/structured-data/sd-policies
- AI features and your website: https://developers.google.com/search/docs/appearance/ai-features
- Build a sitemap: https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap
- Crawl budget (large sites): https://developers.google.com/search/docs/crawling-indexing/large-site-managing-crawl-budget
- HTTP status / network errors: https://developers.google.com/search/docs/crawling-indexing/http-network-errors
- Localized versions (hreflang): https://developers.google.com/search/docs/specialty/international/localized-versions
- Helpful content: https://developers.google.com/search/docs/fundamentals/creating-helpful-content
- Spam policies: https://developers.google.com/search/docs/essentials/spam-policies
- Organization / Site names: https://developers.google.com/search/docs/appearance/structured-data/organization · https://developers.google.com/search/docs/appearance/site-names
- Core Web Vitals: https://developers.google.com/search/docs/appearance/core-web-vitals
- Search Central updates (FAQ deprecation): https://developers.google.com/search/updates
- OpenAI crawlers: https://platform.openai.com/docs/bots · Perplexity: https://docs.perplexity.ai/guides/bots · llms.txt: https://llmstxt.org
- Bing AI Performance report: https://searchengineland.com/bing-webmaster-tools-testing-new-ai-performance-report-468039
