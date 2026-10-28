# תוכנית ביצועים: PostgreSQL ב-production

נכתב ב-2026-10-28, אחרי המעבר ל-PostgreSQL ופיצול ה-web וה-worker. כל שלב עצמאי, נמדד לפני ואחרי, ויש לו rollback.

## מצב בסיס (נמדד ב-production, 2026-10-28 בערב, קריאה בלבד)

| מדד | ערך |
|---|---|
| גודל ה-DB | ‏204MB, ‏`events` ‏135MB (‏55,751 שורות) |
| Cache hit | ‏99.76% |
| חיבורים | ‏19 מתוך 100 (18 idle, ‏1 idle in transaction) |
| סריקות מלאות של `events` | ‏26,896, ‏1.36 מיליארד שורות נקראו, מאז העתקת הנתונים באותו יום |
| `ticket_tiers` | ‏8,238 שורות מתות מתוך 47,067 (17%), ‏0 עדכונים, רק DELETE + INSERT |
| הגדרות | `shared_buffers` ‏128MB, ‏`work_mem` ‏4MB, ‏`random_page_cost` ‏4, ‏`jit` ‏on, ‏`effective_cache_size` ‏4GB |
| `pg_stat_statements` | ב-`shared_preload_libraries`, אבל ה-extension לא נוצר |

שאילתת המדידה (קריאה בלבד, להריץ לפני ואחרי כל שלב):

```sql
SELECT relname, seq_scan, seq_tup_read, idx_scan, n_live_tup, n_dead_tup, n_tup_upd, n_tup_hot_upd
FROM pg_stat_user_tables WHERE relname IN ('events', 'ticket_tiers', 'event_images');
```

## שלב 1: מטמון הדפים לא מתאפס בכל כתיבה (בוצע, commit `40ec581`)

- **הבעיה:** כל commit של ה-worker (כל כמה שניות בזמן סריקה) סימן את כל הדפים במטמון כמיושנים, וכל ביקור בנה את הדף מחדש וסרק את `events`.
- **התיקון:** `ResponseCache(min_fresh=...)`: דף צעיר מ-`api.cache_min_fresh_seconds` (‏30) נשאר תקף גם אחרי כתיבה. `api.cache_ttl_seconds` (‏60) כמו קודם. שניהם ב-`settings.yaml`.
- **מחיר:** עריכה ב-admin מופיעה באתר אחרי עד 30 שניות.
- **מדידה:** קצב `seq_scan` של `events` בשעה, לפני ואחרי ה-deploy.
- **Rollback:** `PARTYLINKS_API__CACHE_MIN_FRESH_SECONDS=0` ב-Railway.

## שלב 2: הפעלת `pg_stat_statements` (בוצע בקוד)

- **מה:** `CREATE EXTENSION IF NOT EXISTS pg_stat_statements` בתוך `create_schema`, בטרנזקציה משלו ולא fatal (כמו `_create_index`). כך זה קורה בכל deploy, בלי פעולה ידנית בשרת, ובלי לשבור localhost או tests (ב-`pgserver` המקומי אין את ה-extension בכלל, רק `plpgsql` ו-`vector`: נרשם warning והעלייה ממשיכה).
- **שימוש:** Top 15 לפי `total_exec_time` אחרי שעה של עומס רגיל. התוצאה קובעת את שלב 5.
- **אופציונלי:** כרטיס "שאילתות איטיות" במסך הניהול (קריאה מה-view). רק אם יידרש.
- **Rollback:** `DROP EXTENSION pg_stat_statements`. אין השפעה על נתונים.

## שלב 3: כוונון הגדרות Postgres (בוצע 2026-10-28 21:34)

- **לפני:** לבדוק ב-Railway (Metrics של שירות `Postgres`) כמה RAM השירות מקבל ומשתמש בפועל. `shared_buffers` נגזר מזה.
- **הפקודות** (משתמש `postgres`, נשמר ב-`postgresql.auto.conf` על ה-volume של Postgres):

  ```sql
  ALTER SYSTEM SET shared_buffers = '<25% מה-RAM>';  -- דורש restart
  ALTER SYSTEM SET random_page_cost = 1.1;            -- SSD
  ALTER SYSTEM SET jit = off;                         -- שאילתות קצרות: ההידור רק מאט
  ALTER SYSTEM SET work_mem = '16MB';                 -- מיון ו-hash בלי קבצים זמניים; כפול עד 80 חיבורים
  ALTER SYSTEM SET effective_cache_size = '<75% מה-RAM>';
  SELECT pg_reload_conf();
  ```

  ואז restart לשירות `Postgres` ב-Railway (בשביל `shared_buffers`). ה-web וה-worker מחכים לו (`database.startup_wait_s`, retry לפי SQLSTATE).
- **מתי:** בשעה שקטה. ה-restart מנתק את כל החיבורים לכמה שניות.
- **אישור:** שינוי בשרת ה-DB של production. מבוצע רק אחרי אישור מפורש.
- **מדידה:** `SHOW` לכל הגדרה, זמני `/events`, ‏`/abroad/france` ו-`/health` במבחן העומס (‏`burst.py`, ‏30 במקביל).
- **Rollback:** `ALTER SYSTEM RESET <name>` לכל הגדרה, `pg_reload_conf()`, ו-restart.

## שלב 4: מחירי כרטיסים בלי כתיבה מיותרת (בוצע, `db._write_tiers`)

- **הבעיה:** `upsert_events` (`db.py`) מוחק ומכניס מחדש את כל ה-tiers של כל אירוע בכל סריקה, גם כשדבר לא השתנה. כל DELETE משאיר שורה מתה, וכל batch מזיז את `Database.changes`.
- **התיקון:**
  1. `INSERT ... ON CONFLICT (event_id, position) DO UPDATE SET ... WHERE (עמודות) IS DISTINCT FROM (excluded)`, על `uq_ticket_tiers_event_position` הקיים. שורה שלא השתנתה לא נכתבת בכלל.
  2. `DELETE` רק ל-positions שמעבר למספר ה-tiers החדש של האירוע (‏tier שירד מהאתר).
  3. `fillfactor` ל-`ticket_tiers` (כמו `events_fillfactor`, הגדרה ב-`database`): עדכון של `remaining`, שזז כל הזמן, נשאר HOT כי אף אינדקס לא מכסה אותו.
- **נשמר:** `deal_changes` ממשיך לחשב שינויים מה-tiers הישנים לפני הכתיבה, כמו היום.
- **Tests:** tier שלא השתנה לא נכתב (`xmin` לא זז), ‏tier שהשתנה מתעדכן, ‏tier שנעלם נמחק, ‏`deal_changes` זהה להיום.
- **מדידה:** `n_dead_tup` ו-`n_tup_hot_upd` של `ticket_tiers` אחרי יום.
- **Rollback:** revert של ה-commit.

## שלב 5: לפי `pg_stat_statements`

- השאילתות היקרות משלב 2: ‏`EXPLAIN (ANALYZE, BUFFERS)` לכל אחת, ואינדקס או שינוי שאילתה רק כשיש מדידה.
- **אינדקסים בלי שימוש:** לבדוק שוב בעוד שבוע (2026-11-04). היום עם 0 סריקות: `ix_events_group_min_price`, ‏`ix_events_venue_slug`, ‏`ix_events_group_entry_type`, ‏`ix_deal_changes_source`, ‏`ix_deal_changes_kind`, ‏`ix_clicks_event_id`. מה שעדיין 0 יוסר מהמודל ו-`DROP INDEX CONCURRENTLY` (‏`create_schema` לא מוחק).
- **החיפוש:** אינדקס `pg_trgm` לחיפוש טקסט, לפי `postgres-migration.md`, אם שאילתות החיפוש יופיעו ב-top.

## תוצאות (2026-10-28 ערב)

- **שלב 3 בוצע:** שירות ה-Postgres מוגבל ל-24GB ומשתמש ב-0.26-0.65GB, וה-DB ‏204MB. לכן `shared_buffers=1GB` (מחזיק את כל ה-DB עם מקום לגדול; Railway מחייב לפי שימוש) ולא 25%, ‏`effective_cache_size=8GB`, ‏`random_page_cost=1.1`, ‏`jit=off`, ‏`work_mem=16MB`. ב-`postgresql.auto.conf`, ‏restart ל-Postgres ב-19:34 UTC, האתר ענה 200 כל הזמן.
- **מבחן עומס** (‏50 בקשות, 30 במקביל, אחרי שהמטמון התחמם): חציון 0.31 ש', ‏p95 ‏0.47 ש', הכול ב-0.6 ש'. בבוקר: חציון 0.77 ש', ‏p95 ‏3.96 ש'.
- **`pg_stat_statements` מצא את מקור הסריקות המלאות:** ‏`city_pages`, ‏`city_genre_counts`, ‏`region_pages` ו-`abroad_countries` ב-`seo.py`: ‏`SELECT events.city / genres / area` על כל האירועים הקרובים, כ-19,700 קריאות מ-11:24 UTC, ‏44-106ms כל אחת. כל דף SEO שאל אותן כמה פעמים, ולפעמים בלולאה.
- **שלב 6 (בוצע):** התשובות שלהן נשמרות ל-`api.cache_ttl_seconds` (‏60 ש') ב-`cachetools.TTLCache` (`seo._facet`). הגיל הזה ממילא מותר לדף במטמון.

## סדר ביצוע

1. שלב 1: ב-production מ-2026-10-28 20:08.
2. שלב 2: commit קטן, deploy, ושעה של איסוף נתונים.
3. שלב 4: commit, tests, מדידה בגודל production (`measure-at-prod-size`).
4. שלב 3: אחרי אישור, בשעה שקטה.
5. שלב 5: לפי הנתונים.

כל שינוי בטבלאות, עמודות או שאילתות נרשם גם ביומן העדכונים של `postgres-migration.md`.
