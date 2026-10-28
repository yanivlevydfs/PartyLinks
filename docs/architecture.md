# Architecture

SPOTX is one Python process (FastAPI) that does three things:

1. **Collects:** a scheduler runs each source on its own cron.
2. **Stores:** events go into SQLite.
3. **Serves:** the REST API, the React website and server-rendered pages for crawlers.

## One source run

`service.py` runs every source end to end, in this order:

```
scrape -> enrich -> save -> artwork -> regroup duplicates -> record the run
```

- **Isolation.** Each source run has its own worker thread, asyncio loop, HTTP client, lock and
  timeout (`scheduler.run_timeout_seconds`). A slow, failing or blocked site never delays or
  breaks another source.
- **Parallel inside a source.** Listing pages, event details and artwork downloads run
  concurrently, up to `http.concurrency`.
- **Bad data stays small.** One event that fails to parse or enrich is recorded and skipped; the
  rest of the batch is saved. Collected events are flushed even on timeout or block.
- **Every run is recorded** in `scrape_runs`: counts, status (`success`, `partial`, `failed`,
  `blocked`) and per-event failures.

## Enrichment (`enrichment.py`)

It runs after scraping and never affects how a site is fetched. It adds:

- canonical genres and event types (party, concert, theatre, cinema, tech...)
- the lineup (DJs and performers), using the artist dictionary in `artistdb.py` (tables `artists`,
  `artist_profiles` in the main database)
- local date, weekday, time slot and weekend flag (Asia/Jerusalem; an event abroad in its venue's own
  time zone, `time_zone`). A source that gives a date without a time sets `time_known = false`: the day
  is kept, there is no time slot, and the site, the calendar entry (all day) and the JSON-LD show the
  date only, never a made-up 00:00
- descriptions cleaned on the way in (`parsing.tidy_text`, on `ScrapedEvent` and again on output):
  ftfy repairs broken encodings, entities and line breaks; then stray markup, every kind of space,
  trimmed lines, one blank line at most, a line repeated right after itself once
- entry type: `free`, `paid` or `free_and_paid`
- city, target-city match and area

## Places (`geo.py`, `venues.py`)

- **Coordinates for the map.** These come from, in order: a manual pin in `geocoding.venues`,
  another event at the same venue or address, then OpenStreetMap Nominatim (cached in
  `geo_cache`). Only street-level answers inside the right city are trusted.
- **Events abroad** are not on the map, but get their city's centre for the weather: the GeoNames city
  of that name in that country when it has 50,000+ people (`places.city_point`, no network), else
  Nominatim. A centre found wrong later (Nominatim's village of the same name) is corrected on the
  next geocoding pass; a venue's own point from the seller is never touched.
- **Venue names.** Spellings of the same place are merged by location (same spot or same street
  and number), not by alias lists.

## Duplicates (`dedup.py`)

The same event is often listed several times: on several platforms, or once per promoter or
entry time. Every listing is kept, because each is a real purchase link with its own prices.
Matching listings share a `group_id`, and one of them is primary:

- `/events` shows one event per group.
- `/events/{id}` lists the others as `alternatives`.

## Deals over time

Each scan replaces an event's ticket tiers and compares them with the previous state. Every
change is stored in `deal_changes`: `new`, `price_down`, `price_up`, `free_entry_started`,
`free_entry_ended`, `sold_out`, `back_on_sale`, `tiers_changed`, `delisted`, `relisted`.

- **Unlisted events are hidden.** An event a complete run no longer returns is marked unlisted
  and hidden.
- **Time still counts between scans.** "Free until 23:30" stops being free at 23:31.

## Retention (`retention.py`)

Only current events are kept. An hourly job purges events that are over, with their tickets,
artwork and change history. A party that started last night and is still running stays until it
ends (`in_progress_max_hours`). Run history older than `keep_runs_days` is deleted too, and SQLite
gives the freed space back to the OS.

## Artwork (`media.py`)

Image URLs are always stored. The images themselves are downloaded into the database (default) or
onto disk, and served at `/images/{id}`.

With disk storage (production), artwork is kept as WebP only: each file becomes two resized
copies, `<file>.640.webp` for cards and `<file>.1280.webp` for the event page (quality
`webp_quality`, no upscaling, EXIF rotation applied, transparency kept), and the original is
deleted. Only animations, pictures over 50M pixels and unreadable files keep their original.
Every source stores artwork through `MediaStore._store`, which writes the download, converts it
and deletes it (`webp_only`), on a worker thread after the run's scrape, so every scraper's images
are WebP right at import. Copies are named after the file (`local_path` keeps the original name),
so a flyer shared by several listings shares its copies, and `delete_files` removes them.

Originals stored before WebP-only storage are converted by `build_missing_variants`. It starts in
the background after the startup maintenance (it never delays the scrapers), runs
`variant_threads` threads, makes any missing copies and deletes each original (so a restart
resumes), and logs progress every 500 files.

The site requests `/images/{id}/640.webp` or `/images/{id}/1280.webp` (`sized()` in `web/src/ui.tsx`).
That route never converts on request. A copy is served with a one-year `immutable` cache, because
an id's picture never changes: a new URL is a new row. The `.webp` extension lets Cloudflare cache
it, while the bare `/images/{id}` is not cached at the edge. Until the copy exists, the route serves
the original with a one-hour cache. `/images/{id}` (used by `og:image`, JSON-LD and `cover_url`)
serves the original while it exists and the 1280 copy (`image/webp`) once it is converted.

Every way artwork rows go away removes the files too, both copies and any original left, unless another row
still uses the same file: retention (`retention.py`, scheduled and `POST /maintenance/retention`)
and an image URL a source replaced (`upsert_events` returns the stale files, and `service._save_batch`
deletes them). After each retention pass, `sweep_orphans` deletes any file under the media
directory that no row claims, such as leftovers of crashes or earlier bugs. It keeps files changed
in the last hour, because a download writes its file before its row is saved. It deletes nothing
when the image table holds no stored file. It also runs on demand through
`POST /admin/api/maintenance/orphans`.

## Events abroad

Sources that sell shows abroad set `abroad_only`. Their events carry an ISO country code, which
keeps them out of the Israeli feed and puts them in the `/abroad` section.

`scrapers/abroad.py` handles the location data for these sources:

- **Country names to ISO codes**, in Hebrew or English: `countries.py` (pycountry, one index per process).
- **Local time zones**: the GeoNames city (Hebrew names too), then an airport's city, then the country's
  main zone (`airportsdata`).
- **City names** without the seller's extras ("ברלין, גרמניה" is stored as "ברלין", `places.city_name`).
  The city list shows a city once in either language, and the city filter and the search match both
  names ("פריז" also finds "Paris").
- **Weather** for every event abroad with a city, at its venue's time; a date-only event takes that day's
  noon forecast.

## Sponsored placements (`ads/`)

All advertising code lives in `src/partylinks/ads/` and `web/src/ads/`. The rest of the app touches
it through two hooks: `api.py` includes its routers, and the `/events` list calls
`ads.service.promoted_for`. The admin picks paid events (`sponsored` table, by event id: scrapes
rewrite event rows, so a flag there would be lost). Each pick has a `placement`: `rails` (the side rails on every page,
`/events/sponsored`), `results` (first in the event results it matches) or `both`. Picks made
before results existed are `rails`.

For results, `GET /events?sponsored=true` evaluates the list's own filter clauses on the picks'
duplicate groups only (primary card, upcoming, campaign not over): three small queries per list
build, nothing per row. The clauses are a selected column there, not the `WHERE`, because SQLite's
planner would otherwise scan a filter column's index over the whole table (measured: 100 ms vs 2 ms
at 28k events). The matches lead page 1 and are excluded from
the rest of the list. The answer sits in the response cache like any list; an admin save or switch
is a write, which makes every cached entry stale, so the change shows within the browser's 30 s.
Views and clicks go to `sponsored_stats`, with the part from results counted separately.

## Plans: "who's coming with me?" (`plans/`)

WhatsApp gives a web page no way to open a group or a poll, so the poll is ours. The 👥 button on an
event card or page creates a plan (`/plan/<id>`, a random 8-character id); the creator sends it with a
ready WhatsApp message (the link preview is server-rendered, never indexed). Friends answer with a name
only, no sign-up: in / maybe / not this time, which of the plan's events (up to four of the same day,
"which party?"), and a car with free seats or the need of a ride. A plan whose event has free entry with
a known end shows when it closes, with a ready reminder for the group and a calendar alarm.

Each browser keeps a random voter key (localStorage) to change its own answer; keys are never sent back.
Up to 60 answers per plan. A plan is deleted with its own event (`db.delete_events` calls
`plans.routes.delete_plans`); a compared event that is over just leaves the options. All code lives in
`src/partylinks/plans/` and `web/src/plans/`; the rest of the app has one-line hooks only.

## Code layout

```
config/settings.yaml          all settings (no hard-coded URLs or limits in code)
content/blog/                 blog posts (HTML with a YAML header)
src/partylinks/
  config.py                   settings models, YAML + PARTYLINKS_* env overrides
  models.py                   ScrapedEvent / TicketTier: what every scraper produces
  scrapers/base.py            per-source transport: retries, proxy, parallel pagination
  scrapers/<source>.py        one module per site
  scrapers/abroad.py          time zones for sources abroad (country codes: countries.py)
  countries.py                every country (pycountry), one index built once per process: codes,
                              English and Hebrew names; the only module importing pycountry
  geonames.py                 GeoNames cities (geonamescache) read once and cached small in data/
                              (geonames-<version>.json); the only module importing geonamescache
  places.py                   Israeli localities and cities abroad, from geonames.py
  parsing.py                  generic parsing helpers
  enrichment.py               genres, lineup, time categories, free/paid, city
  artistdb.py                 artist dictionary (main database; imports the old artists.db once)
  artists.py                  ArtistProfile, what a ticket site tells about an artist
  geo.py, venues.py           coordinates and venue names
  dedup.py                    duplicate grouping
  media.py                    artwork download and storage
  retention.py                purge finished events
  db.py                       schema, lightweight auto-migration, bulk writes
  service.py                  threaded runner
  scheduler.py                one cron job per source + retention
  api.py                      FastAPI app, public API, /go/ redirect
  admin.py                    admin API (/admin/api)
  security.py                 headers, host check, bot blocking, rate limits, bans
  seo.py                      server-rendered pages, sitemap, robots.txt, llms.txt
  blog.py                     blog / magazine
  utm.py, clicks.py           ticket-link UTM tags and click counting
  plans/                      "who's coming with me?" plans: models.py (plans, plan_votes,
                              plan_events), routes.py (API, link preview, clean-up with the event)
  ads/                        advertising, apart from the rest: models.py (sponsored,
                              sponsored_stats tables), routes.py (rails, hits, admin picks),
                              service.py (sponsored first in matching results)
  logs.py                     one logging setup for every process
  cli.py                      command line
web/                          React + TypeScript website (Vite)
web/src/plans/                the plan page, the 👥 button, which-party, rides, free-entry reminder
web/src/ads/                  the paid side rails, the results' Sponsored label, their CSS and the admin's sponsored tab
tests/                        pytest, with real-page fixtures under tests/fixtures/
```

Heavy libraries load once per process, each behind one module: pycountry (`countries.py`),
geonamescache (`geonames.py`, plus a small disk cache, so later starts skip the 96 MB of city files),
airportsdata (`scrapers/abroad.py`, `airports()`).

## Database

One code base, two databases: SQLite (localhost, tests, and production until the switch) and
PostgreSQL (production, `PARTYLINKS_DATABASE__URL=postgresql://...`; plan and status in
[postgres-migration.md](postgres-migration.md)). Writes are batched: one multi-row upsert per batch of
events, and bulk insert or delete for tickets and images.

- **SQLite:** WAL mode, one writer at a time (this process's writers queue on a lock).
- **PostgreSQL:** psycopg 3, a connection pool (`database.pool_size` / `max_overflow` / `pool_timeout`,
  pre-ping, recycle), TCP keepalives and server-side `statement_timeout`, `lock_timeout` and
  `idle_in_transaction_session_timeout`. Writers run concurrently on row locks (rows are always
  written in id order, so they never deadlock); `database.serialize_writes: true` queues them as on SQLite.
- **Transient errors are retried** (`db.with_db_retry`, `database.retries`): a locked SQLite file,
  deadlock, serialization failure, lock timeout, a dropped connection or a full pool.
- **Same results on both:** NULLs sort as the smallest value everywhere (`nulls_first` / `nulls_last`),
  the short-word search rule is `db.whole_word` (a Python function on SQLite, a regex on PostgreSQL),
  JSON is JSONB on PostgreSQL, scraped text is stripped of NUL characters (`models.without_nul`),
  ids and scraped text are `TEXT` (PostgreSQL enforces `VARCHAR(n)`), and duplicate grouping sorts
  in Python, never by the database's collation.
- **Keys:** every table has a primary key; foreign keys with `ON DELETE CASCADE` from tickets, images
  and deal changes to events, and from plan answers and options to plans. Clicks, sponsored picks and
  plans point at events without a foreign key on purpose: they outlive retention or go with it in code.

`Database.create_schema()` creates missing tables, adds new columns (with their default, `NOT NULL`
where the model says so) and builds missing indexes, at every start. On PostgreSQL it runs under an
advisory lock (one process at a time); an index that cannot be built is logged and the site still
starts. A rename or type change needs a real migration.

`partylinks copy-db --to URL [--from URL] [--force]` (`dbcopy.py`) copies every table to another
database through the same models, then verifies each table: row count and a checksum of every row.
