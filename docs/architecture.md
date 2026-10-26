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
- the lineup (DJs and performers), using the artist dictionary in `artists.py`
- local date, weekday, time slot and weekend flag (Asia/Jerusalem)
- entry type: `free`, `paid` or `free_and_paid`
- city, target-city match and area

## Places (`geo.py`, `venues.py`)

- **Coordinates for the map.** These come from, in order: a manual pin in `geocoding.venues`,
  another event at the same venue or address, then OpenStreetMap Nominatim (cached in
  `geo_cache`). Only street-level answers inside the right city are trusted.
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

## Events abroad

Sources that sell shows abroad set `abroad_only`. Their events carry an ISO country code, which
keeps them out of the Israeli feed and puts them in the `/abroad` section.

`scrapers/abroad.py` handles the location data for these sources:

- **Country names to ISO codes**, in Hebrew or English, via `pycountry`.
- **Local time zones**, looked up by city and then by country, via `airportsdata`.

## Code layout

```
config/settings.yaml          all settings (no hard-coded URLs or limits in code)
content/blog/                 blog posts (HTML with a YAML header)
src/partylinks/
  config.py                   settings models, YAML + PARTYLINKS_* env overrides
  models.py                   ScrapedEvent / TicketTier: what every scraper produces
  scrapers/base.py            per-source transport: retries, proxy, parallel pagination
  scrapers/<source>.py        one module per site
  scrapers/abroad.py          country codes and time zones for sources abroad
  parsing.py                  generic parsing helpers
  enrichment.py               genres, lineup, time categories, free/paid, city
  artists.py                  artist dictionary (its own SQLite file)
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
  logs.py                     one logging setup for every process
  cli.py                      command line
web/                          React + TypeScript website (Vite)
tests/                        pytest, with real-page fixtures under tests/fixtures/
```

## Database

SQLite in WAL mode, so source threads can write concurrently. Writes are batched: one multi-row
upsert per batch of events, and bulk insert or delete for tickets and images.

`Database.create_schema()` creates missing tables and adds new columns automatically (additive
only). A rename or type change needs a real migration.
