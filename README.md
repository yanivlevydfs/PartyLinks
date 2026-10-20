# Pulse (דופק)

A Python backend that collects Tel Aviv party events from Israeli ticketing sites on a schedule. For every event it captures:

- every ticket tier, with its price rounds, booking fee, status and whether it is free or paid
- the event artwork
- the lineup (DJs and performers)
- genres, local date, weekday, time slot, venue and city

Everything is served through a REST API.

| Source | Listing (pagination / "load more") | Per-event detail |
|---|---|---|
| `goout` (go-out.co) | `POST /endOne/getEventsByTypeNew`, `skip`/`limit` | `__NEXT_DATA__` on the event page; all tiers from `GET /endOne/loadEventTickets` |
| `eventer` (eventer.co.il) | `/sliders/categories/<cat>/events.js` (single payload) | `/events/explainNames/<link>.js` + `/events/<id>/ticketTypes.js` |
| `zygo` (zygo.co.il) | `api.zygo.co.il/v1/event/<city>`, `skip` plus repeated passes (the API shuffles results) | `__NEXT_DATA__` on the event page, including tickets |
| `tickchak` (live.tickchak.co.il) | slider sections found in the page's `__NEXT_DATA__`; `offset` until `count` | carried in the list items (aggregator: price range + provider link) |
| `selector` (selector.org.il) | `api.selector.org.il/event/list`, `skip`/`take` until `totalCount` | `/event?id=<id>`, including tickets |
| `megatickets` (megatickets.co.il) | public `/events` page (event links; robots.txt disallows `/api/`) | schema.org `MusicEvent` JSON-LD on the event page, including tickets |
| `eventbuzz` (eventbuzz.co.il) | `/events` (single server-rendered page) | `/lp/event/<slug>` HTML; selectors and labels in config |
| `popit` (popit.events) | `/explore/all`; the page data embeds every event (robots.txt disallows `/api`) | none needed; no ticket tiers (links out to the seller, merged by dedup) |

Scrapers read the site's own JSON (or schema.org data) over plain HTTP where it exists; Eventbuzz has none, so its HTML is parsed with selectors kept in config. No browser is needed.

**Checked and not added:** T-TLV (no live site at t-tlv.com), Ticketnet (ticketnet.co.il no longer answers; last working capture 2024), GetIn (public catalogue has no Israeli events right now; the getin.xyz sitemap + schema.org pages would work if that changes).

**Chillz (`chillz`)** sits behind a Vercel Security Checkpoint (a JavaScript bot challenge), so live runs are recorded as `blocked` until Chillz allows Pulse (a Firewall rule on the `PARTYLINKS_SOURCES__CHILLZ__HTTP__HEADERS` key, an IP allowlist, or an API/export). Until then, use the **inbox**: open an event page in your browser, save it with `Ctrl+S` into `data/chillz_inbox/`, and every run imports it with all ticket tiers, artwork, venue and categories. Saved pages keep their original URL, so they never duplicate events collected live later.

## Design

- **Parallel at every level.** Every source runs on its own worker thread with its own asyncio loop, HTTP client, lock and timeout. Inside a source, listing pages, event details and artwork downloads run concurrently, up to `http.concurrency`.
- **Each source is isolated.** Each source has its own module in `src/partylinks/scrapers/` and its own settings model. A slow, failing or blocked site never affects the others, and one bad event is skipped and logged without aborting the run.
- **The database is written in bulk.** Each batch of events is saved with a fixed number of statements: a multi-row `INSERT … ON CONFLICT DO UPDATE` for events, one delete and one multi-row insert for tickets, and bulk insert, update and delete for images. Retention deletes in bulk. SQLite runs in WAL mode so threads can write concurrently.
- **Enrichment runs after scraping** (`enrichment.py`). It maps genres to canonical names, extracts lineups from descriptions, adds local date, weekday, time slot and weekend flags, sets the entry type (`free`, `paid`, `free_and_paid`) and matches the target city. All of it is driven by config.
- **Artwork** URLs are always stored. The images themselves are downloaded and kept either in the database (`event_images.data`, the default) or on disk, and served at `/images/{id}`. The image type is detected from the file bytes.
- **Only current events.** An event belongs to its start date. Events from today (Israel time) onwards are kept; yesterday and earlier are never imported, never shown and purged by an hourly job, with their tickets, artwork and change history (`keep_days_back: 0`). One exception: a party that started last night and is still running (end time ahead, started under `in_progress_max_hours` ago) stays until it ends. Run history older than `keep_runs_days` is purged too. On SQLite the freed pages go back to the OS (incremental auto-vacuum) and the WAL is truncated, so the file actually shrinks.
- **Deals stay current.** Every source scans every hour (staggered minutes). Each scan overwrites an event's prices and replaces all its ticket tiers, and compares them with the previous state; every change is recorded in `deal_changes`: `new`, `price_down`, `price_up`, `free_entry_started`, `free_entry_ended`, `sold_out`, `back_on_sale`, `tiers_changed` (with detail such as `~Entry: 80 -> 60` or `-Free before 00:30`), `delisted`, `relisted`. An event a complete run no longer returns is marked unlisted and hidden. Between scans, time still counts: free entry stops at its recorded end ("free until 23:30" is not free at 23:31), and a tier whose sale window closed shows as `closed` (or `coming_soon` before it opens).
- **Duplicates are merged, not deleted** (`dedup.py`). The same party often appears several times: one page per promoter or entry time, and on several platforms. Every listing is kept (each is a real purchase link with its own prices), but matching listings share a `group_id` and one is primary. Two listings match when they have the same date and either one links to the other, or they have a similar title (dates and emojis ignored), start within an hour of each other and share a location. `/events` returns one event per group with a `listings` count; `/events/{id}` lists the others under `alternatives`; `include_duplicates=true` returns every listing.
- **Every run is recorded** in `scrape_runs` with counts, a status (`success`/`partial`/`failed`) and per-event failures.

```
config/settings.yaml          all settings (no hard-coded URLs/limits in code)
src/partylinks/
  config.py                   YAML + PARTYLINKS_* env overrides
  models.py                   normalized event / ticket-tier model
  scrapers/base.py            per-source transport: retries, parallel pagination
  scrapers/<source>.py        one module per site
  enrichment.py               genres, lineup, time categories, free/paid, city
  media.py                    artwork download + storage
  retention.py                purge finished events
  db.py                       schema + bulk upsert/delete
  service.py                  threaded runner: scrape -> enrich -> save -> artwork
  scheduler.py                one cron job per source + retention job
  api.py                      FastAPI app
  cli.py                      serve / scrape / sources / cleanup
```

## Run

This uses the system Python 3.12+ with no virtualenv.

```bash
pip install -r requirements.txt              # once
set PYTHONPATH=src                           # PowerShell: $env:PYTHONPATH="src"; bash: export PYTHONPATH=src
python -m partylinks scrape                  # all enabled sources in parallel, once
python -m partylinks scrape goout zygo       # specific sources
python -m partylinks serve                   # API on :8000 + scheduler
python -m partylinks cleanup                 # run retention now
python -m pytest
```

Docs are at `http://localhost:8000/docs`.

### Website (React + TypeScript, Hebrew / English)

Pages: parties with filters, `/event/<id>`, `/map` (events with coordinates), `/venues`, `/venue/<name>`, `/artists`, `/artist/<name>`.
Stack: Vite, React Router, TanStack Query, zustand, Leaflet (OpenStreetMap tiles, darkened in CSS).

Windows shortcuts in the repo root:

| File | Does |
|---|---|
| `start.bat` | builds the site if missing, then runs API + scheduler + site on http://localhost:8000/ |
| `build.bat` | `npm install` + `npm run build` into `web/dist` |
| `dev.bat` | API in its own window + Vite dev server with hot reload on http://localhost:5173/ |

Manually:

```bash
cd web && npm install
npm run dev        # http://localhost:5173, proxies the API on :8000
npm run build      # web/dist, then served by `python -m partylinks serve` at http://localhost:8000/
```

### Deploy to Railway

The repo ships a `Dockerfile` (builds the website, then runs API + scheduler + site in one
container) and `railway.json` (Dockerfile build, `/health` check, one replica).

1. Create a project from this repo (Railway picks up `railway.json`).
2. **Add a volume mounted at `/app/data`.** It holds the SQLite DB, the artist dictionary and the
   images; without it every deploy starts empty.
3. Variables:
   - `PARTYLINKS_ADMIN__TOKEN` — a long random string (admin console and mutating API).
   - `PARTYLINKS_SITE__BASE_URL` — only for a custom domain; otherwise the Railway public domain is used.
   - `PARTYLINKS_SECURITY__ALLOWED_HOSTS` — e.g. `["partylinks.co.il","www.partylinks.co.il"]` (optional).
4. Generate a domain (Settings → Networking). The app listens on Railway's `$PORT`.

Keep one replica: SQLite and the scheduler are single-instance. Logs are JSON lines on stdout
(`level`, `logger`, `message`), warnings and errors on stderr, so Railway's log filters work.

### Logs

One format everywhere (app, uvicorn, access lines), configured under `logging:` in
`config/settings.yaml`: INFO to stdout, WARNING+ to stderr, UTF-8, plus a rotating file at
`data/logs/partylinks.log` locally (10 MB × 5). `format: json` gives one JSON object per line.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/events` | filters: `source`, `genre`, `artist`, `venue`, `city`, `in_target_city`, `entry_type` (`free`/`paid`/`free_and_paid`), `free_entry` (free right now: honours "free until" times), `sold_out`, `day_of_week`, `time_slot`, `weekend`, `date`, `date_from`, `date_to`, `min_price`, `max_price`, `q`, `upcoming` (default true); `sort`; `page`, `page_size` |
| GET | `/events/{id}` | full event: tickets with their live status (kind, status, price, fee, total, rounds), images, other listings (`alternatives`) and the latest 20 deal `changes`; id is `<source>:<source_event_id>` or the slug |
| GET | `/changes` | deal-change feed, newest first: `since` (default last 24 hours), `kind` (e.g. `price_down`, `free_entry_ended`), `source`, `limit` |
| GET | `/images/{id}` | stored artwork bytes |
| GET | `/events?include_duplicates=true` | every listing instead of one per real event |
| GET | `/categories` | counts per genre, date, weekday, time slot, entry type, venue, city, lineup, event type, source |
| GET | `/sources` | config, running state, next run, last run |
| POST | `/sources/{name}/run` | trigger a run now (202; 409 if already running) |
| GET | `/runs` | run history; filter by `source`, `status` |
| GET/POST | `/maintenance/retention` | retention settings and next run / purge now |

## SEO, social previews and AI visibility

The site is a React app, but every public page (`/`, `/event/<slug>`, `/venue/<name>`, `/artist/<name>`, `/venues`, `/artists`, `/map`) is served by FastAPI (`seo.py`) with its own:
- `<title>` and description, canonical URL, and hreflang tags for Hebrew and English (`?lang=en`)
- Open Graph and Twitter card tags (the event artwork, or `og-default.png`) and Tel Aviv geo tags
- schema.org JSON-LD: `MusicEvent`/`Event` with offers, place, performers, and Israel-time dates; plus `BreadcrumbList`, `ItemList`, `WebSite`, `Organization`, `MusicVenue` and `Person`
- a plain-HTML copy of the content for crawlers and LLMs that don't run JavaScript

Crawl files are generated live:
- `/sitemap.xml`: every upcoming event, venue and artist, with hreflang alternates
- `/robots.txt`: search engines and AI crawlers allowed, `/admin` blocked
- `/llms.txt`: a site summary, the API, and the next 400 events

Unknown pages return a real 404. Set `site.base_url` to the public domain when you deploy. Favicons and the web manifest are in `web/public/`.

## Admin console

`/admin` is not linked from the site and is marked `noindex`. It has:
- **overview:** events, storage and scheduler at a glance
- **sources:** run now, pause or resume each schedule, last-run results
- **runs:** history with errors
- **config:** edit `settings.yaml` with validation, automatic backups in `config/backups/`, and live apply without a restart
- **maintenance:** purge now, reclaim space, full vacuum, regroup duplicates

All times are shown in Asia/Jerusalem.

Access: set `PARTYLINKS_ADMIN__TOKEN` (environment or `.env`) and sign in with it. Without a token, only requests from the same machine are accepted, and proxied requests never count as local. The same rule guards `POST /sources/{name}/run` and `POST /maintenance/retention`.

## Configuration

Everything lives in `config/settings.yaml`: endpoints, page sizes, cron schedules, HTTP concurrency and retries, run timeout, genre keywords, lineup headers, time slots, weekend days, free-entry keywords, city aliases, media storage and retention. You can override any key from the environment:

```bash
PARTYLINKS_CONFIG=/etc/partylinks.yaml
PARTYLINKS_DATABASE__URL=postgresql+psycopg://user:pass@host/partylinks
PARTYLINKS_SOURCES__ZYGO__ENABLED=false
PARTYLINKS_HTTP__CONCURRENCY=16
PARTYLINKS_MEDIA__STORAGE=filesystem
```

## Adding a source

1. Create `src/partylinks/scrapers/<name>.py` with a `SourceSettings` subclass and a `BaseScraper` subclass. Its `scrape()` yields `ScrapedEvent`s; use `self.paginate(...)` for parallel listing pages and `self.map_isolated(...)` for per-event details.
2. Register it in `scrapers/__init__.py`.
3. Add a `sources.<name>` block to `config/settings.yaml`.
4. Add a parser test with a trimmed fixture under `tests/fixtures/`.
