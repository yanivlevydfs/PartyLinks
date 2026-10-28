# תוכנית מעבר: SPOTX / PartyLinks מ-SQLite ל-PostgreSQL

> מסמך תכנון בלבד. לא שונה שום קובץ בריפו, לא הורצה שום migration ולא נגעו ב-Railway.
> כל הפניה `file:line` נבדקה מול `master` ב-411ce2a. טבלאות הפרסום (`sponsored`, `sponsored_stats`) ו-`flags`, וכן `_literal_default` ו-`create_schema`, עודכנו מול `master` ב-4a1a6a7 (ראו "עדכונים" בסוף).
> נכתב לפי שיטת brainstorming: המשתמש לא זמין לשאלות, אז כל שאלה מופיעה ב"שאלות פתוחות" עם ברירת מחדל מומלצת, והתכנון ממשיך לפיה.

---

## 0. תקציר

- **ההמלצה:** לעבור, בשלבים, אחרי שלב A ("להכין את הקוד לשני ה-dialects"), שמשתלם גם אם בסוף לא עוברים. ה-ORM כבר כתוב בחלקו עם PostgreSQL בראש (`db.py:535`, `db.py:684`, `db.py:778`, `api.py:751`, `docs/configuration.md:12`), אז העלות נמוכה מהצפוי.
- **ההחלפה עצמה היא משתנה ב-Railway ולא commit.** ‏`PARTYLINKS_DATABASE__URL` מחליף DB, והסרתו מחזירה ל-SQLite. כך push-ים של sessions אחרים ל-master לא יכולים לשבור את ה-cutover, ו-rollback הוא שינוי של משתנה אחד.
- **Driver:** ‏psycopg 3 sync (`postgresql+psycopg://`). הקוד כולו sync (threads ו-`asyncio.to_thread`), אז asyncpg לא רלוונטי.
- **העתקת הנתונים:** פקודת `copy-db` קטנה בתוך `cli.py`, שעובדת דרך ה-SQLAlchemy models עצמם וכך שומרת על אותם types ו-JSON. היא כוללת אימות של מספרי שורות ו-checksum לכל טבלה, ועובדת בשני הכיוונים, כך שגם rollback של נתונים משתמש בה.
- **Downtime:** בערך כמו deploy רגיל, דקה או שתיים (`.railway/railway.ts` עם `overlapSeconds: 0`; עד 2026-10-28 `railway.json`). ה-scheduler כבוי לכ-20–30 דקות, אז ה"נזק" הוא נתונים פחות טריים, לא אתר למטה.
- **שינוי ההתנהגות מכוון להיות אפס בשלב C.** ה-writer lock התהליכי נשאר גם על PG. רק בשלב D, אחרי שבוע יציב, מסירים אותו ומקבלים את התועלת העיקרית.

---

## 1. הבנת המצב (Understanding Summary)

- **מה בונים:** החלפת ה-DB של prod מקובץ SQLite על volume ב-Railway (`/app/data/partylinks.db`) לשירות Postgres מנוהל באותו פרויקט Railway.
- **למה:** כל המקורות (74 ומעלה) כותבים דרך נעילת כתיבה אחת. כבר הייתה הרעבה של writers (`service.py:84-88`, אוקטובר 2026). יש גם retries על "database is locked" (`db.py:56-66`), חלונות VACUUM, ותפעול מוגבל: אי אפשר לשאול את ה-DB בלי להיכנס לקונטיינר.
- **למי:** האתר spotx.fun, ה-admin וה-scheduler. תהליך אחד, replica אחת.
- **אילוצים:** כ-28k אירועים. יש passes על כל הטבלה אחרי כל scrape (regroup, geocode, areas, retention). פעם O(n²) הפיל את prod, אז ביצועים בגודל prod הם תנאי. ה-Docker מתקין את `requirements.lock` (`Dockerfile:31`). sessions אחרים דוחפים ל-master במקביל, ו-Railway עושה auto-deploy. אסור לשמור אירועי עבר. גודל DB ורוחב פס לא מגבילים. ב-localhost ה-scheduler תמיד כבוי.
- **Non-goals:** לא multi-replica (ה-scheduler, ה-`ResponseCache` (`api.py:410-454`), ה-bans (`security.py:130`) וה-`Database.changes` (`db.py:415-418`) כולם בזיכרון של התהליך). לא Alembic. לא PostGIS. לא async. לא להזיז את `artists.db` (קובץ SQLite נפרד, `artists.py:1,16,70-74`), את `weather_cache.json`, את `chillz_past.json` או את `geonames-<version>.json` (מטמון הערים, 2026-10-28). כולם נשארים על ה-volume.

### Non-functional (הנחות)
| נושא | הנחה |
|---|---|
| ביצועים | ‏p95 של `/events` ו-`/events/map` לא גרוע ממה שיש היום. regroup, geocode(network=False) ו-purge בגודל prod לא יותר מפי 1.5 מ-SQLite. |
| Scale | כ-28k אירועים, כ-28k upserts ביום, כ-3 tiers לאירוע, clicks ו-deal_changes מצטברים. |
| אמינות | rollback בתוך 5 דקות. אובדן נתונים מותר רק בנתונים שמתחדשים מעצמם (אירועים נאספים מחדש). |
| אבטחה | Postgres רק ברשת הפרטית של Railway. ה-public TCP proxy פתוח רק בזמן החזרה הכללית (rehearsal) וסגור אחריה. |
| תחזוקה | אותו קוד רץ על SQLite (local, tests) ועל PG (prod). שני ה-dialects נבדקים ב-tests. |

---

## 2. הנחות

1. Railway Postgres מגיע מה-template הרשמי (Postgres 16/17) עם volume ומשתני `DATABASE_URL` / `DATABASE_PUBLIC_URL` / `PG*`.
2. `media.storage: filesystem` (`config/settings.yaml:591`), כלומר תמונות כבר על הדיסק ו-`event_images.data` ריק כמעט לגמרי. ה-volume נשאר חובה (media, artists.db, caches).
3. localhost וה-tests ממשיכים לרוץ על SQLite כברירת מחדל.
4. עלות שירות נוסף ב-Railway (כמה דולרים בחודש) מקובלת.
5. אין CI. בתיקייה `.github/workflows` לא נמצא כלום, אז ה-tests רצים ידנית.

## 3. שאלות פתוחות (עם ברירת מחדל מומלצת)

| # | שאלה | ברירת מחדל |
|---|---|---|
| Q1 | לעבור בכלל, או להסתפק בשלב A ולהמשיך עם SQLite? | לבצע את שלב A. אחריו להחליט לפי לוגים: אם יש "database busy" או runs שנחנקים, עוברים. התועלת הגדולה מגיעה רק בשלב D. |
| Q2 | collation של ה-DB | `LC_COLLATE 'C'`, `LC_CTYPE 'C.UTF-8'`. זה שומר על סדר בינארי כמו SQLite (מזהים, tie-breakers) ועדיין מאפשר ל-pg_trgm ול-ILIKE לזהות אותיות עבריות. |
| Q3 | לשמר את סדר ה-NULL של SQLite (NULL הכי קטן) או לקבל את ברירת המחדל של PG? | לשמר במפורש (`nulls_first` ב-ASC, `nulls_last` ב-DESC) כבר בשלב A, כדי שהמעבר לא ישנה UX. שיפור UX (אירועים בלי מחיר בסוף) הוא החלטה נפרדת. |
| Q4 | ה-writer lock התהליכי (`db.py:447-450`, `db.py:568-585`) על PG | להשאיר בשלב C, להסיר בשלב D אחרי שבוע יציב. |
| Q5 | `JSON` או `JSONB` | בשלב C להשאיר `json`, כך שההתנהגות זהה ל-SQLite, כולל escape של עברית. JSONB ו-GIN רק אם מדידה תראה צורך (§9). |
| Q6 | clicks ו-sponsored_stats שנכתבים בין ה-snapshot ל-flip (דקות) | לקבל אובדן של דקות. אופציה: delta copy של `clicks` לפי `id > max`. |
| Q7 | לשמור את SQLite כ-DB מקומי/tests לתמיד? | כן. suite על PG רק opt-in דרך env var. |
| Q8 | כמה זמן לשמור את קובץ ה-SQLite הישן על ה-volume | 30 יום, ואז למחוק ידנית. |
| Q9 | לפתוח Postgres ב-environment נפרד של Railway (staging) לבדיקה? | לא. rehearsal על DB נפרד (`partylinks_rehearsal`) באותו שירות Postgres, בלי scraping. |

---

## 4. כדאיות: יתרונות מול עלות, בכנות

**יתרונות**
- כתיבות במקביל ונעילה ברמת השורה. היום כל source מחכה לנעילת קובץ אחת עם timeout של 120s (`db.py:53`, `db.py:577-578`), וכבר היה starvation (`service.py:84-88`). התועלת הזו מגיעה רק בשלב D, כשמסירים את ה-lock.
- בלי VACUUM ידני ובלי incremental vacuum, ובלי `compact_if_bloated` שצריך DB שקט (`db.py:513-553`). ‏autovacuum עושה את זה.
- תפעול: `psql` מרחוק, `pg_stat_statements`, `EXPLAIN ANALYZE`, backups מנוהלים, ושחזור לנקודת זמן ב-snapshots של Railway.
- אפשרויות שלא קיימות ב-SQLite: partial indexes מתקדמים, GIN/jsonb, pg_trgm לחיפוש עברית, HOT updates (§9).
- `ILIKE` אמיתי שמטפל ב-Unicode. ב-SQLite ‏LIKE מתעלם מאותיות גדולות/קטנות רק ב-ASCII. בעברית אין הבדל, אבל זה משנה בשמות לטיניים עם diacritics.

**עלויות וסיכונים**
- latency: כל query עובר רשת פרטית (בערך 0.3–1ms) במקום קריאה בתוך התהליך. דף עם 5–10 queries יוסיף 5–10ms. ה-`ResponseCache` מבלע את רוב זה.
- שני dialects לתחזק לתמיד: tests, ובקרה על קוד SQLite-only חדש מ-sessions אחרים.
- PG מחמיר ב-4 מקומות ש-SQLite סלחני בהם (§6.3): אורכי `VARCHAR(n)`, תו NUL, `DEFAULT 1` ל-BOOLEAN, ו-NOT NULL על עמודות שנוספו ב-ALTER.
- שירות נוסף שעולה כסף ויכול ליפול.

**שורה תחתונה:** ב-28k שורות SQLite מספיק מבחינת נפח. הסיבה לעבור היא concurrency של כתיבה ותפעול, לא גודל. אם הלוגים אחרי שלב A נקיים מ-"database busy", זה לגיטימי לעצור שם (Q1).

---

## 5. גישות שנשקלו

| גישה | תיאור | Trade-offs | החלטה |
|---|---|---|---|
| **A (מומלצת)** | קוד dual-dialect, אותם models. ‏psycopg3 sync. ‏`copy-db` עצמי דרך SQLAlchemy. ‏flip דרך משתנה Railway. | מינימום קוד חדש, schema זהה ל-ORM, rollback של משתנה אחד. מחייב tests על שני ה-dialects. | ✅ |
| B | ‏pgloader להעתקה חד-פעמית ואחריו Alembic | pgloader ממפה types בעצמו (DateTime שנשמר כטקסט ל-timestamptz, JSON ל-text), וה-schema מתרחק מה-ORM. Alembic מוסיף תשתית שה-ORM לא צריך כרגע (`db.py:455-456`). | ❌ |
| C | להישאר על SQLite ולחזק אותו | זול. לא פותר concurrency של כתיבה. | נבחרת כברירת מחדל רק אם Q1 יוצא "לא". שלב A מכסה אותה ממילא. |

---

## 6. ממצאים: כל מקום SQLite-specific בקוד

### 6.1 חיבור, PRAGMAs, נעילות
| מיקום | מה | פעולה ב-PG |
|---|---|---|
| `db.py:339-344` `_sqlite_pragmas` (WAL, synchronous, foreign_keys) | רק SQLite, ונרשם רק כשה-backend הוא SQLite (`db.py:438-440`) | לא נדרש |
| `db.py:347-360` `_sqlite_begin` + `BEGIN IMMEDIATE` | רק SQLite | לא נדרש. ב-PG ‏READ COMMITTED ונעילות שורה. |
| `db.py:420-437` `Database.__init__` | `check_same_thread`, `timeout`, `max_overflow=-1` רק ל-SQLite. `pool_pre_ping` כבר מוגדר ל-PG. | להוסיף pool sizing ל-PG (§8) ונרמול scheme של ה-URL |
| `db.py:442` `execution_options(sqlite_write=True)` | מתעלמים ממנו ב-PG | לא נדרש |
| `db.py:447-450`, `db.py:568-585` `session()` | ה-writer lock מופעל רק כש-`is_sqlite` (`db.py:571`). **ב-PG היום הוא כבוי.** | Q4: בשלב C להפעיל גם ל-PG (`if not write:` במקום `if not (write and self.is_sqlite)`), ולהסיר בשלב D |
| `db.py:56-66` `with_db_retry` | מחפש "locked"/"deadlock" בטקסט השגיאה | ב-PG זה "deadlock detected", שכבר נתפס. לשקול להוסיף "could not serialize". |
| `service.py:84-88` `Coalesced` | נבנה בגלל ה-lock של SQLite | נשאר, כי הוא שימושי גם ב-PG |
| `service.py:157` `ThreadPoolExecutor(max_workers=len(SCRAPERS))` | כ-74 ומעלה threads שעשויים להחזיק connection | pool sizing (§8) |
| `service.py:487-495` נפילה ל-save שורה אחרי שורה | ב-PG שגיאה מבטלת את כל ה-transaction, אבל כל `_save_batch` הוא session משלו (`service.py:536-541`), אז זה תקין | לבדוק ב-test על PG |

### 6.2 VACUUM ושטח דיסק
| מיקום | מה | פעולה |
|---|---|---|
| `db.py:363`, `db.py:475-476`, `db.py:486-496` | incremental auto_vacuum | כבר מוגן ב-`is_sqlite` |
| `db.py:498-511` `storage_stats` | PRAGMA page_count/freelist. ב-PG מחזיר None. | בסדר. `status.py:33-34` ו-admin מציגים "n/a". אופציונלי: `pg_database_size()`, אבל YAGNI. |
| `db.py:513-553` `compact_if_bloated`, `reclaim_space` | ב-PG מחזיר 0 | בסדר. `api.py:580` קורא לו בעלייה, ועל PG זה no-op. |
| `admin.py:459-464` `POST /admin/api/maintenance/vacuum` | ב-PG מחזיר `freed_bytes: 0` | להשאיר. אופציונלי: על PG להריץ `VACUUM (ANALYZE)` ב-autocommit. לא נדרש. |
| `retention.py:101` `db.reclaim_space()` + `config.py:282-283` | no-op על PG | בסדר. לעדכן את ההערה. |

### 6.3 DDL ו-schema (כאן PG מחמיר)
| מיקום | בעיה | פעולה |
|---|---|---|
| `db.py:303` `_literal_default` + `db.py:455-457` | ל-Boolean מייצר `DEFAULT 1` / `SET "x" = 1`. **ב-PG זה נכשל** ("column is of type boolean but default expression is of type integer"). כל עמודת bool חדשה שתתווסף אחרי המעבר תפיל את העלייה. דוגמה חיה: `events.time_known` (`Boolean, default=True`, נוסף 2026-10-28) נוסף ב-SQLite כ-`ADD COLUMN "time_known" BOOLEAN DEFAULT 1` ו-`UPDATE ... = 1`. ב-PG שנבנה טרי מ-`create_all` הוא `BOOLEAN NOT NULL` ותקין; הבעיה רק בעמודה הבאה שתתווסף ב-ALTER על PG. | שלב A: לרנדר `true`/`false` לפי dialect (`sa.true()` מקומפל עם `self.engine.dialect`), ולבדוק ב-test על PG |
| `db.py:457` `ALTER TABLE ... ADD COLUMN` בלי NOT NULL | ב-SQLite הקיים עמודות כמו `is_listed`, `has_discount`, `free_sold_out`, `pay_at_door`, ומאז גם `sponsored.placement`, `sponsored_stats.results_views`, `results_clicks` ו-`events.time_known` (2026-10-28), נוספו כ-nullable. ייתכנו בהן NULL-ים, בעוד שב-PG ה-schema החדש נבנה מה-ORM עם NOT NULL. | בדיקת נתונים §10.1 (Q-NULL). ב-copy להמיר NULL ל-default של המודל. |
| כל `String(n)` במודלים (`db.py:93-324`) | SQLite מתעלם מאורך. **ב-PG זה `StringDataRightTruncation`.** בזמן ה-copy זה נכשל, ובזמן scrape זה מפיל אירוע (fallback שורה-שורה, `service.py:487-495`), שקט ומסוכן. | §10.1 (Q-LEN) על snapshot. לכל עמודה שחורגת או קרובה לגבול: להחליף ל-`Text` (עדיף, כי ב-PG ל-`text` ול-`varchar` אותו ביצוע), או לקצר בקוד. ממליץ: `source_event_id`, `city`, `venue_slug`, `content_type` ו-`geo_cache.query` ל-`Text`. |
| תו `\x00` | PG `text` דוחה NUL. `parsing.py` מנקה NUL בפונקציות שמשתמשות ב-`_INVISIBLE` ו-ftfy (`remove_control_chars`). מ-2026-10-28 `events.description` ו-`ticket_tiers.description` עוברים תמיד דרך `tidy_text` (הטיפוס `TidyText` ב-`models.py`, validator של pydantic), כלומר NUL לא מגיע אליהם מ-scrape חדש. שאר שדות הטקסט (title, venue_name, address, organizer...) עדיין לא. שורות ישנות לא נוקו. | §10.1 (Q-NUL) עדיין לכל העמודות (שורות ישנות). שלב A: אותו `TidyText` (או ניקוי NUL בלבד) על שאר שדות הטקסט של `ScrapedEvent`, מקום אחד ב-`models.py`. |
| `Integer` PK autoincrement | ב-PG זה `SERIAL` (int4), ואחרי copy חובה `setval` | ב-copy: `setval(pg_get_serial_sequence(t,'id'), max(id))`. ‏`ticket_tiers.id` רץ מהר (delete+insert בכל upsert, `db.py:652-659`), בערך 84k ביום, כלומר כ-70 שנה עד 2^31. אין צורך ב-BigInteger. Q-INT מאמת. |
| `create_schema` לא מוחק אינדקסים (`db.py:473-474`) | שינוי אינדקסים במודל לא יגיע ל-SQLite קיים | ב-PG ה-schema נבנה טרי, אז זה בסדר. ל-SQLite: רשימת `DROP INDEX IF EXISTS` מפורשת (§9.6). |

### 6.4 Upsert
- `db.py:777-788` `_upsert`: כבר תומך ב-`postgresql` עם אותו `on_conflict_do_update`.
- ב-PG `ON CONFLICT DO UPDATE` נכשל אם אותו מפתח מופיע פעמיים באותו VALUES ("cannot affect row a second time"). SQLite מקבל את זה. **נבדק ותקין:** ה-batch הוא `dict` לפי `event_id` (`service.py:373`, `service.py:414`).
- גודל batch: `SAVE_BATCH_SIZE = 200` (`service.py:68`) כפול כ-60 עמודות, כלומר כ-12k bind params. מתחת לגבול 65535 של PG.
- `bulk_update_by_id` (`db.py:679-692`) עושה executemany. ב-psycopg3 זה רץ ב-pipeline ומהיר. כבר ממוין לפי id נגד deadlocks (`db.py:684`).

### 6.5 JSON
- עמודות `JSON`: `db.py:110-114`, `db.py:131`, `db.py:181`, `db.py:324`. ב-PG זה `json`, שנשמר כטקסט כפי שנכתב. SQLAlchemy מסריאל עם `json.dumps`, שעושה escape לעברית (`ש`), כמו היום.
- התאמות טקסטואליות על JSON:
  - `api.py:751-752` `_json_contains`: ‏`CAST(genres AS VARCHAR) ILIKE '%"x"%'`. עובד ב-PG על `json`.
  - `search.py:52-53` `_genre` ו-`seo.py:651`: אותה לוגיקה בשלושה עותקים (seo משתמש ב-`like`, השניים האחרים ב-`ilike`). המלצה: לאחד ל-helper אחד בשלב A.
  - `search.py` `_json_has(col, word)` (`text_match`): ‏`CAST(event_types AS TEXT) ILIKE` ומ-2026-10-28 גם על `source_genres`. ב-PG `json::text ILIKE` עובד אותו דבר; ב-`jsonb` הטקסט מגיע עם עברית גולמית (לא `\uXXXX`), וזה אפילו מתאים יותר ל-ILIKE על מילה עברית.
  - `db.py:366-377` `lineup_contains`: מנסה גם צורה מוברחת וגם צורה גולמית. עובד גם על `json` וגם על `jsonb` (שמחזיר עברית גולמית).
- `plan_votes.picks` (JSON, נוסף 2026-10-28): רשימת event ids. נכתב ונקרא רק כשדה שלם ב-Python, ואין עליו שום התאמה ב-SQL. ב-PG `json` או `jsonb`, אותו דבר.
- אין `json_extract` או `json_each` בקוד. נבדק ב-grep על `src/` ו-`scripts/`.

### 6.6 תאריכים ושעות
- `db.py:73-83` `UTCDateTime`: שומר naive UTC ומחזיר aware. ב-PG זה `timestamp without time zone` עם אותה התנהגות. **ממליץ להשאיר**, כדי לא לשנות type וכדי שה-copy יהיה פשוט. ה-`TimeZone` של ה-session ב-PG לא משפיע על naive.
- אין `func.date`, `strftime` או `julianday` ב-SQL. ה-`strftime` שנמצא ב-grep (`enrichment.py:465`, `admin.py:425`) הוא Python.
- `local_date` / `sponsored_stats.day` הם `Date` ותקינים.
- **מ-2026-10-28:** `local_date`, `day_of_week` ו-`time_slot` של אירוע בחו"ל מחושבים ב-Python (`enrichment._categorize_time`) באזור הזמן של המקום (`events.time_zone`, IANA, מ-GeoNames דרך `places.city_zone` / `scrapers.abroad.local_zone`), לא של ישראל. אירוע עם `time_known = false` (האתר נתן תאריך בלבד) מקבל את היום כפי שהאתר כתב אותו ו-`time_slot` NULL. `start_at` עצמו נשאר UTC naive כמו היום, אז אין השפעה על ה-type או על ה-copy.
- **הצגה:** ה-API מחזיר `time_known` ו-`time_zone`; ה-frontend (`ui.tsx` `when`) ו-SEO (`seo.py` `_when` / `_iso`) מציגים באזור של המקום, ו-JSON-LD של אירוע בלי שעה מקבל `startDate` של תאריך בלבד. הכול Python/JS, בלי SQL על אזורי זמן (אין `AT TIME ZONE`), אז ה-`TimeZone` של ה-session ב-PG עדיין לא רלוונטי.

### 6.7 חיפוש, case ו-collation (עברית)
| מיקום | מה | ב-PG |
|---|---|---|
| `search.py:45-49` `text_match` | ILIKE על title/description/venue_name/city/address ו-lineup | ILIKE אמיתי. בעברית אותה תוצאה. בלטינית לא-ASCII התוצאה מעט רחבה יותר (רצוי). |
| `api.py:894-901` venue/city | ILIKE עם escape. מ-2026-10-28 `city` הוא `OR` של ILIKE על `city` ו-`address` לכל שם של העיר (`api._city_names`: עברית + אנגלית מ-GeoNames, "פריז" גם `%Paris%`); שם עם תוספות ("לונדון, אנגליה") מתאים כי זה `%...%`. | תקין, אותו ILIKE. אין אינדקס שמשרת אותו גם היום (seq scan, זניח ב-28k). |
| `search.py` `smart_filters`: עיר בחו"ל בטקסט (`places.city_abroad`, 100k+ תושבים) | `OR` של `text_match` לכל שם של העיר | אותו ILIKE, לא תלוי DB. |
| `db.py` `_sqlite_pragmas`: ‏`create_function("has_word")` (נוסף 2026-10-27) | מילה של עד 3 אותיות נמצאת רק כמילה שלמה ("ראפ" לא בתוך "תראפיה"), עם prefix עברי אחד. פונקציית Python שנרשמת על חיבור SQLite. | **לא קיים ב-PG, חוסם.** ב-PG: ‏`col ~* '(^|\W)[בלמהוכש]?ראפ($|\W)'` (‏`\m`/`\M` לא מכירים prefix), או דרך `search_text` + pg_trgm (§9.4). לעבור ל-`func` לפי dialect בשלב A. |
| `search.py`: ‏`skeleton()`/`sounds_like()`, ‏`did_you_mean()`, typo דרך `difflib` (נוספו 2026-10-27) | התאמה לפי צליל (עברית/לטינית), "התכוונת ל", שגיאות כתיב מול אוצר המילים | רץ ב-Python, לא תלוי DB. עובד אותו דבר. ב-PG אפשר להחליף את ה-typo וה-did-you-mean ב-`similarity()`/`word_similarity()` של pg_trgm (§9.4), לא חובה. |
| `search.py` ‏`date_ranges()` (convertdate) | "השבוע", "בדצמבר", "חנוכה" לטווח `local_date` | `BETWEEN` על date, תקין |
| `blog.py:186`, `seo.py:429`, `seo.py:983` | ILIKE **בלי escape** של `%`/`_` מהקלט | אותה התנהגות כמו היום. לא חסם למעבר, אבל שווה תיקון. |
| `geo.py:481` `LIKE '% @__'` | LIKE ב-PG תלוי case (ב-SQLite לא, ב-ASCII) | הדפוס הוא `@` ושני תווים, כלומר קוד מדינה. לוודא שהקודים נשמרים באותיות שה-LIKE מצפה להן (אין עניין של case ב-`@__`). תקין. |
| `service.py:238` `LIKE 'genres:%'` | תקין | |
| `ORDER BY` על טקסט: `dedup.py:259` (`order_by(EventRow.id)`, "same input order, same groups on every pass"), tie-breakers `api.py:978`, `api.py:1002`, `api.py:1107` | **collation משנה את הסדר.** ב-`en_US.utf8` הסדר שונה מבינארי, וכך ה-regroup הראשון על PG יכול להחליף primaries ו-group_id בהמון קבוצות (churn ב-deal_changes, alternatives וקישורים) | Q2: DB עם `LC_COLLATE 'C'` |
| סדר NULL: `api.py:383-390` (`start_at`, `group_min_price`, `last_seen_at`, `group_size`), `api.py:1107`, `seo.py:211`, `seo.py:516`, `seo.py:1132` | ב-SQLite ‏NULL הוא הכי קטן (ראשון ב-ASC, אחרון ב-DESC). ב-PG הוא הכי גדול (אחרון ב-ASC, **ראשון ב-DESC**). `price_desc` יציג אירועים בלי מחיר ראשונים. | Q3: `nulls_first()`/`nulls_last()` מפורש בשלב A. SQLite תומך מגרסה 3.30. |

### 6.8 Transaction semantics
- קריאות (`session(write=False)`, ‏24 מקומות, למשל `api.py:517`) ב-SQLite WAL רואות snapshot של כל ה-transaction. ב-PG ‏READ COMMITTED כל statement רואה את מה שכבר committed. לכן `total` ב-`api.py:974` ו-`rows` ב-`api.py:976-981` יכולים לצאת לא עקביים במקרה של commit ביניהם. ההשפעה זניחה. אופציה: `isolation_level="REPEATABLE READ"` ל-`_sessions`. ברירת מחדל: לקבל.
- `Database.changes` (`db.py:409-418`) הוא מונה בתוך התהליך. כתיבה חיצונית (psql, copy-db) לא תבטל את ה-cache, וה-TTL מטפל בזה. כך גם היום.

### 6.9 מצב מקומי שנקשר ל-path של SQLite
- `geonames.py` `cache_file()` (נוסף 2026-10-28): ‏`project_path("data")/geonames-<version>.json`, קובץ JSON ולא DB. לא תלוי ב-URL של ה-DB, ולא משתנה במעבר.
- `weather.py:53-61` `cache_file`: עם URL של PG נופל ל-`project_path("data")`, שזה `/app/data`, אותו מקום כמו היום. תקין.
- `scrapers/chillz.py:199-200`: `data/chillz_past.json` קבוע ועצמאי. תקין.
- `artists.py`: קובץ SQLite משלו ומחוץ לתחום. נשאר על ה-volume.
- `admin.py:101`, `admin.py:348`, `status.py:82`: מציגים את ה-URL עם `hide_password=True`. תקין.

### 6.10 תלויות ו-deploy
- `pyproject.toml:19` (`sqlalchemy>=2.0`) ו-`requirements.lock:65` (`sqlalchemy==2.1.5`). **אין psycopg.** צריך להוסיף `psycopg[binary]>=3.2` ל-`pyproject.toml` ול-`requirements.txt`, ולחדש את `requirements.lock` לפי `docs/deployment.md` ("Dependencies"). חובה שזה ינחת **לפני** ה-flip. אחרת: `ModuleNotFoundError`, crash loop, ו-rollback.
- Railway נותן `postgresql://...`. ‏SQLAlchemy מפרש את זה כ-psycopg2, שלא מותקן. שתי אפשרויות: לבנות את ה-URL מ-`${{Postgres.PGUSER}}` וכו', או נרמול של שתי שורות ב-`Database.__init__` (`postgresql://` ל-`postgresql+psycopg://`). **ממליץ על הנרמול**, כי הוא עמיד לשינוי סיסמה ב-Railway.

### 6.11 Tests
- כל ה-tests שנוגעים ב-DB בונים URL של SQLite בעצמם: `tests/test_storage_api.py:27`, `tests/test_status.py:15`, `tests/test_safety_nets.py:36`, `tests/test_safety_nets.py:152`, `tests/test_resilience.py:121`, `tests/test_dedup.py:33`, `tests/test_deal_freshness.py:24`.
- §12 מתאר איך מריצים אותם גם על PG.

---

## 7. מיפוי schema ו-types

| SQLAlchemy | SQLite היום | PostgreSQL | הערה |
|---|---|---|---|
| `String(n)` | TEXT (אורך לא נאכף) | `VARCHAR(n)` (נאכף) | §6.3: חלק עוברים ל-`Text` |
| `Text` | TEXT | `TEXT` | |
| `Integer` PK | INTEGER rowid | `SERIAL` (int4) | `setval` אחרי copy |
| `Boolean` | INTEGER 0/1 | `BOOLEAN` | ‏`is_(True)` מתרנדר `IS true` |
| `Numeric(10,2)` | REAL/NUMERIC affinity | `NUMERIC(10,2)` | ‏SQLAlchemy כבר מעגל ל-2 ספרות בקריאה, אז אין שינוי ערכים |
| `Float` | REAL | `DOUBLE PRECISION` | lat/lng |
| `UTCDateTime` (DateTime) | TEXT ISO | `TIMESTAMP WITHOUT TIME ZONE` | UTC naive, כמו היום |
| `Date` | TEXT | `DATE` | |
| `JSON` | TEXT | `JSON` (שלב C), אופציה ל-`JSONB` (§9.4) | |
| `LargeBinary` | BLOB | `BYTEA` | `event_images.data`, כמעט ריק |

ה-schema ב-PG נוצר מ-`Base.metadata.create_all` דרך `create_schema()` (`db.py:440`). טבלאות הפרסום מוגדרות ב-`partylinks/ads/models.py` על אותו `Base`, ונרשמות בו כי `db.py:804` מייבא את המודול. לכן כל מי שמייבא את `db` (כולל `copy-db`) רואה את כל הטבלאות. זה אותו קוד שרץ בעלייה (`api.py:568`), כך שאין schema ידני.

---

## 8. Driver, pooling ו-Railway

**Driver:** ‏`psycopg[binary]` 3 sync. URL: `postgresql+psycopg://`. ה-binary wheel כולל libpq, אז לא צריך apt ב-`python:3.12-slim`.

**Pool (ל-PG בלבד, ב-`Database.__init__`):**
- `pool_size=10`, `max_overflow=20`, `pool_timeout=30`, `pool_pre_ping=True` (קיים), `pool_recycle=1800`.
- סה"כ עד 30 connections. ‏Railway Postgres מגיע עם `max_connections=100`, כך שנשאר מקום ל-psql, ל-copy-db ול-redeploy חופף (שלא קורה, כי `overlapSeconds: 0`).
- `connect_args={"options": "-c idle_in_transaction_session_timeout=300000 -c statement_timeout=120000"}` כרשת ביטחון נגד transaction תקוע, כמו ה-`WRITE_WAIT_S` של היום. לוודא שה-regroup המלא לא חורג מ-120s במדידה (§13). אם הוא חורג, statement_timeout רק לקריאות.

**Railway setup (שלב B, ידני, בלי commit):**
1. בפרויקט PartyLinks: ‏New → Database → PostgreSQL. שם השירות: `Postgres`.
2. ב-Postgres להפעיל Backups (scheduled, daily, שמירה של 7 ימים או יותר).
3. ליצור DB עם collation (Q2) דרך `psql` על `DATABASE_PUBLIC_URL`:
   `CREATE DATABASE partylinks TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C.UTF-8';`
   ואז `CREATE DATABASE partylinks_rehearsal ...` באותה צורה. ב-`partylinks` להריץ `CREATE EXTENSION IF NOT EXISTS pg_trgm;` (זול, ושומר את האפשרות פתוחה).
4. בשירות `web` (רק בשלב C): `PARTYLINKS_DATABASE__URL = postgresql://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/partylinks`. ה-scheme יתנרמל בקוד. ‏PGHOST הוא הכתובת הפרטית (`postgres.railway.internal`). ‏psycopg תומך ב-IPv6, למקרה שה-environment ישן ו-IPv6 בלבד.
5. ה-public TCP proxy: לפתוח רק לזמן ה-rehearsal, ואחר כך לסגור.

---

## 9. מפתחות ואינדקסים (סעיף ייעודי)

### 9.1 Primary keys, כולם קיימים
| טבלה | PK | סוג | הערה |
|---|---|---|---|
| `events` | `id` String(128) (`db.py:93`) | טקסט טבעי `"{source}:{source_event_id}"` (`models.py:175-176`) | להשאיר. מופיע ב-URLs, ב-FKs וב-clicks. עם `LC_COLLATE C` ה-btree מהיר. |
| `ticket_tiers` | `id` int autoinc (`db.py:171`) | surrogate | נוצר מחדש בכל upsert |
| `event_images` | `id` int (`db.py:202`) | surrogate | מופיע ב-URL `/images/{id}`, לכן חייב להישמר ב-copy |
| `geo_cache` | `query` String(300) (`db.py:223`) | טקסט טבעי | Q-LEN. לשקול `Text`. ב-PG יש מגבלת btree של כ-2700 bytes לערך, ו-300 תווים עבריים הם כ-600 bytes, אז תקין. |
| `deal_changes` | `id` int (`db.py:239`) | | |
| `clicks` | `id` int (`db.py:257`) | | |
| `searches` | `id` int (`db.py:275`) | | |
| `sponsored` | `event_id` (`ads/models.py:21`) | טבעי | |
| `sponsored_stats` | `(event_id, day)` composite (`ads/models.py:36-37`) | composite | תקין |
| `flags` | `key` (`db.py:287`) | טבעי | |
| `plans` | `id` String(12) (`plans/models.py`) | טבעי: הסוד של הקישור (`secrets.token_urlsafe(6)`, 8 תווים) | נוסף 2026-10-28. מופיע ב-URL `/plan/{id}`, לכן חייב להישמר ב-copy. |
| `plan_votes` | `id` int autoinc (`plans/models.py`) | surrogate | נוסף 2026-10-28. `setval` אחרי copy כמו כל int PK. |
| `plan_events` | `(plan_id, event_id)` composite (`plans/models.py`) | composite | נוסף 2026-10-28 ("איזו מסיבה?"). |
| `scrape_runs` | `id` int (`db.py:299`) | | |

**אין טבלה בלי PK.**

### 9.2 Foreign keys: מוצהרים, משתמעים, ומה *לא* להוסיף
מוצהרים היום, כולם `ON DELETE CASCADE` ונאכפים כבר ב-SQLite (`db.py:343`):
- `ticket_tiers.event_id` → `events.id` (`db.py:172`)
- `event_images.event_id` → `events.id` (`db.py:203`)
- `deal_changes.event_id` → `events.id` (`db.py:240`)
- `plan_votes.plan_id` → `plans.id` (`plans/models.py`, נוסף 2026-10-28). **בלי `ON DELETE CASCADE`**: `plans.routes.delete_plans` מוחק את התשובות לפני התוכנית, באותה טרנזקציה של `delete_events`. ב-PG ה-FK נאכף, אז הסדר הזה (ילד לפני הורה) חובה, והוא כבר כזה. אינדקס על `plan_id` קיים (`index=True`).
- `plan_events.plan_id` → `plans.id` (`plans/models.py`, נוסף 2026-10-28), גם הוא בלי CASCADE: `delete_plans` מוחק את `plan_events` לפני `plans`. ‏`plan_id` הוא העמודה הראשונה ב-PK, אז ה-PK משמש כאינדקס שלו.

CASCADE מתאים לכלל "never past events": ‏retention מוחק אירוע וכל מה שתלוי בו נמחק איתו. `delete_events` (`db.py:791-800`) מוחק את הילדים במפורש לפני ה-parent. ב-PG זה כפול אבל לא מזיק, וזה גם המקום שבו אוספים את קבצי התמונות. להשאיר. ‏PG לא יוצר אינדקס אוטומטית על עמודת FK, אבל לשלושתן יש `index=True`, וזה חיוני ל-CASCADE.

משתמעים, ו**המלצה לא להוסיף FK**:
| קשר | למה לא |
|---|---|
| `events.group_id` / `events.best_listing_id` → `events.id` (self) (`db.py:144`, `db.py:153`) | retention מוחק לפעמים את ה-primary לפני שה-regroup הבא מעדכן את החברים. FK היה מפיל את retention, או מחייב `SET NULL`/`DEFERRABLE`, ו-`regroup` מעדכן אלפי שורות ב-executemany (`dedup.py:245-249`). הערך הזה מחושב מחדש בכל pass, כך שהוא cache ולא קשר. |
| `events.source`, `scrape_runs.source`, `deal_changes.source` → "sources" | אין טבלת sources. המקורות הם config (`settings.yaml`, `SOURCE_NAMES`). |
| `clicks.event_id`, `sponsored.event_id`, `sponsored_stats.event_id` → `events.id` | **בכוונה בלי FK.** הם צריכים לשרוד את retention (`db.py:253-255`, `ads/models.py:15-17`, `ads/models.py:31-32`). |
| `plans.event_id` → `events.id` | **בכוונה בלי FK**, כמו `sponsored`. תוכנית **לא** שורדת את האירוע: `db.delete_events` קורא ל-`delete_plans` לפני מחיקת האירוע. בלי FK, כי `delete_events` מוחק במנות של 1000 ולא צריך cascade, ו-API שלא מוצא את האירוע מחזיר 404. |
| `plan_events.event_id` → `events.id` | **בכוונה בלי FK.** אירוע שמושווה בתוכנית של מישהו אחר נמחק (retention) ופשוט יוצא מהאפשרויות: `delete_plans` מוחק את השורות שלו ב-`plan_events`, והתוכנית נשארת. ‏`plan_votes.picks` (JSON של event ids) יכול להחזיק id שכבר נמחק, וה-API מסנן אותו בקריאה. |
| `events.venue_slug` → venues | אין טבלת venues. השמות מחושבים ב-`venues.py`. |
| artists | DB נפרד (`artists.py`), ולא אפשרי בין DBs. |
| `geo_cache` | מפתח שאילתה, ולא קשור לאירוע. |

### 9.3 Unique constraints שהקוד מניח
| הנחה | איפה | מצב | בדיקה (על snapshot, **לא** על prod) | המלצה |
|---|---|---|---|---|
| `plan_events (plan_id, event_id)` ייחודי: אותו אירוע פעם אחת בתוכנית (נוסף 2026-10-28) | ה-PK. ‏`plans.routes.add_option` בודק לפני insert, ועד 4 אפשרויות לתוכנית (`MAX_OPTIONS`) | PK, נאכף | אין צורך: PK. | להשאיר. ב-PG שני "הוספה" של אותו אירוע באותו רגע: אחד יקבל 500 מה-PK. זניח. |
| `plan_votes (plan_id, voter)` ייחודי: תשובה אחת לכל דפדפן בתוכנית (נוסף 2026-10-28) | `UniqueConstraint` ב-`plans/models.py`, ה-upsert ב-`plans.routes.vote` (select לפי שניהם, ואז update או insert) | נאכף ב-SQLite ובנוי מה-ORM ב-PG | `SELECT plan_id, voter, count(*) FROM plan_votes GROUP BY 1, 2 HAVING count(*) > 1;` | להשאיר. ב-PG שתי הצבעות ראשונות של אותו דפדפן באותו רגע יכולות להתנגש ב-unique, ואחת מהן תקבל 500. זניח (לחיצה כפולה). אם יפריע: `INSERT ... ON CONFLICT (plan_id, voter) DO UPDATE`. |
| `events.slug` ייחודי | `db.py:96` (`unique=True`), lookup ב-`api.py:1098`, `seo.py:393` | כבר נאכף ב-SQLite | `SELECT slug, count(*) FROM events WHERE slug IS NOT NULL GROUP BY slug HAVING count(*)>1;` | להשאיר |
| `(source, source_event_id)` ייחודי | ה-PK בנוי מהם (`models.py:175-176`) | נגזר מה-PK, כל עוד אין `:` בשם source (אין) | `SELECT source, source_event_id, count(*) FROM events GROUP BY 1,2 HAVING count(*)>1;` | **לא להוסיף.** האינדקס כפול ל-PK ועולה בכל upsert. |
| `(event_id, url)` ייחודי ב-`event_images` | `db.py:664` (`known = {(row.event_id, row.url): row}`) מניח ייחודיות. כפילות נבלעת ב-dict ושורה עודפת נשארת לתמיד. | לא נאכף | `SELECT event_id, url, count(*) FROM event_images GROUP BY 1,2 HAVING count(*)>1;` | לבדוק. אם נקי, **לא להוסיף** constraint (URL ארוך, ומגבלת btree). `models.py:170` כבר מסיר כפילויות במקור. אם יש כפילויות, ניקוי חד-פעמי ב-copy. |
| `(event_id, position)` ייחודי ב-`ticket_tiers` | `db.py:653-657` (positions 0..n אחרי delete) | לא נאכף | `SELECT event_id, position, count(*) FROM ticket_tiers GROUP BY 1,2 HAVING count(*)>1;` | **להוסיף** `UNIQUE (event_id, position)`. הוא מחליף את `ix_ticket_tiers_event_id` (prefix), כך שעלות הכתיבה זהה. |
| `(event_id, position)` ב-`event_images` | positions מתעדכנים שורה-שורה (`db.py:669-670`) | | | **לא להוסיף.** בזמן ה-executemany יש כפילויות זמניות ו-constraint ייכשל. |

### 9.4 אינדקסים: נגזרים מה-queries האמיתיים
**הבחנה מרכזית לפני הכול:** ב-28k שורות כל טבלת `events` נכנסת ל-RAM, ו-seq scan לוקח בערך 10–30ms. רוב האינדקסים הקיימים לא נבחרים בכלל על ידי ה-planner, ובמיוחד booleans שמסננים כחצי מהשורות. ב-PG יש להם גם מחיר נסתר: **HOT updates.** כל upsert כותב את `last_seen_at` (`db.py:638`), ועמודה זו מאונדקסת (`db.py:133`), ולכן אף upsert לא יכול להיות HOT, וכל אחד מכ-25 האינדקסים של `events` מקבל entry חדש בכל upsert, עם bloat בהתאם. אם אף עמודה שמשתנה ב-upsert רגיל לא מאונדקסת, ה-upsert הוא HOT ובלי כתיבות אינדקס בכלל.

**עלות כתיבה כמותית:** כ-28k upserts ביום. כל btree על `events` שווה בערך 28k index inserts ביום כשאין HOT, ואפס כשיש HOT. ב-PG זה זניח ב-CPU (שניות ביום), ההשפעה היא בעיקר bloat ו-autovacuum. ‏GIN trigram על `title` הוא כ-20–40 trigrams לשורה, כלומר כ-1M entries ביום. על `description` זה כמה מאות לשורה, כלומר מעל 10M ביום. **לא.**

#### events: אינדקסים שנשארים
| אינדקס | query שהוא משרת | הערה |
|---|---|---|
| PK `id` | `api.py:1098` / `seo.py:393` (`id = ref OR slug = ref`, BitmapOr), `upsert` `db.py:624`, `dedup.py:322` | |
| `slug` unique | `api.py:1098`, `seo.py:393` | |
| `source` | `service.py:507`, `service.py:552`, `service.py:559`, `media.py:127`, תת-query ב-`api.py:888` | מחליף את הצורך ב-`(source, last_seen_at)`. מקור אחד הוא כמה אלפי שורות, והסינון על `last_seen_at` / `offers_changed_at` רץ עליהן בזיכרון. |
| `group_id` | `api.py:680`, `api.py:695`, `api.py:888`, `api.py:923`, `api.py:1106` | משתנה רק ב-regroup, כך שלא שובר HOT ב-upsert (ה-upsert לא נוגע ב-`_GROUP_COLUMNS`, `db.py:599-602`, `db.py:648`) |
| `local_date` | `api.py:940`, `seo.py:657` | |
| `venue_slug` | `api.py:896` | |
| `city` | שוויון: `seo.py:933`, `seo.py:972`, `blog.py:184` (ה-ILIKE ב-`api.py:899` לא משתמש בו) | |
| `country` | `api.py:908` (`IS NULL` כמעט תמיד, לא סלקטיבי), `api.py:910`, `seo.py:737` | לשקול הסרה. השאלה היא אם `country IS NOT NULL` (abroad) סלקטיבי. לבדוק `count` על ה-snapshot. |
| `area` | `api.py:929-934`, `seo.py:977` | |
| `group_min_price` | מיון `price_asc`/`price_desc` (`api.py:386-387`), `api.py:952` | |
| `group_entry_type` | `api.py:912` | סלקטיביות נמוכה, ללא HOT-breaking (קבוצתי). אופציונלי. |
| `start_at` | `retention.py` (`current_clause`), `api.py:945-948` (date_from/to) | **מוחלף** ב-feed index למטה, ונשאר רק אם EXPLAIN מראה שימוש. |

#### events: חדש (עם מחלוקת ההכרעה)
**`ix_events_feed (start_at, id) WHERE is_primary IS true AND is_listed IS NOT false`**, partial. SQLAlchemy: `Index(..., postgresql_where=..., sqlite_where=...)` עם אותו ביטוי בשני ה-dialects.
- **משרת:** את ה-query החם, `/events` עם `sort=start_asc` (ברירת מחדל): `api.py:976-981` עם `ORDER BY start_at, start_at, id LIMIT n`, והפילטרים `is_primary IS true` (`api.py:944`) ו-`is_listed IS NOT false` (`db.py:387`).
- **למה composite בסדר הזה:** ה-`upcoming` clause הוא `OR` (`retention.py` `current_clause`), אז ה-planner לא יכול להשתמש ב-range על `start_at`. מה שמרוויחים הוא **ordered index scan שעוצר ב-LIMIT**. מאחר שאירועי עבר נמחקים, כמעט כל שורה עוברת את הפילטר, והסריקה עוצרת אחרי כ-24 שורות במקום sort של 28k. ‏`id` בסוף תואם ל-tie-breaker.
- **מלכודת:** אם Q3 קובע `start_at ASC NULLS FIRST`, צריך להגדיר את האינדקס באותו `NULLS FIRST`, אחרת ה-order לא יתאים. לאמת ב-`EXPLAIN`.
- **partial "upcoming only":** **לא אפשרי.** ה-predicate תלוי ב-`now()`, ש-PG לא מרשה ב-index predicate. גם לא נחוץ, כי אין אירועי עבר ב-DB.
- **עלות כתיבה:** `start_at`, `is_primary` ו-`is_listed` כמעט לא משתנים ב-upsert, אז זה לא שובר HOT. ‏`is_listed` משתנה ב-`service.py:562` רק לאירועים שנעלמו.
- `count(*)` (`api.py:974`) ו-`/events/map` (`api.py:999-1004`, בלי LIMIT) יישארו seq scan. זה תקין ב-28k, ונשמר ב-cache.

#### events: אינדקסים להסיר ב-PG (שוברי HOT או לא סלקטיביים)
| אינדקס (`db.py`) | סיבה |
|---|---|
| `last_seen_at` (`:133`) | **שובר HOT בכל upsert.** משמש רק את `seen_desc` (`api.py:389`), ו-sort של 28k בזיכרון לוקח כ-10ms, ואת `service.py:559` שמכוסה ע"י `source`. |
| `offers_changed_at` (`:139`) | שובר HOT כשהמחיר משתנה. `service.py:552` מכוסה ע"י `source`. |
| `is_listed` (`:136`), `is_primary` (`:145`), `in_target_city` (`:109`), `is_weekend` (`:119`), `has_free_entry` (`:126`), `group_has_free_entry` (`:151`) | booleans עם סלקטיביות של כ-30–90%. ה-planner לא בוחר בהם. `is_primary` ו-`is_listed` עוברים ל-predicate של ה-feed index. |
| `day_of_week` (`:116`), `time_slot` (`:117`) | 7 ו-4 ערכים, לא סלקטיבי |
| (`time_known`, `time_zone`, נוספו 2026-10-28) | **בלי אינדקס מלכתחילה, ושיישאר כך:** אף query לא מסנן או ממיין לפיהן, רק מחזירים אותן. שתיהן נכתבות ב-upsert (`_EVENT_FIELDS`), אז אינדקס עליהן היה שובר HOT. |
| `entry_type` (`:127`) | הפילטר משתמש ב-`group_entry_type` (`api.py:912`), ו-`entry_type` עצמו לא מסונן ב-SQL |

בנוסף ל-events: **`fillfactor=85`** (`postgresql_with={"fillfactor": 85}` ב-`__table_args__`), שמשאיר מקום ל-HOT באותו page.

#### טבלאות אחרות
| טבלה | שינוי | query | עלות כתיבה |
|---|---|---|---|
| `ticket_tiers` | `UNIQUE (event_id, position)` מחליף `ix_event_id` | `db.py:652` delete, `db.py:713-715`, `dedup.py:337` (group by event_id, index-only scan) | זהה (אינדקס אחד במקום אחד). insert-only, כ-84k ביום. |
| `ticket_tiers` | להסיר `ix_kind` (`db.py:183`) | אין query שמסנן על `TicketRow.kind` (grep) | חוסך כ-84k inserts ביום |
| `deal_changes` | `(event_id, at)` מחליף `ix_event_id` | `api.py:1116-1117` (`WHERE event_id IN ... ORDER BY at DESC`) | זהה |
| `deal_changes` | להשאיר `at`, `kind`, `source` | `api.py:1142-1150` | insert-only, זול |
| `scrape_runs` | אופציונלי: `(source, id)` | `api.py:1306`, `api.py:1350` | טבלה קטנה (30 יום), לא נחוץ. YAGNI. |
| `clicks` | להשאיר `at` (`clicks.py:40`). `event_id`/`source` לא בשימוש ב-WHERE. | | insert-only, זניח. אפשר להשאיר. |
| `searches` | רק `at` (`searches.py:56`). בכוונה בלי אינדקס על `q`: הספירה לפי מילה נעשית ב-Python. | | insert-only, שורה לכל חיפוש (עמוד ראשון). זניח. |
| `geo_cache` | בלי שינוי. `geo.py:481` (`LIKE '% @__'`) הוא seq scan על טבלה קטנה. מ-2026-10-28 גם `UPDATE` ו-`DELETE ... WHERE query = ?` (תיקון מרכז עיר בחו"ל), לפי ה-PK. | | |
| `plans` (2026-10-28) | PK `id`, אינדקס `event_id` | `GET /plans/{id}` (PK), `delete_plans` (`event_id IN (...)`) | insert אחד לכל לחיצה על "מי בא איתי?". זניח. |
| `plan_events` (2026-10-28) | PK `(plan_id, event_id)`, אינדקס `event_id` | `plan_id = ? ORDER BY position` (ה-PK משרת), ‏`event_id IN (...)` במחיקה (האינדקס) | עד 3 שורות לתוכנית. זניח. |
| `plan_votes` (2026-10-28) | אינדקס `plan_id`, ה-UNIQUE `(plan_id, voter)` | `plan_id = ?` (רשימת התשובות, `count(*)` מול `MAX_VOTES = 60`), `plan_id = ? AND voter = ?` (ה-UNIQUE משרת), `plan_id IN (subquery)` במחיקה | עד 60 שורות לתוכנית. ה-UNIQUE מתחיל ב-`plan_id`, אז אפשר לוותר על `ix_plan_votes_plan_id` ב-PG (אינדקס כפול). לא חשוב בגודל הזה. |

#### PG-only, נדחה עד שמדידה מצדיקה
| אפשרות | למה לא עכשיו | מתי כן |
|---|---|---|
| `JSONB` + GIN על `genres` (`?|`) | מחייב `JSON().with_variant(JSONB(), "postgresql")` ושינוי query ב-3 מקומות (`api.py:752`, `search.py:53`, `seo.py:651`). ‏seq scan עם ILIKE על 28k לוקח כ-10–20ms. GIN על כל upsert הוא כ-5 entries לשורה. | אם `EXPLAIN ANALYZE` של `/events?genre=` מעל 50ms |
| `pg_trgm` על `title` (`gin_trgm_ops`) | החיפוש הוא `OR` על 6+ ביטויים (`text_match` ב-`search.py`: title, description, venue_name, city, address, organizer, ‏lineup ו-event_types כ-JSON), ואינדקס על title לבד **לא יכול** לשרת OR: כל ענף צריך אינדקס, כולל `description`, וזה כ-10M+ entries ביום. ‏`q` מתחיל מ-2 תווים (`api.py:877`), ומתחת ל-3 trigram לא עוזר. | ראו השורה הבאה: עמודה מאוחדת. לבדוק עברית: `SELECT show_trgm('מסיבת טכנו');` חייב להחזיר trigrams עבריים, וזה מחייב `LC_CTYPE` UTF-8 (Q2). |
| **`pg_trgm` על עמודה מאוחדת `search_text` (התכנון, הוחלט 2026-10-27)** | ‏FTS5 של SQLite נדחה כדי לא לבנות פעמיים: האינדקס נבנה פעם אחת, ב-PG. **העמודה:** `search_text TEXT`, מחושבת ב-upsert ‏(`service.py`/`db.py`) מ-title, venue_name, city, address, organizer, ‏lineup (שמות **לא** escaped, לא ה-JSON), ‏event_types ו-description, ‏casefold. **האינדקס:** `CREATE EXTENSION pg_trgm; CREATE INDEX ix_events_search_trgm ON events USING gin (search_text gin_trgm_ops);`. **ה-query:** `text_match(word)` הופך ל-`search_text ILIKE '%word%'` אחד (GIN משרת ILIKE מ-3 תווים), ‏`has_word` ל-`~*` על אותה עמודה, ושגיאות כתיב ל-`word_similarity(q, search_text) > 0.6` (אופרטור `<%`, אותו אינדקס). ‏`relevance()` נשאר CASE על title/lineup/venue (רק מיון, אחרי הסינון). **עלות:** description ארוך, אז האינדקס כבד בכתיבה. לבדוק גודל ו-upsert time ב-rehearsal (§13). אם יקר: description באינדקס נפרד או בלי description ב-`search_text`. | **שלב D**, צעד 3. מדידה לפני/אחרי על `/events?q=` ב-35k: היום כ-0.3 ש׳ ("מסיבה" 0.65, שגיאת כתיב 0.55). לבדוק `EXPLAIN ANALYZE` שה-GIN נבחר. ‏q של 2 תווים נשאר seq scan, וזה בסדר. |
| מפה (lat/lng) | ‏`/events/map` לא מסנן לפי bbox (`api.py:999-1004`) | רק אם יהיה bbox query. אז btree `(latitude, longitude)` או PostGIS. |
| sitemap (`seo.py:1132`) | כל ה-upcoming, נשמר ב-cache לשעה (`seo.py:1270`) | אף פעם |

### 9.5 מה אפשר כבר על SQLite, לפני המעבר (צעד ראשון בטוח)
| פריט | על SQLite? | הערה |
|---|---|---|
| `UNIQUE (event_id, position)` על ticket_tiers | ✅ | אחרי בדיקת כפילויות. `create_schema` יוצר אינדקסים חסרים בעלייה (`db.py:473-474`), ו**אם יש כפילויות העלייה נופלת**. לכן חובה להריץ את הבדיקה על snapshot לפני ה-push. |
| `(event_id, at)` על deal_changes | ✅ | |
| partial `ix_events_feed` | ✅ | SQLite תומך ב-partial index (`sqlite_where`). ה-planner של SQLite דורש שה-WHERE יכיל את אותם terms, ו-SQLAlchemy מרנדר `IS 1` בשני המקומות. לאמת ב-`EXPLAIN QUERY PLAN`. |
| הסרת אינדקסים מיותרים | ✅ | מחייב `DROP INDEX IF EXISTS` מפורש ב-`create_schema`, כי היום הוא additive בלבד. זה גם מקל על ה-write lock של SQLite. |
| `nulls_first`/`nulls_last` מפורש | ✅ | SQLite 3.30+ |
| `String(n)` ל-`Text` | ✅ | לא משנה כלום ב-SQLite (אין אכיפה). ה-DDL הקיים לא מתעדכן, וזה לא משנה. |
| ניקוי NUL ב-model | ✅ | |
| GIN / jsonb / pg_trgm / fillfactor / HOT | ❌ | PG בלבד |

**מדידה:** `EXPLAIN QUERY PLAN` ותזמון של `/events`, `/events/map` ו-`regroup` על snapshot לפני ואחרי. הכלל "measure at prod size" חל גם על שינוי אינדקסים.

### 9.6 סיכון בשינוי אינדקסים בזמן עלייה
`create_schema()` רץ בעלייה (`api.py:568`) ומחזיק transaction אחת (`db.py:460`). ‏`CREATE INDEX` על 28k שורות לוקח פחות משנייה בשני ה-DBs. כשל (unique violation) מפיל את העלייה, ה-health check נכשל, Railway לא מקדם את ה-deploy, ומאחר ש-`overlapSeconds: 0` האתר למטה עד revert. **הגנה:** בדיקת נתונים על snapshot לפני ה-push. אופציונלי: לעטוף את יצירת האינדקסים ב-try עם log.error, כך שאינדקס שנכשל לא יפיל את האתר.

---

## 10. העתקת נתונים ואימות

### 10.1 בדיקות נתונים לפני (על snapshot ב-SQLite, אף פעם לא על prod חי)
Snapshot: בתוך הקונטיינר, `VACUUM INTO '/app/data/snapshot-YYYYMMDD.db'`. זה עקבי ולא חוסם קוראים. אחר כך מורידים את הקובץ, או מריצים את הבדיקות בתוך הקונטיינר על ה-snapshot.
- **Q-LEN:** לכל `String(n)`: `SELECT max(length(col)), sum(length(col) > n) FROM t;`. ‏`length()` של SQLite סופר תווים, בדיוק כמו `varchar(n)` ב-PG.
- **Q-NUL:** לכל עמודת טקסט/JSON: `SELECT count(*) FROM t WHERE instr(col, char(0)) > 0;`
- **Q-NULL:** לכל עמודה שבמודל היא NOT NULL: `SELECT count(*) FROM t WHERE col IS NULL;`. חשוד במיוחד: עמודות שנוספו ב-ALTER (`is_listed`, `has_discount`, `free_sold_out`, `pay_at_door`, `admits`, `group_*`, `sponsored.placement`, `sponsored_stats.results_views`, `sponsored_stats.results_clicks`, `events.time_known`). ‏`plan_votes.picks` / `ride` / `seats` נוספו ב-ALTER אבל הם nullable גם במודל, אז NULL בהם תקין.
- **Q-JSON:** `SELECT count(*) FROM events WHERE json_valid(genres) = 0;` (לכל עמודת JSON, כולל `ticket_tiers.price_rounds` ו-`scrape_runs.failures`).
- **Q-ORPHAN:** `SELECT count(*) FROM ticket_tiers t LEFT JOIN events e ON e.id = t.event_id WHERE e.id IS NULL;`, וכנ"ל ל-`event_images` ול-`deal_changes`. גם `plan_votes` ו-`plan_events` בלי `plans`, ו-`plans` בלי `events` (צריך להיות 0: `delete_plans` מוחק אותם עם האירוע; FK של `plan_votes` נאכף ב-PG, אז יתום שם יפיל את ה-copy).
- **Q-DUP:** השאילתות מ-§9.3.
- **Q-INT:** `SELECT max(id) FROM <t>;` לכל טבלה עם int PK, מול 2^31. ‏`SELECT max(size_bytes) FROM event_images;`
- **Q-BOOL:** `SELECT DISTINCT is_listed FROM events;` ו-`SELECT DISTINCT time_known FROM events;` (מצפים ל-0/1/NULL בלבד; ב-`time_known` ‏NULL הופך ל-`true`, ה-default).
- **Q-TZ:** `SELECT time_zone, count(*) FROM events GROUP BY 1;`: כל ערך שאינו NULL חייב להיות שם IANA תקין (`zoneinfo.ZoneInfo(x)` לא זורק), וב-Q-LEN מול `String(48)` (השם הארוך ב-IANA הוא 32 תווים). אירוע ישראלי: NULL.
- **Q-SIZE:** `SELECT count(*), sum(length(data)) FROM event_images WHERE data IS NOT NULL;` (קובע את זמן ההעתקה).

### 10.2 `copy-db` (שלב A, כ-60 שורות ב-`cli.py`)
`python -m partylinks copy-db --from <url> --to <url> [--force]`
1. מסרב אם ב-`--to` יש כבר שורות ב-`events` בלי `--force`, כדי שאי אפשר יהיה לדרוס בטעות את ה-PG החי.
2. `Database(to).create_schema()`, ואם `--force`, קודם `drop_all`.
3. לכל טבלה ב-`Base.metadata.sorted_tables` (סדר FK): `select(table).order_by(pk)` ב-stream (`yield_per(2000)`), ואז `insert(table)` ב-batches של 2000 דרך Core. כך ה-types עוברים דרך `UTCDateTime`, `JSON` ו-`Numeric` של SQLAlchemy, באותה סריאליזציה בשני הצדדים. NULL בעמודת NOT NULL מוחלף ב-default של המודל (Q-NULL), ו-`\x00` מוסר (Q-NUL).
4. ב-PG: `setval` לכל sequence, ואחר כך `ANALYZE`. **חובה.** בלי סטטיסטיקות ה-planner עיוור אחרי bulk load.
5. אימות (§10.3). exit code שונה מ-0 אם משהו לא תואם.

עובד בשני הכיוונים (PG ל-SQLite ל-rollback מאוחר). זמן צפוי: שניות עד דקה (כ-28k אירועים, כ-84k tiers, deal_changes, clicks; תמונות על הדיסק).

### 10.3 אימות
- **מספר שורות** לכל טבלה: מקור מול יעד, שוויון מלא.
- **Checksum** לכל טבלה: `sha256` על tuples מנורמלים (datetime ל-ISO UTC, Decimal ל-`normalize()`, JSON ל-`json.dumps(sort_keys=True)`), לפי סדר PK, מחושב ב-Python משני הצדדים דרך אותם models. כך חוסכים הבדלי ייצוג בין ה-DBs.
- **Diff התנהגותי (הכי חשוב):** להרים את האפליקציה מקומית פעמיים, פעם על ה-snapshot ופעם על `partylinks_rehearsal` (scheduler כבוי כרגיל ב-local), ולהשוות JSON של רשימה קבועה: `/events`, `/events?sort=price_asc|price_desc|seen_desc|trending`, `/events?genre=techno`, `/events?q=<עברית>`, `/events?artist=<עברית>`, `/events?city=תל אביב`, `/events/map`, `/events/{slug}` (כולל אירוע בחו"ל ואירוע עם `time_known = false`: שעה מקומית, תאריך בלבד), `/events/rails`, `/events?abroad=true&city=פריז` מול `city=Paris` (אותו total), `/plans/{id}` ו-`/plan/{id}` של תוכנית קיימת (אותן תשובות באותו סדר: `ORDER BY plan_votes.id`), `/sitemap.xml`, `/abroad`, `/venues`, `/artists`, `/categories`. כל הבדל הוא באג (סדר NULL, collation, ILIKE) או מוסבר.

---

## 11. Cutover ו-Rollback

### 11.1 Cutover (שלב C), כ-30 דקות, בשעה שקטה (בוקר, בין crons)
1. **Pre-flight:** שלב A כבר ב-master ורץ ב-prod (psycopg מותקן, `copy-db` קיים). ה-rehearsal עבר (§13). Backups של Postgres פעילים. הודעה ל-sessions אחרים / ב-memory: "חלון migration". הוא לא חוסם push-ים, כי השינוי הוא משתנה ולא קוד.
2. ב-Railway: `PARTYLINKS_SCHEDULER__ENABLED=false`, ואז deploy (D1). מעכשיו SQLite מקבל רק writes קטנים (clicks, sponsored_stats, admin).
3. `railway ssh --service web`, ובו: `VACUUM INTO` ל-snapshot, ואז `python -m partylinks copy-db --from sqlite:///data/snapshot-....db --to $PG_URL`. כולל אימות.
4. ב-Railway: `PARTYLINKS_DATABASE__URL=...` (§8), ואז deploy (D2). בדיקות: `/health`, 10 עמודים, ה-admin, `/admin/api` stats (מספרים זהים ל-snapshot), לוגים בלי שגיאות.
5. `PARTYLINKS_SCHEDULER__ENABLED=true` (או הסרה, כי ב-Railway הערך המקורי הוא true), ואז deploy (D3). מעקב אחרי ה-runs הראשונים: `railway run --service web -- python scripts/run_sources.py --runs`, ובלוגים לחפש `StringDataRightTruncation`, `NotNullViolation`, `deadlock`, "rejected ... saving row by row" (`service.py:491`), ואת זמני ה-regroup.
6. אחרי 24 שעות: `scripts/run_sources.py --report` על כל ה-sources (לפי כלל "run sources and report").

**push של session אחר באמצע:** deploy חדש עם המשתנים הנוכחיים. בין D1 ל-D2 הוא רץ על SQLite עם scheduler כבוי, וזה בטוח. אם זה קטע ssh באמצע ה-copy, מריצים שוב עם `--force` לאותו DB ריק-עדיין. אחרי D2 הוא רץ על PG, ולכן בטוח.

### 11.2 Rollback
- **מהיר (שעות ראשונות):** להסיר את `PARTYLINKS_DATABASE__URL`, ואז deploy. האתר חוזר ל-SQLite המקורי, שלא נגעו בו. מה שאבד: אירועים שנאספו אחרי ה-cutover (נאספים מחדש ב-run הבא), clicks, deal_changes, ושינויי admin/sponsored מהשעות האלה.
- **מאוחר (ימים):** קודם scheduler=false. אחר כך `copy-db --from $PG_URL --to sqlite:///data/rollback.db` (כיוון הפוך), להחליף את קובץ ה-DB, ולבסוף להסיר את המשתנה ולהחזיר את ה-scheduler. כך שום מידע לא-משתחזר (clicks, sponsored_stats, flags, deal_changes) לא הולך לאיבוד.
- **קובץ SQLite הישן נשמר 30 יום** (Q8).

---

## 12. Tests

- **ברירת מחדל:** SQLite ב-`tmp_path`, כמו היום. מהיר, בלי docker.
- **Opt-in PG:** fixture אחד ב-`tests/conftest.py`, `db_url(tmp_path)`, שמחזיר `sqlite:///{tmp_path}/t.db`, או את `PARTYLINKS_TEST_PG_URL` כשהוא מוגדר (עם `drop_all`/`create_all` לכל test). להחליף את 7 ה-URLs הקשיחים (§6.11) ב-fixture.
- הרצה מקומית: `docker run --rm -e POSTGRES_PASSWORD=x -p 5432:5432 postgres:17`, ואז `PARTYLINKS_TEST_PG_URL=postgresql+psycopg://postgres:x@localhost/postgres pytest`.
- tests חדשים, הכרחיים בלבד:
  1. `create_schema` מוסיף עמודת Boolean עם default על PG (תופס את §6.3). מקרה אמיתי לבדוק: DB בלי `events.time_known` ו-`events.time_zone`, ואז `create_schema` (כמו שקרה ב-SQLite ב-2026-10-28).
  2. upsert עם title או `source_event_id` ארוך, ועם `\x00` (תופס את §6.3).
  3. סדר `price_asc`/`price_desc` עם NULL, זהה בשני ה-dialects (Q3).
  4. `copy-db` round-trip SQLite → PG → SQLite, עם checksums שווים.
  5. `tests/test_plans.py` על PG: יצירה, הצבעה ושינוי תשובה (ה-UNIQUE), ומחיקה עם האירוע (ה-FK `plan_votes` → `plans` נאכף ב-PG, אז זה בודק את סדר המחיקה).
- **שמירה על dual-dialect לאורך זמן:** שורה ב-`docs/architecture.md` ("Database") וב-memory: "prod = PostgreSQL. אסור SQL/PRAGMA ספציפי ל-SQLite מחוץ ל-`is_sqlite`. להריץ את ה-suite עם `PARTYLINKS_TEST_PG_URL` לפני push שנוגע ב-DB."

---

## 13. בדיקות ביצועים בגודל prod (שלב B, rehearsal)

מריצים **בתוך Railway**, כי מ-localhost דרך ה-public proxy יש כ-50–150ms RTT שמעוות את התוצאות: ‏`railway ssh --service web`, ואז סקריפט ב-scratch מול `partylinks_rehearsal` (כתובת פרטית), ובמקביל אותו סקריפט מול ה-snapshot של SQLite.
| מה | פונקציה | קריטריון |
|---|---|---|
| regroup מלא | `ScrapeService.regroup` (`service.py:287-290`), `dedup.py:245-249` | ≤ ×1.5 מ-SQLite, ומספר `changed` אחרי pass ראשון כ-0 (אין churn מ-collation) |
| geocode בלי רשת | `service.geocode(network=False)` (`service.py:200`) | ≤ ×1.5 |
| recompute_areas | `admin.py` `/maintenance/areas` | ≤ ×1.5 |
| retention | `purge_expired` (`retention.py`) | ≤ ×1.5 |
| upsert | 140 batches של 200 אירועים סינתטיים (`ScrapedEvent`) + `upsert_events` | ≤ ×1.5. ב-PG זה ה-round-trips, אז לוודא שכל batch הוא מספר קבוע של statements (`db.py:605-615`) |
| עומס מקביל | 20 threads של upsert + 20 threads של קריאת `/events` | בלי deadlock, ובלי `QueuePool limit` |
| EXPLAIN ANALYZE | `/events` ברירת מחדל, `?genre=`, `?q=עברית`, `/events/map`, `api.py:1116` | < 50ms. ה-feed index נבחר. |

אחרי D3 ב-prod: `pg_stat_statements` (אם זמין ב-template) ולוגים של משך run לכל source, להשוואה לשבוע שלפני.

---

## 14. Backups

- **Postgres:** Backups של Railway על ה-volume של שירות Postgres (daily, שמירה של 7 ימים או יותר). **תרגול שחזור אחד** ב-rehearsal: restore ל-DB חדש ובדיקת מספרי שורות.
- **אופציונלי:** `pg_dump -Fc` שבועי ל-volume של `web` (`/app/data/backups`), בלי cron חדש בקוד. YAGNI עד שיהיה צורך.
- **SQLite הישן** כגיבוי קר ל-30 יום.
- היום אין גיבוי ל-SQLite בכלל (לא נמצא בקוד). המעבר משפר את המצב.

---

## 15. שלבים

### שלב A: הכנה, dual-dialect, prod עדיין SQLite (commits רגילים ל-master)
- ‏`has_word()` (פונקציית SQLite, §6.7): גרסה ל-PG עם `~*`, כדי שהחיפוש יעבוד על שני ה-DB לפני ה-cutover. בדיקה: ‏`test_a_short_search_word_is_matched_as_a_whole_word` על שניהם.
כל commit קטן, עצמאי, ונבנה על HEAD טרי (כלל concurrent sessions).
1. `psycopg[binary]` ל-`pyproject.toml` ול-`requirements.txt`, וחידוש `requirements.lock`.
2. `Database.__init__`: נרמול `postgresql://` ל-`postgresql+psycopg://`, pool ל-PG, ו-`connect_args` timeouts (§8).
3. `_literal_default` לפי dialect (§6.3).
4. ניקוי `\x00` ב-`ScrapedEvent`, ו-`String(n)` ל-`Text` לעמודות שחרגו או קרובות לגבול (לפי Q-LEN).
5. `nulls_first`/`nulls_last` מפורש ב-`api.py:383-390`, `api.py:1107`, `seo.py:211`, `seo.py:516`, `seo.py:1132` (Q3).
6. איחוד `_json_contains`, `search._genre` ו-`seo.py:651` ל-helper אחד.
7. `copy-db` ב-`cli.py`, כולל אימות.
8. fixture `db_url` ו-4 tests (§12).
9. אינדקסים בטוחים על SQLite (§9.5), **אחרי** בדיקות §10.1 על snapshot ומדידה.
10. Docs: `docs/architecture.md` ("Database", "Retention"), `docs/deployment.md` (volume ו-"one replica", משתני PG), `docs/configuration.md:12`/`:25`, ההערה ב-`config.py:282`.

**Gate:** suite ירוק על SQLite ועל PG מקומי. prod יציב 48 שעות. החלטת Q1.

### שלב B: Rehearsal (Railway, בלי לגעת ב-web)
1. שירות Postgres, DBs עם collation, `pg_trgm`, Backups (§8).
2. snapshot, ואז `copy-db` ל-`partylinks_rehearsal`, ואז אימות (§10.3) ו-diff התנהגותי.
3. ביצועים (§13). אם חורג, לתקן בשלב A ולחזור.
4. תרגול restore.
5. סגירת ה-public proxy.

**Gate:** כל הקריטריונים ב-§13 עברו. ה-diff ההתנהגותי נקי או מוסבר.

### שלב C: Cutover (§11.1)
writer lock פעיל גם על PG (Q4), שינוי שורה אחת ב-`db.py:571` שנכנס בשלב A מאחורי `if self.is_sqlite or <flag>`, או פשוט תמיד.
**Gate:** שבוע בלי שגיאות DB, וזמני run לא גרועים.

### שלב D: לקצור את התועלת
1. להסיר את ה-writer lock ל-PG (`db.py:571`), ולמדוד שוב עומס מקביל.
2. להסיר את האינדקסים שוברי ה-HOT ב-PG (§9.4) ו-fillfactor, אם לא נכנסו כבר.
3. חיפוש: עמודת `search_text` + GIN ‏`gin_trgm_ops` (§9.4), ואז `text_match`, ‏`has_word` וה-typo עוברים אליה. מדידה לפני ואחרי. JSONB+GIN ל-genres רק לפי מדידה.
4. אחרי 30 יום: למחוק את קובץ ה-SQLite הישן מה-volume.

---

## 16. סיכונים

| # | סיכון | הסתברות/השפעה | מניעה / זיהוי |
|---|---|---|---|
| R1 | `VARCHAR(n)` נאכף, ואירועים נופלים בשקט ב-save שורה-שורה | גבוהה/בינונית | Q-LEN, ‏`Text`, חיפוש "saving row by row" בלוגים |
| R2 | `BOOLEAN DEFAULT 1` מפיל עלייה בהוספת עמודה עתידית | ודאי אם לא מתקנים/גבוהה | תיקון בשלב A + test על PG |
| R3 | collation משנה סדר ב-`dedup.py:259`, ו-regroup מחליף primaries | בינונית/בינונית | `LC_COLLATE C`, לוודא ש-`changed ≈ 0` ב-rehearsal |
| R4 | סדר NULL משתנה ב-price/seen sort | ודאי/נמוכה | Q3 בשלב A |
| R5 | psycopg חסר ב-lock, ו-flip גורם crash loop | נמוכה/גבוהה | שלב A נוחת ורץ 48 שעות לפני flip. rollback = הסרת משתנה. |
| R6 | deadlocks או pool exhaustion כשמסירים את ה-lock | בינונית/בינונית | נשאר בשלב C. ‏`with_db_retry` כבר תופס deadlock. מדידה בשלב D. |
| R7 | latency רשת מאט passes שמבצעים הרבה queries קטנות | נמוכה/בינונית | §13 בתוך Railway. ה-passes כבר bulk. |
| R8 | NULL-ים בעמודות NOT NULL (ALTER בלי NOT NULL) מפילים את ה-copy | בינונית/נמוכה (זה בזמן ה-copy, לא prod) | Q-NULL, ונרמול ב-copy |
| R9 | push של session אחר מוסיף קוד SQLite-only אחרי המעבר | בינונית/בינונית | docs ו-memory, suite על PG לפני push שנוגע ב-DB |
| R10 | Postgres נופל, ו-`/health` מחזיר 503 | נמוכה/גבוהה | Railway restart. rollback ל-SQLite. backups. |
| R11 | unique index חדש על SQLite נכשל בעלייה ומפיל את prod | נמוכה/גבוהה | בדיקת כפילויות על snapshot לפני push (§9.6) |
| R12 | אובדן clicks בזמן החלון | ודאי/זניחה | Q6 |

---

## 17. Decision Log

| # | החלטה | חלופות | נימוק |
|---|---|---|---|
| D1 | גישה A: dual-dialect, ‏copy עצמי, flip דרך משתנה | pgloader+Alembic, להישאר | schema = ORM, rollback של משתנה אחד, לא תלוי ב-push-ים |
| D2 | psycopg 3 sync | asyncpg, psycopg2 | הקוד sync, ו-psycopg2 בלי פיתוח פעיל |
| D3 | `timestamp without time zone` UTC (להשאיר `UTCDateTime`) | `timestamptz` | אפס שינוי התנהגות. אפשר לשנות אחר כך. |
| D4 | `json` בשלב C, JSONB רק לפי מדידה | JSONB מיד | זהות התנהגות. GIN לא נחוץ ב-28k. |
| D5 | `LC_COLLATE C` + `LC_CTYPE C.UTF-8` | ברירת המחדל `en_US.utf8` | סדר זהה ל-SQLite, ו-trigrams עבריים עובדים |
| D6 | writer lock נשאר בשלב C | להסיר מיד | הפרדה בין "החלפת DB" ל"שינוי concurrency". שינוי אחד בכל פעם. |
| D7 | בלי FK ל-group_id, best_listing_id, clicks, sponsored | FK מלא | אלה cache או נתונים ששורדים retention. FK יפיל את retention. |
| D8 | בלי UNIQUE `(source, source_event_id)` | להוסיף | נגזר מה-PK. עלות כתיבה בלי תועלת. |
| D9 | `UNIQUE (ticket_tiers.event_id, position)` | כלום | מחליף אינדקס קיים, בלי עלות נוספת, ותופס באגים |
| D10 | להסיר אינדקסים שוברי HOT ו-booleans | להשאיר הכול | ב-PG הם מונעים HOT, וה-planner ממילא לא בוחר בהם |
| D11 | partial feed index `(start_at, id)` | אינדקס `start_at` רגיל | ה-OR ב-upcoming מונע range. ordered scan עם LIMIT הוא הרווח האמיתי. |
| D12 | pg_trgm ו-GIN נדחים | להוסיף עכשיו | OR על 6 עמודות מבטל את התועלת, ו-description יקר בכתיבה |
| D16 (2026-10-27) | חיפוש: לא FTS5 ב-SQLite. ב-PG: ‏`search_text` מאוחדת + pg_trgm, בשלב D | FTS5 עכשיו; ‏tsvector ב-PG | FTS5 היה נבנה פעמיים. ‏tsvector לא מכיר עברית (אין stemmer), ו-trigram עובד על כל שפה, משרת ILIKE ומוצא שגיאות כתיב. מעדכן את D12: עמודה אחת פותרת את בעיית ה-OR. |
| D13 | בלי Alembic | Alembic | ה-schema ב-PG נבנה טרי מה-ORM. לפי ה-ponytail ב-`db.py:455-456`, להחליף כשיש rename או type change. |
| D14 | SQLite נשאר ל-local ול-tests, PG ב-opt-in | PG בכל מקום | מהירות פיתוח. localhost לא סורק בכל מקרה. |
| D15 | `artists.db` ו-caches נשארים על ה-volume | להעביר ל-PG | מחוץ לתחום. אין concurrency שדורש את זה. |

---

## 18. Exit criteria (לפי ה-skill)
- Understanding Lock: מתועד ב-§1–§3. לא אושר על ידי המשתמש. **צריך אישור על Q1–Q4 לפני שלב A.**
- גישה נבחרה: A (§5).
- הנחות וסיכונים: §2, §16.
- Decision Log: §17.
- Hand-off: זה שינוי בסיכון גבוה, ולכן לפי ה-skill לשקול `multi-agent-brainstorming` על §9 ו-§11 לפני מימוש.

---

## עדכונים

### 2026-10-27 (מול 4a1a6a7)
- **טבלאות הפרסום עברו קובץ:** `SponsoredRow` ו-`SponsoredStatRow` הועברו מ-`db.py` ל-`partylinks/ads/models.py` (commit 4426d5f). שמות הטבלאות, ה-PKs וה-`Base` לא השתנו, ולכן אין השפעה על ה-schema ב-PG ועל `copy-db`. ההפניות ב-§6.3, §7, §9.1, §9.2 ו-§10.1 עודכנו.
- **עמודות חדשות:**
  - `sponsored.placement`: `String(8)`, default `"rails"` (`ads/models.py:27`). ערכים: `rails` / `results` / `both`. בגבול של 8 תווים, ונכנס לבדיקת Q-LEN.
  - `sponsored_stats.results_views` ו-`sponsored_stats.results_clicks`: `Integer`, default 0 (`ads/models.py:42-43`).
  - שלושתן נוספו ב-SQLite ב-`ALTER TABLE ... ADD COLUMN` (`db.py:457`), כלומר nullable, ונכנסות לבדיקת Q-NULL (§10.1). ה-query בדו"ח הפרסום כבר עוטף אותן ב-`coalesce(..., 0)`.
- **בלי שינוי schema:** מקור חדש (`funzing`) ותיקון טבלת "Events per source" באדמין (מציגה כל מקור מוגדר, גם עם 0 אירועים). השינוי ב-admin הוא ב-Python בלבד: אותו `GROUP BY source` על `events` כמו קודם, ואין לו אינדקס חדש או השפעה על PG.
- **הגדרה חדשה:** `enrichment.declared_genres_only` (רשימת מקורות שז'אנרים שלהם נקראים רק מהקטגוריות שהם מצהירים). זה config בלבד, בלי השפעה על ה-DB.

### 2026-10-27 (טבלת חיפושים)
- **טבלה חדשה `searches`** (`SearchRow`, `db.py:269-278`): `id` Integer PK, `at` UTCDateTime עם אינדקס, `q` String(100) (טקסט מנורמל: רווחים מכווצים ו-casefold, חתוך ל-100 תווים), `results` Integer. אין FK, אין IP ואין user agent.
- **כתיבה:** `GET /events?q=...` בעמוד 1 בלבד, בלי header `authorization`, כותב שורה אחת ב-background task (`api.py:1047`, `searches.record`) עם session כתיבה משלו, אחרי שהתשובה נשלחה. ב-PG זה insert רגיל, בלי נעילת ה-writer של SQLite.
- **קריאה:** `GET /admin/api/searches` (`searches.stats`): `WHERE at >= since ORDER BY at`, והספירה נעשית ב-Python כמו ב-`clicks`.
- **Q-LEN:** `q` מוגבל ל-100 תווים כבר בקוד (`searches.normalize`), ולכן `String(100)` ב-PG תקין.
- ב-`copy-db` הטבלה נכנסת כמו `clicks`. אובדן של דקות בין snapshot ל-flip מקובל (Q6).
- **הפניות שזזו:** ב-§9.1 עודכנו ה-PKs של `flags` (`db.py:287`) ו-`scrape_runs` (`db.py:299`). הפניות אחרות ל-`db.py` מתחת לשורה 269 זזו ב-12 שורות.

### 2026-10-27 (סדר רשימת האירועים)
- **query שהשתנה:** `GET /events` במיון לפי תאריך (`sort=start_at`, ברירת המחדל) ממיין עכשיו `ORDER BY local_date, <is_place>, start_at, id`, כש-`<is_place>` הוא `coalesce(extra['happy_hour'] as boolean, false)` (`api.py`, `_IS_PLACE`). כך רשימות מקומות (מועדון או בית קפה "פתוח היום" של HappyTLV, אלפים ב-00:00) באות אחרי האירועים של אותו יום.
- **ב-PG:** `EventRow.extra[...].as_boolean()` של SQLAlchemy מתורגם ל-`(extra ->> 'happy_hour')::boolean` (ב-SQLite: `JSON_EXTRACT`). `extra` צריך להיות `JSONB` כדי שזה יעבוד, וזה כבר בתכנון (§6). אין אינדקס על הביטוי: המיון רץ על השורות שעברו את הסינון, ונמדד ב-SQLite באותו זמן כמו המיון הקודם. אם ב-PG זה יאט בעמוד 1 בלי פילטרים, אפשר להוסיף אינדקס על `(local_date, ((extra ->> 'happy_hour')::boolean), start_at)`.
- **בלי שינוי schema.** קטגוריות: `genre_groups` (משפחות הפילטר) הן config בלבד, וה-API מחשב את הספירות שלהן ב-Python.

### 2026-10-28 (חיפוש ואירועים בחו"ל)
- **פילטר `city`:** במקום `ILIKE` אחד על `city` / `address`, עכשיו `OR` של `ILIKE` לכל שם של העיר (עברית + אנגלית מ-GeoNames, `places.city_abroad`), למשל "פריז" גם `%Paris%`. ב-PG זה אותו `ILIKE` (אין אינדקס עליו גם היום).
- **חיפוש (`search.text_match`):** נוסף `_json_has(source_genres, word)`, כלומר `CAST(source_genres AS TEXT) ILIKE` (כמו `event_types`). ב-PG `source_genres` הוא `JSONB` (§6), ו-`::text ILIKE` עובד אותו דבר.
- **עיר בחו"ל בחיפוש:** `smart_filters` מזהה עיר בחו"ל (100k+ תושבים) ומוסיף `OR` של `text_match` לכל שמותיה. אין שינוי מבני.
- **מיון:** `sort=start_at` עם `abroad=true` חזר ל-`ORDER BY start_at, id`, בלי `local_date` ובלי `<is_place>`.
- **בלי שינוי schema.**

### 2026-10-28 (שעה מקומית בחו"ל, תאריך בלי שעה)
- **שתי עמודות חדשות ב-`events`:** `time_known BOOLEAN NOT NULL DEFAULT TRUE` (False כשהאתר נותן רק תאריך) ו-`time_zone VARCHAR(48)` (אזור הזמן של מקום אירוע בחו"ל, IANA, ‏NULL בישראל). ב-SQLite הן נוספו ב-`create_schema` (ADD COLUMN, שורות קיימות קיבלו `TRUE` / NULL). ב-PG הן נבנות מה-ORM (`create_all`, §7) כ-`BOOLEAN NOT NULL` ו-`VARCHAR(48)`, בלי אינדקס (§9.4): לא מסננים ולא ממיינים לפיהן. ‏`time_known` הוא הדוגמה החיה לבעיית `DEFAULT 1` (§6.3) ונכנס ל-Q-NULL / Q-BOOL, ו-`time_zone` ל-Q-LEN / Q-TZ (§10.1).
- **`local_date` / `time_slot` של אירוע בחו"ל** מחושבים עכשיו באזור הזמן של המקום, לא של ישראל. אירוע בלי שעה מקבל `time_slot` NULL. אין שינוי ב-queries.
- **`GET /events/map`** בוחר גם `time_known`, `time_zone`, `local_date`. אותו `SELECT`, שלוש עמודות נוספות.
- **ערים עם תוספת** ("לונדון, אנגליה", "Saint-Denis (Paris)") מאוחדות ב-Python (`places._city`, `api._merged_cities`), בלי שינוי SQL.
- **ניקוי תיאורים (`parsing.tidy_text`, ftfy):** `events.description` ו-`ticket_tiers.description` נכתבים נקיים (רווחים, שורות, entities, mojibake, NUL). אין שינוי schema; משפיע על Q-NUL (§6.3). ftfy היא תלות Python, לא של ה-DB.
- **שורות קיימות:** מתעדכנות ב-scrape הבא של כל מקור (upsert כותב את כל `_EVENT_FIELDS`). אין backfill. ב-copy ל-PG הן יעברו כפי שהן.
- **הפניות לשורות קוד** בסעיפים הקודמים (למשל `db.py:133`) נכונות ל-4a1a6a7 ויכולות לזוז בכמה שורות. השמות (פונקציות, עמודות) הם העיקר.
- **`events.city` בחו"ל נשמר נקי (`places.city_name`, ב-`enrichment`):** "ברלין, גרמניה" נשמר "ברלין", "BERLIN, GERNANY" נשמר "Berlin". אין שינוי schema ואין שינוי query. הערכים ב-`city` נעשים עקביים יותר, וזה עוזר לשוויון ב-`seo.py` / `blog.py` (אינדקס `city`, §9.4). שורות ישנות מתנקות ב-scrape הבא; עד אז `_merged_cities` מציג את השם הנקי.

### 2026-10-28 ("מי בא איתי?": תוכניות ותשובות)
- **שתי טבלאות חדשות** (חבילה נפרדת `partylinks/plans/`, על אותו `Base`, נוצרות ב-`create_schema` כמו טבלאות הפרסום):
  - `plans`: ‏`id VARCHAR(12)` PK (הסוד של הקישור), ‏`event_id VARCHAR(128)` עם אינדקס, ‏`created_at` (`UTCDateTime`).
  - `plan_votes`: ‏`id` int PK, ‏`plan_id VARCHAR(12)` FK → `plans.id` עם אינדקס, ‏`voter VARCHAR(36)`, ‏`name VARCHAR(40)`, ‏`answer VARCHAR(8)` (yes / maybe / no), ‏`at` (`UTCDateTime`, ‏onupdate), ‏`UNIQUE (plan_id, voter)`.
- **PK / FK / UNIQUE / אינדקסים:** §9.1, §9.2, §9.3, §9.4 (טבלאות אחרות) עודכנו. ‏`plans.event_id` בכוונה בלי FK, ו-`plan_votes.plan_id` עם FK בלי CASCADE.
- **queries חדשים:** `GET /plans/{id}` (PK, ואז `plan_votes WHERE plan_id = ? ORDER BY id`), ‏`POST /plans/{id}/votes` (select לפי `(plan_id, voter)`, ‏`count(*)` לפי `plan_id`, ואז insert או update), ‏`POST /plans` (insert). ‏`db.delete_events` מוחק עכשיו גם `plan_votes WHERE plan_id IN (SELECT id FROM plans WHERE event_id IN (...))` ואז `plans WHERE event_id IN (...)`, לפני `events`. הכול SQL סטנדרטי, אותו דבר ב-PG.
- **Q-LEN:** `name` נחתך ל-40 ב-pydantic לפני הכתיבה, ‏`voter` עד 36 (pattern של אותיות, ספרות ו-`-`), ו-`id` הוא 8 תווים. ב-PG ‏`VARCHAR(n)` לא ייפול.
- **Q-NUL:** `name` עובר `tidy_text` (שמנקה NUL) לפני הכתיבה.
- **retention:** בלי שינוי ב-`purge_expired`: התוכניות נמחקות בתוך `delete_events`.
- **copy-db (§10.2):** שתי הטבלאות ב-`Base.metadata.sorted_tables`, ‏`plans` לפני `plan_votes` (סדר FK). ‏`setval` ל-`plan_votes.id`.

### 2026-10-28 ("איזו מסיבה?" ו"מי מסיע?")
- **טבלה חדשה `plan_events`:** ‏`plan_id VARCHAR(12)` FK → `plans.id`, ‏`event_id VARCHAR(128)` עם אינדקס, ‏`position INTEGER`; ‏PK `(plan_id, event_id)`. האירועים הנוספים שתוכנית משווה (עד 3, ו-`plans.event_id` הוא הראשון).
- **שלוש עמודות חדשות ב-`plan_votes`, כולן nullable:** ‏`picks JSON` (לאן החבר הולך, event ids), ‏`ride VARCHAR(8)` (`drive` / `need`), ‏`seats INTEGER` (מקומות פנויים, 1–8). ב-SQLite נוספו ב-`create_schema` (ADD COLUMN בלי DEFAULT). **בכוונה בלי Boolean**, כדי לא להיתקל בבעיית `DEFAULT 1` של §6.3. ב-PG הן נבנות מה-ORM.
- **PK / FK / אינדקסים / JSON / בדיקות:** §6.5, §9.1, §9.2, §9.3, §9.4, §10.1 עודכנו.
- **queries חדשים:** `POST /plans/{id}/events` (select של האפשרויות לפי `plan_id ORDER BY position`, ואז insert). ‏`GET /plans/{id}` ו-`_plan_out` טוענים את האירועים ב-`events.id IN (...)`. ‏`delete_plans` מוחק עכשיו גם `plan_events WHERE plan_id IN (תוכניות של האירועים) OR event_id IN (...)`, לפני `plans`. הכול SQL סטנדרטי.
- **copy-db (§10.2):** סדר FK: `plans`, ואז `plan_events` ו-`plan_votes`.

### 2026-10-28 (מרכזי ערים בחו"ל, מדינות ו-GeoNames פעם אחת)
- **`geo_cache` (בלי שינוי schema):** ערך חדש ב-`kind`: ‏`'geonames'`, מרכז עיר בחו"ל שנלקח מ-GeoNames (עיר של 50,000+ תושבים) במקום Nominatim. ‏`Geocoder._repoint_abroad` מעדכן שורות קיימות (`session.merge`, ‏UPDATE לפי ה-PK `query`) ומוחק שורה שכבר לא עומדת בכלל (`DELETE FROM geo_cache WHERE query = ?`). ‏`kind` הוא `String(32)`, ‏`'geonames'` נכנס. Q-LEN בלי שינוי.
- **`events.latitude` / `longitude`:** אירועים בחו"ל שהמרכז שלהם היה שגוי עוברים (או מתאפסים ל-NULL לחיפוש מחדש) ב-`bulk_update_by_id`. אותו executemany כמו היום (§6.4).
- **`countries.py`, `geonames.py`:** pycountry ו-geonamescache נטענים פעם אחת לתהליך. ‏GeoNames נשמר כקובץ JSON על ה-volume (§6.9). שום טבלה לא השתנתה, ושום query לא השתנה.
- **שדות JSON-LD** (`validFrom`, ‏`organizer`, ‏`performer`, ‏`endDate`, ‏`addressCountry`): נבנים ב-Python מעמודות קיימות (`first_seen_at`, ‏`organizer`, ‏`venue_name`, ‏`source`, ‏`local_date`, ‏`country`). אין query חדש.

### 2026-10-28 (שלב A מומש בקוד, לא ב-commit)
- **Driver ו-pool:** `psycopg[binary]` ב-`pyproject.toml`, `requirements.txt` ו-`requirements.lock` (3.3.6). `db.postgres_url` מנרמל `postgres://` / `postgresql://` ל-`postgresql+psycopg://`. ה-pool, ה-timeouts (`statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout`), ה-keepalives, `application_name`, `retries`, `serialize_writes`, `events_fillfactor` ו-`copy_batch` הם הגדרות ב-`database` (`config.py`, `settings.yaml`), בלי ערכים קשיחים בקוד. `pool_pre_ping` ו-`pool_use_lifo`.
- **Writer lock (Q4):** `database.serialize_writes`: ‏`null` = רק SQLite (ברירת מחדל: על PG כותבים במקביל, נעילות שורה, upsert ממוין לפי id). לשבוע הראשון אחרי ה-cutover: `PARTYLINKS_DATABASE__SERIALIZE_WRITES=true` ב-Railway, בלי commit.
- **Retry:** `with_db_retry` חוזר על SQLSTATE ‏40001, 40P01, 55P03, 57P01-03, 08xxx, חיבור שנפל (`connection_invalidated`) ו-pool מלא. לא על statement timeout ‏(57014).
- **create_schema:** `pg_advisory_lock` סביב ה-DDL; `_literal_default` לפי dialect (`true` ב-PG); עמודה חדשה עם default ו-NOT NULL במודל נוספת `NOT NULL DEFAULT x`; כל אינדקס נבנה בטרנזקציה משלו, וכישלון נרשם ב-log ולא מפיל את העלייה (§9.6); `fillfactor` של `events` מוגדר ב-PG.
- **Types (§6.3):** ‏`events.id`, `source_event_id`, `slug`, `venue_slug`, `city`, `group_id`, `best_listing_id`, `ticket_tiers.source_ticket_id`, `event_images.content_type`, `geo_cache.query` / `city`, `clicks.event_id` / `city`, `sponsored*.event_id`, `plans.event_id`, `plan_events.event_id` עברו ל-`Text`. JSON הוא `JSONB` ב-PG (`db.JSONType`), JSON ב-SQLite (Q5 / D4 שונו).
- **NUL:** `models.without_nul` כ-validator ‏`before` על `ScrapedEvent` ו-`TicketTier` (בלי הקצאה כשאין NUL). `copy-db` מנקה גם שורות ישנות.
- **has_word (§6.7):** `db.whole_word(column, word)`: ב-SQLite הפונקציה `has_word`, ב-PG ‏`~*` עם מחלקת תווים מפורשת (לא תלוי collation/ctype). המילה וה-regex הם bind params שנבנים ביצירת הביטוי, כדי שה-statement cache לא ישתמש בדפוס של חיפוש אחר (באג שה-test תפס).
- **סדר NULL (Q3):** `nulls_first()` / `nulls_last()` ב-`api._SORT_COLUMNS`, במיון לפי תאריך, ב-alternatives, ב-rails וב-`seo.py`.
- **Collation (R3):** `dedup.plan` ממיין ב-Python לפי code point, ולא ב-`ORDER BY id`, כך שה-regroup לא תלוי ב-collation של ה-DB.
- **אינדקסים (§9.4):** חדשים: `ix_events_feed (local_date NULLS FIRST, start_at NULLS FIRST, id) WHERE is_primary IS true AND is_listed IS NOT false` (PG בלבד, `ddl_if`; משרת את ה-ORDER BY בעזרת incremental sort), `uq_ticket_tiers_event_position`, `ix_deal_changes_event_at`. הוסרו מהמודל (שוברי HOT / לא סלקטיביים): `start_at`, `last_seen_at`, `offers_changed_at`, `is_listed`, `is_primary`, `in_target_city`, `is_weekend`, `has_free_entry`, `group_has_free_entry`, `day_of_week`, `time_slot`, `entry_type`, `ticket_tiers.kind`, ו-`ix_*_event_id` שהוחלפו ב-composite. ב-SQLite הקיים הם נשארים (create_schema לא מוחק).
- **FK:** `plan_votes.plan_id` ו-`plan_events.plan_id` עם `ON DELETE CASCADE` במודל (ב-PG שנבנה טרי; `delete_plans` עדיין מוחק ילדים קודם).
- **copy-db (§10.2):** `partylinks copy-db --to URL [--from URL] [--force]` ב-`dbcopy.py`. סירוב כשביעד יש שורות, העתקה דרך ה-models ב-batches, תיקון NUL / NULL / אורך עם ספירה, `setval` ו-`ANALYZE` ב-PG, ואימות: מספר שורות ו-sha256 על digests ממוינים (לא תלוי בסדר או ב-collation).
- **Tests (§12):** `tests/conftest.py`: ‏`PARTYLINKS_TEST_PG_URL` מריץ כל test על PG. `tests/test_postgres_ready.py`: עמודות שנוספו ב-ALTER, id ארוך ו-NUL, whole word, סדר NULL, ‏copy-db round trip. נבדק מקומית על PostgreSQL 16.2 (`pgserver`).
- **לא בוצע:** שלבים B–D (שירות Postgres ב-Railway, rehearsal, cutover). שום דבר לא נשלח ל-git או ל-Railway.

### 2026-10-28 (מילון האמנים עבר ל-DB הראשי)
- **בוצע:** `artists.db` (קובץ SQLite נפרד) הוחלף בשתי טבלאות ב-DB הראשי, `artists` ו-`artist_profiles` (`artistdb.py`, על אותו `Base`, נוצרות ב-`create_schema`). D15 בוטל לגבי המילון.
- **Schema:** `artists`: ‏`key TEXT` PK (שם casefold), `name TEXT`, `seen INTEGER`, `blocked BOOLEAN`, `first_seen` / `last_seen` (`UTCDateTime`). `artist_profiles`: ‏`key TEXT` PK, `name`, `source`, `kind`, `genre`, `image_url`, `spotify_url`, `profile_url` (`TEXT`), `links` (`JSONB` ב-PG), `updated_at`. בלי FK: שמות שורדים את retention בכוונה. בלי אינדקסים נוספים: נקראים במלואם (`load`, `profiles`).
- **Queries:** `record`: ‏`INSERT ... ON CONFLICT (key) DO UPDATE SET seen = artists.seen + excluded.seen` (שורות ממוינות לפי key, סדר נעילה קבוע), **בתוך אותה טרנזקציה** של `upsert_events` ב-`service._save_batch`. `save_profiles`: ‏upsert עם `COALESCE(excluded.x, x)`. `set`: ‏upsert של `blocked`. כתיבות עצמאיות עוברות `with_db_retry`.
- **מיגרציה:** בעלייה, `ArtistBook(db, enrichment.lineup.artists_db)` מייבא את הקובץ הישן פעם אחת, רק כשהטבלאות ריקות (`ON CONFLICT DO NOTHING`, בטוח גם לשני תהליכים). הקובץ נשאר על ה-volume כגיבוי.
- **Tests:** `test_the_old_artist_file_is_imported_once`; ‏`test_artist_book_learns_and_blocks` ו-`test_tickchak_artist_profiles_merge_into_artist_book` רצים על ה-DB.
